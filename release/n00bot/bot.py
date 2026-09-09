"""A small, server-scoped Discord slash-command bot."""

import asyncio
import logging
import os
import shutil
import sys
import time
import yt_dlp
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv
from music import youtube_url
from player import MusicPlayer
from room_controls import RoomControls
from diagnostics import health, RecentFailures
from temp_voice import TempVoice
from moderation import Moderation, ModerationError, visible_commands
from deployment import data_directory, validate_runtime, run_bot
from version import get_version

log = logging.getLogger("bot")


def read_config() -> tuple[str, int]:
    load_dotenv(Path(__file__).with_name(".env"))
    token = os.getenv("DISCORD_TOKEN", "").strip()
    guild_id = os.getenv("DISCORD_GUILD_ID", "").strip()
    if not token or token == "your_bot_token_here":
        raise ValueError("Set DISCORD_TOKEN in .env to your bot token.")
    if not guild_id.isascii() or not guild_id.isdecimal() or not 0 < int(guild_id) < 2**64:
        raise ValueError("Set DISCORD_GUILD_ID in .env to your numeric test server ID.")
    return token, int(guild_id)


class N00Bot(discord.Client):
    def __init__(self, guild_id: int):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.test_guild = discord.Object(id=guild_id)
        self.tree = app_commands.CommandTree(self)
        self.tree.add_command(ping)
        self.tree.add_command(help_command)
        self.tree.add_command(join)
        self.tree.add_command(leave)
        self.tree.add_command(play)
        self.tree.add_command(stop)
        self.moderation = Moderation(data_directory() / 'moderation.sqlite3')
        self.tree.add_command(self.moderation)
        for command in (queue, skip, nowplaying, health):
            self.tree.add_command(command)
        self.players = {}
        self.maintenance_task = None
        self.recovery_task = None
        self.alone_since = {}
        self.alone_timeout = int(os.getenv('VOICE_ALONE_TIMEOUT', '120'))
        if not 0 <= self.alone_timeout <= 86400:
            raise ValueError('VOICE_ALONE_TIMEOUT must be between 0 and 86400 seconds.')
        self.failures = RecentFailures()
        self.voice_locks: dict[int, asyncio.Lock] = {}
        self.tree.on_error = self.on_command_error
        lobby_id = os.getenv('TEMP_VOICE_LOBBY_ID', '').strip()
        if lobby_id and (not lobby_id.isascii() or not lobby_id.isdecimal() or not 0 < int(lobby_id) < 2**64):
            raise ValueError('TEMP_VOICE_LOBBY_ID must be a numeric channel ID or blank.')
        self.temp_voice = TempVoice(
            int(lobby_id) if lobby_id else None,
            os.getenv('TEMP_VOICE_NAME', "{user}'s room"),
            data_directory() / f'temp-voice-{guild_id}.json',
            status_template=os.getenv('TEMP_VOICE_STATUS', ''),
        )

        self.tree.add_command(RoomControls(self.temp_voice))
        log.addHandler(self.failures)

    async def close(self):
        if self.maintenance_task is not None:
            self.maintenance_task.cancel()
            await asyncio.gather(self.maintenance_task, return_exceptions=True)
        if self.recovery_task is not None:
            self.recovery_task.cancel()
            await asyncio.gather(self.recovery_task, return_exceptions=True)
        for player in self.players.values():
            await player.stop()
        log.removeHandler(self.failures)
        await super().close()

    async def maintain_once(self, guild, now=None):
        if guild.unavailable or guild.me is None:
            self.alone_since.pop(guild.id, None)
            return
        now = time.monotonic() if now is None else now
        async with self.voice_locks.setdefault(guild.id, asyncio.Lock()):
            voice = guild.voice_client
            player = self.players.get(guild.id)
            if player and (voice is not player.voice or not voice.is_connected() or voice.channel.id != player.channel_id):
                await player.stop()
                self.players.pop(guild.id, None)
            if not self.alone_timeout or voice is None or not voice.is_connected():
                self.alone_since.pop(guild.id, None)
            elif set(voice.channel.voice_states) - {guild.me.id}:
                self.alone_since.pop(guild.id, None)
            else:
                key = voice.channel.id
                previous = self.alone_since.get(guild.id)
                if previous is None or previous[0] != key:
                    self.alone_since[guild.id] = (key, now)
                elif now - previous[1] >= self.alone_timeout:
                    if player:
                        await player.stop()
                    await voice.disconnect(force=True)
                    self.alone_since.pop(guild.id, None)
                    log.info('Disconnected after being alone for %s seconds', self.alone_timeout)
        if now >= self.temp_voice.reconcile_at and (self.recovery_task is None or self.recovery_task.done()):
            # Discord may delay channel edits for rate limits. Recovery must not
            # prevent the independent alone timer from disconnecting voice.
            self.recovery_task = asyncio.create_task(self.recover_rooms(guild), name='room-recovery')

    async def recover_rooms(self, guild):
        try:
            await self.temp_voice.recover(guild)
        except Exception:
            log.exception('Room recovery failed; will retry')
            self.temp_voice.reconcile_at = time.monotonic() + 30

    async def maintain(self):
        while not self.is_closed():
            guild = self.get_guild(self.test_guild.id)
            try:
                if self.is_ready() and guild is not None:
                    await self.maintain_once(guild)
                else:
                    self.alone_since.clear()
            except Exception:
                log.exception('Voice maintenance failed; will retry')
            await asyncio.sleep(5)

    async def setup_hook(self) -> None:
        # Sync once at startup, rather than on every gateway reconnect.
        self.tree.copy_global_to(guild=self.test_guild)
        commands = await self.tree.sync(guild=self.test_guild)
        log.info("Registered %s commands in server %s", len(commands), self.test_guild.id)

    async def on_ready(self) -> None:
        log.info("Online as %s (ID: %s)", self.user, self.user.id)
        log.info("Runtime versions: n00bot=%s yt-dlp=%s", get_version(), yt_dlp.version.__version__)
        guild = self.get_guild(self.test_guild.id)
        log.info("Test server gateway state: cached=%s available=%s bot_member=%s",
                 guild is not None, guild is not None and not guild.unavailable,
                 guild is not None and guild.me is not None)
        if self.maintenance_task is None or self.maintenance_task.done():
            self.maintenance_task = asyncio.create_task(self.maintain(), name="voice-maintenance")

    async def on_voice_state_update(self, member, before, after):
        if member.guild.id != self.test_guild.id or member.guild.me is None:
            return
        if before.channel != after.channel:
            voice = member.guild.voice_client
            if member.id == member.guild.me.id or (voice and voice.channel in (before.channel, after.channel)):
                self.alone_since.pop(member.guild.id, None)
            if member.id == member.guild.me.id and before.channel is not None:
                async with self.voice_locks.setdefault(member.guild.id, asyncio.Lock()):
                    player = self.players.get(member.guild.id)
                    current = member.guild.voice_client
                    # Gateway events can wait behind a new /play connection.
                    # Do not cancel a replacement session for an older event.
                    if player and (current is not player.voice or not current.is_connected() or current.channel.id != player.channel_id):
                        self.players.pop(member.guild.id, None)
                        await player.stop()
        try:
            await self.temp_voice.update(member, before, after)
        except (discord.HTTPException, OSError):
            log.exception('Temporary voice channel operation failed')

    async def on_guild_channel_delete(self, channel):
        if channel.guild.id == self.test_guild.id and channel.id in self.temp_voice.rooms:
            async with self.temp_voice.lock:
                self.temp_voice.forget(channel.id)
                self.temp_voice.save()
                self.temp_voice.schedule_reconcile()

    async def on_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        log.error("Slash command failed", exc_info=(type(error), error, error.__traceback__))
        message = "Something went wrong running that command. Please try again."
        original = getattr(error, 'original', error)
        if isinstance(original, ModerationError):
            message = str(original)
        elif isinstance(original, discord.Forbidden):
            message = 'Discord denied this action. Check my permissions and role position.'
        elif isinstance(original, discord.NotFound):
            message = 'Discord could not find that member, ban, role, or channel. Refresh your selection and try again.'
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


