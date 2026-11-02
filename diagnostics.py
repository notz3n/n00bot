"""Private operational checks without exposing configuration or log contents."""

import asyncio
from collections import deque
from datetime import datetime, timezone
import logging
import math
import tempfile

import discord
from discord import app_commands
from deployment import data_directory
from moderation import ModerationError
from version import get_version


class RecentFailures(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.events = deque(maxlen=5)

    def emit(self, record):
        area = {'bot.temp_voice': 'Temporary rooms', 'bot.music': 'Music',
                'bot.moderation': 'Moderation'}.get(record.name, 'Bot / command')
        timestamp = datetime.fromtimestamp(record.created, timezone.utc).strftime('%H:%M:%S UTC')
        kind = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else record.levelname
        self.events.append(f'{timestamp} — {area}: {kind}')


def storage_check(store):
    try:
        with tempfile.TemporaryFile(dir=data_directory()) as probe:
            probe.write(b'health check')
            probe.flush()
        rows, _, _ = store.query('PRAGMA quick_check')
        return 'Writable; moderation database OK.' if rows == [('ok',)] else 'Database integrity check failed.'
    except Exception:
        return 'Storage check failed. Inspect host permissions and bot logs.'


@app_commands.command(name='health', description='Staff-only bot, voice, storage and permission diagnostics.')
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
async def health(i: discord.Interaction):
    if i.guild is None:
        raise ModerationError('Use this command in a server.')
    await i.response.defer(ephemeral=True, thinking=True)
    actor = await i.guild.fetch_member(i.user.id)
    if not actor.guild_permissions.manage_guild:
        raise ModerationError('You need Manage Server permission.')
    bot = await i.guild.fetch_member(i.guild.me.id)
    client = i.client
    embed = discord.Embed(title='n00bot health', description=get_version())
    latency = client.latency
    embed.add_field(name='Gateway', value=f'{round(latency * 1000)} ms' if math.isfinite(latency) else 'Latency unavailable')
    voice = i.guild.voice_client
    embed.add_field(name='Voice', value=f'Connected: <#{voice.channel.id}>' if voice and voice.is_connected() else 'Disconnected')
    player = client.players.get(i.guild.id)
    embed.add_field(name='Music', value=f'{player.current.state if player and player.current else "Idle"}; {len(player.pending) if player else 0} waiting')
    embed.add_field(name='Storage', value=await asyncio.to_thread(storage_check, client.moderation.store), inline=False)
    manager = client.rooms_for(i.guild.id)
    embed.add_field(name='Temporary rooms', value=f'This server: {len(manager.lobby_ids)} parent(s), {len(manager.rooms)} tracked room(s); recovery every 30 seconds; alone timeout {client.alone_timeout}s', inline=False)
    checks = []
    for parent_id in sorted(manager.lobby_ids):
        lobby = i.guild.get_channel(parent_id)
        if lobby is None:
            checks.append(f'Parent {parent_id}: configured channel is unavailable.')
        else:
            permissions = lobby.permissions_for(bot)
            needed = ['view_channel', 'connect', 'manage_channels', 'move_members', 'manage_roles']
            if manager.status_template:
                needed.append('set_voice_channel_status')
            missing = [name.replace('_', ' ') for name in needed if not getattr(permissions, name, False)]
            checks.append(f'Parent <#{parent_id}>: ' + (', '.join(missing) + ' missing.' if missing else 'required permissions present.'))
    if not manager.lobby_ids:
        checks.append('No parent lobbies. Use /room setparent channel to add one.')
    if voice and voice.is_connected():
        permissions = voice.channel.permissions_for(bot)
        missing = [name for name in ('view_channel', 'connect', 'speak') if not getattr(permissions, name)]
        checks.append('Voice: ' + (', '.join(missing) + ' missing.' if missing else 'required permissions present.'))
    missing = [name.replace('_', ' ') for name in ('moderate_members', 'kick_members', 'ban_members', 'manage_messages', 'manage_roles') if not getattr(bot.guild_permissions, name)]
    checks.append('Moderation: ' + (', '.join(missing) + ' missing.' if missing else 'server permissions present.') + ' Target hierarchy and channel overrides still apply.')
    # Keep health within embed limits even with hundreds of configured parents.
    details = '\n'.join(checks)
    if len(details) > 1024:
        details = details[:930].rsplit('\n', 1)[0] + '\n… Additional parents omitted; use /room parents for the list.'
    embed.add_field(name='Bot permissions', value=details, inline=False)
    embed.add_field(name='Recent warnings / failures (this process)', value='\n'.join(client.failures.events) or 'None recorded.', inline=False)
    await i.followup.send(embed=embed, ephemeral=True)