@app_commands.command(name="ping", description="Check whether the bot is online.")
async def ping(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        f"Pong! Gateway latency: {round(interaction.client.latency * 1000)} ms. n00bot {get_version()}"
    )


@app_commands.command(name="help", description="Show the available commands.")
async def help_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    embed = discord.Embed(title='Available commands', description=(
        "`/ping` — Check whether I'm online and see gateway latency.\n"
        "`/help` — Show this command list.\n"
        "`/join [channel]` — Join your voice channel, or select one explicitly.\n"
        "`/leave` — Disconnect from your voice channel.\n"
        "`/play url` — Join your channel and add a YouTube video to the queue.\n"
        "`/stop` — Stop playback and clear the queue.\n"
        "`/queue`, `/skip`, `/nowplaying` — Inspect or control music.\n"
        "`/room rename|limit|lock|unlock|transfer|claim` — Manage your temporary room."
    ))
    if interaction.guild is not None:
        member = await interaction.guild.fetch_member(interaction.user.id)
        if member.guild_permissions.manage_guild:
            embed.add_field(name='/health', value='Private bot diagnostics.', inline=False)
        group = interaction.client.tree.get_command('mod')
        for command in visible_commands(group, member, interaction.channel):
            embed.add_field(name=f'/mod {command.name}', value=command.description, inline=False)
    embed.set_footer(text='Voice/music commands need no staff role. Playback controls require the same voice channel. Moderation also checks bot permissions and role hierarchy. Server command overrides may restrict access.')
    await interaction.followup.send(embed=embed, ephemeral=True)


async def resolve_voice_state(interaction: discord.Interaction):
    """Use the gateway cache, with an API fallback when it has no channel."""
    state = interaction.user.voice
    if state is None or state.channel is None:
        try:
            state = await interaction.user.fetch_voice()
        except discord.NotFound as error:
            log.warning(
                "Voice lookup unavailable: guild=%s user=%s status=%s code=%s",
                interaction.guild.id, interaction.user.id, error.status, error.code,
            )
            return None
    return state


class VoiceChannelOption(app_commands.Transformer):
    """Keep the channel reference until the command can defer and fetch it."""

    @property
    def type(self):
        return discord.AppCommandOptionType.channel

    @property
    def channel_types(self):
        return [discord.ChannelType.voice]

    async def transform(self, interaction, value):
        return value


@app_commands.command(name="join", description="Join your current voice channel.")
@app_commands.guild_only()
@app_commands.describe(channel="Optional: select a voice channel if automatic detection fails.")
async def join(interaction: discord.Interaction, channel: app_commands.Transform[app_commands.AppCommandChannel, VoiceChannelOption] = None) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    if interaction.guild.unavailable or interaction.guild.me is None:
        await interaction.followup.send(
            "I don't have a ready bot connection to this server. In the Developer Portal, "
            "use Guild Install with both bot and applications.commands scopes, then add me to this server. "
            "If I'm already a server member, wait a few seconds and restart me if this persists.",
            ephemeral=True,
        )
        return
    if isinstance(channel, app_commands.AppCommandChannel):
        try:
            channel = channel.resolve() or await channel.fetch()
        except discord.HTTPException:
            await interaction.followup.send("I couldn't access that channel. Check my View Channel and Connect permissions.", ephemeral=True)
            return
    if channel is not None and not isinstance(channel, discord.VoiceChannel):
        await interaction.followup.send("Select a regular voice channel.", ephemeral=True)
        return
    lock = interaction.client.voice_locks.setdefault(interaction.guild.id, asyncio.Lock())
    async with lock:
        if channel is None:
            try:
                state = await resolve_voice_state(interaction)
            except discord.HTTPException:
                await interaction.followup.send("I couldn't check your voice status with Discord. Try /join with the channel option selected.", ephemeral=True)
                return
            if state is None:
                await interaction.followup.send("I couldn't detect your voice channel. Use /join and select your channel in the channel option.", ephemeral=True)
                return
            if state.channel is None:
                await interaction.followup.send("I can't see your voice channel. Check my View Channel permission, then try /join with the channel option.", ephemeral=True)
                return
            if not isinstance(state.channel, discord.VoiceChannel):
                await interaction.followup.send("You're in a Stage channel. /join currently supports regular voice channels only.", ephemeral=True)
                return
            channel = state.channel
        else:
            if channel.guild.id != interaction.guild.id:
                await interaction.followup.send("Select a voice channel in this server.", ephemeral=True)
                return
            user_permissions = channel.permissions_for(interaction.user)
            if not user_permissions.view_channel or not user_permissions.connect:
                await interaction.followup.send("You need View Channel and Connect permissions in the selected channel.", ephemeral=True)
                return
        permissions = channel.permissions_for(interaction.guild.me)
        if not permissions.view_channel or not permissions.connect:
            await interaction.followup.send("I need View Channel and Connect permissions in your voice channel.", ephemeral=True)
            return
        voice = interaction.guild.voice_client
        if voice is not None and voice.is_connected():
            message = "I'm already in your voice channel." if voice.channel.id == channel.id else "I'm in another voice channel. Use /leave there before moving me."
            await interaction.followup.send(message, ephemeral=True)
            return
        try:
            if voice is not None:
                await voice.disconnect(force=True)
            await channel.connect(timeout=20.0, reconnect=False, self_deaf=True)
        except (asyncio.TimeoutError, discord.DiscordException) as error:
            log.warning("Voice connection failed: %s", type(error).__name__)
            voice = interaction.guild.voice_client
            if voice is not None:
                await voice.disconnect(force=True)
            await interaction.followup.send("I couldn't connect. Check my channel permissions and try again.", ephemeral=True)
            return
        await interaction.followup.send(f"Joined {channel.mention}.", ephemeral=True)


@app_commands.command(name="leave", description="Disconnect the bot from your voice channel.")
@app_commands.guild_only()
async def leave(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Use this command in a server.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    lock = interaction.client.voice_locks.setdefault(interaction.guild.id, asyncio.Lock())
    async with lock:
        voice = interaction.guild.voice_client
        if voice is None:
            await interaction.followup.send("I'm not in a voice channel.", ephemeral=True)
            return
        state = await resolve_voice_state(interaction)
        same_channel = state is not None and state.channel is not None and state.channel.id == voice.channel.id
        if not same_channel and not interaction.user.guild_permissions.move_members:
            await interaction.followup.send("Join my voice channel before using /leave.", ephemeral=True)
            return
        player = interaction.client.players.pop(interaction.guild.id, None)
        if player:
            await player.stop()
        await voice.disconnect(force=True)
        await interaction.followup.send("Disconnected from voice; queue cleared.", ephemeral=True)


async def music_access(interaction: discord.Interaction):
    voice = interaction.guild.voice_client if interaction.guild else None
    if voice is None or not voice.is_connected():
        await interaction.followup.send("Use /join first, then /play.", ephemeral=True)
        return None
    state = await resolve_voice_state(interaction)
    if state is None or state.channel is None or state.channel.id != voice.channel.id:
        await interaction.followup.send("Join my voice channel to control playback.", ephemeral=True)
        return None
    return voice


@app_commands.command(name="play", description="Join your voice channel and queue a YouTube video.")
@app_commands.guild_only()
async def play(interaction: discord.Interaction, url: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if interaction.guild is None or interaction.guild.me is None or interaction.guild.unavailable:
        raise ModerationError('Use this command in an available server.')
    try:
        url = youtube_url(url)
    except ValueError as error:
        raise ModerationError(str(error)) from error
    if not shutil.which('ffmpeg'):
        raise ModerationError('Install FFmpeg on the bot’s computer, then try again.')
    lock = interaction.client.voice_locks.setdefault(interaction.guild.id, asyncio.Lock())
    async with lock:
        state = await resolve_voice_state(interaction)
        if state is None or not isinstance(state.channel, discord.VoiceChannel):
            raise ModerationError('Join a regular voice channel first.')
        channel = state.channel
        voice = interaction.guild.voice_client
        if voice is not None and voice.is_connected() and voice.channel.id != channel.id:
            raise ModerationError('Join my voice channel to queue music. Use /leave there before moving me.')
        permissions = channel.permissions_for(interaction.guild.me)
        user_permissions = channel.permissions_for(interaction.user)
        if not (permissions.view_channel and permissions.connect and permissions.speak and user_permissions.view_channel and user_permissions.connect):
            raise ModerationError('You need View Channel and Connect; I also need Speak in this channel.')
        if voice is None or not voice.is_connected():
            if voice is not None:
                await voice.disconnect(force=True)
            try:
                voice = await channel.connect(timeout=20.0, reconnect=False, self_deaf=True)
            except (asyncio.TimeoutError, discord.DiscordException):
                stale = interaction.guild.voice_client
                if stale is not None:
                    await stale.disconnect(force=True)
                raise ModerationError('Could not connect to voice. Check permissions and try again.')
            # A member can leave during the connection handshake.
            state = await resolve_voice_state(interaction)
            if state is None or state.channel is None or state.channel.id != voice.channel.id:
                await voice.disconnect(force=True)
                raise ModerationError('You left the voice channel while I was connecting.')
        player = interaction.client.players.get(interaction.guild.id)
        if player is None or player.voice is not voice or player.channel_id != voice.channel.id:
            if player:
                await player.stop()
            player = MusicPlayer(voice, lock)
            interaction.client.players[interaction.guild.id] = player
        try:
            position = player.enqueue(url, interaction.user.id)
        except ValueError as error:
            raise ModerationError(str(error)) from error
        await interaction.followup.send(f'Added at position {position}. Audio downloads when its turn starts; use /nowplaying or /queue for status.', ephemeral=True)


@app_commands.command(name="stop", description="Stop playback and clear all queued tracks.")
@app_commands.guild_only()
async def stop(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if interaction.guild is None:
        raise ModerationError('Use this command in a server.')
    async with interaction.client.voice_locks.setdefault(interaction.guild.id, asyncio.Lock()):
        voice = await music_access(interaction)
        if voice is None:
            return
        player = interaction.client.players.get(interaction.guild.id)
        if player:
            await player.stop()
        else:
            voice.stop()
        await interaction.followup.send('Playback stopped; queue cleared.', ephemeral=True)


@app_commands.command(name='skip', description='Skip the current track or download and play the next.')
@app_commands.guild_only()
async def skip(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    if interaction.guild is None:
        raise ModerationError('Use this command in a server.')
    async with interaction.client.voice_locks.setdefault(interaction.guild.id, asyncio.Lock()):
        if await music_access(interaction) is None:
            return
        player = interaction.client.players.get(interaction.guild.id)
        if player is None or (player.current is None and not player.pending):
            raise ModerationError('There is no track to skip.')
        # The worker might not have started since the latest enqueue.
        if player.current is None and player.pending:
            player.pending.popleft()
        await player.stop(clear=False)
        await interaction.followup.send('Skipped. The next queued track will start downloading.', ephemeral=True)


async def show_music(interaction, *, show_queue):
    await interaction.response.defer(ephemeral=True, thinking=True)
    if await music_access(interaction) is None:
        return
    player = interaction.client.players.get(interaction.guild.id)
    embed = discord.Embed(title='Music queue' if show_queue else 'Now playing')
    current = player.current if player else None
    if current:
        title = discord.utils.escape_markdown(current.title[:200] or current.url)
        embed.description = f'{current.state.title()}: {title}\nRequested by <@{current.requester}>'
    else:
        embed.description = 'No track is playing.'
    if show_queue and player:
        for number, track in enumerate(player.pending, 1):
            embed.add_field(name=f'{number}. Waiting', value=f'{track.url} — <@{track.requester}>', inline=False)
    if player and player.last_error:
        embed.set_footer(text='Last music failure: ' + player.last_error)
    await interaction.followup.send(embed=embed, ephemeral=True)


@app_commands.command(name='queue', description='Show current and waiting tracks.')
@app_commands.guild_only()
async def queue(interaction: discord.Interaction):
    await show_music(interaction, show_queue=True)


@app_commands.command(name='nowplaying', description='Show the current track or download status.')
@app_commands.guild_only()
async def nowplaying(interaction: discord.Interaction):
    await show_music(interaction, show_queue=False)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        token, guild_id = read_config()
        validate_runtime()
        bot = N00Bot(guild_id)
    except (ValueError, OSError) as error:
        raise SystemExit(str(error)) from None
    if '--check' in sys.argv:
        log.info('Configuration, runtime dependencies, and writable data directory checked. No Discord connection made.')
        return
    try:
        asyncio.run(run_bot(bot, token))
    except discord.LoginFailure:
        raise SystemExit("Discord rejected the token. Update DISCORD_TOKEN in .env.") from None
    except discord.HTTPException as error:
        raise SystemExit(
            f"Discord API request failed (HTTP {error.status}). "
            "Check the server ID and make sure the bot is installed in that server."
        ) from None


if __name__ == "__main__":
    main()
