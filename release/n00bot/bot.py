"""A small, server-scoped Discord slash-command bot."""

import asyncio
import logging
import os
import shutil
import sys
import yt_dlp
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv
from music import extract_audio, youtube_url
from temp_voice import TempVoice
from moderation import Moderation, ModerationError, visible_commands
from deployment import data_directory, validate_runtime, run_bot

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
        self.tree.add_command(Moderation(data_directory() / 'moderation.sqlite3'))
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

    async def setup_hook(self) -> None:
        # Sync once at startup, rather than on every gateway reconnect.
        self.tree.copy_global_to(guild=self.test_guild)
        commands = await self.tree.sync(guild=self.test_guild)
        log.info("Registered %s commands in server %s", len(commands), self.test_guild.id)

    async def on_ready(self) -> None:
        log.info("Online as %s (ID: %s)", self.user, self.user.id)
        log.info("Runtime versions: n00bot=2026.09.07 yt-dlp=%s", yt_dlp.version.__version__)
        guild = self.get_guild(self.test_guild.id)
        log.info("Test server gateway state: cached=%s available=%s bot_member=%s",
                 guild is not None, guild is not None and not guild.unavailable,
                 guild is not None and guild.me is not None)
        if guild is not None:
            await self.temp_voice.recover(guild)

    async def on_voice_state_update(self, member, before, after):
        if member.guild.id != self.test_guild.id:
            return
        try:
            await self.temp_voice.update(member, before, after)
        except (discord.HTTPException, OSError):
            log.exception('Temporary voice channel operation failed')

    async def on_guild_channel_delete(self, channel):
        if channel.guild.id == self.test_guild.id and channel.id in self.temp_voice.rooms:
            async with self.temp_voice.lock:
                self.temp_voice.rooms.discard(channel.id)
                self.temp_voice.labels.pop(str(channel.id), None)
                await self.temp_voice.renumber(channel.guild)

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
        f"Pong! Gateway latency: {round(interaction.client.latency * 1000)} ms."
    )


@app_commands.command(name="help", description="Show the available commands.")
async def help_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    embed = discord.Embed(title='Available commands', description=(
        "`/ping` — Check whether I'm online and see gateway latency.\n"
        "`/help` — Show this command list.\n"
        "`/join [channel]` — Join your voice channel, or select one explicitly.\n"
        "`/leave` — Disconnect from your voice channel.\n"
        "`/play url` — Play a YouTube video after /join.\n"
        "`/stop` — Stop playback."
    ))
    if interaction.guild is not None:
        member = await interaction.guild.fetch_member(interaction.user.id)
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
        await voice.disconnect(force=True)
        await interaction.followup.send("Disconnected from voice.", ephemeral=True)


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


@app_commands.command(name="play", description="Play audio from a YouTube video link.")
@app_commands.guild_only()
async def play(interaction: discord.Interaction, url: str) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    if interaction.guild is None:
        await interaction.followup.send("Use this command in a server.", ephemeral=True)
        return
    try:
        url = youtube_url(url)
    except ValueError as error:
        await interaction.followup.send(str(error), ephemeral=True)
        return
    if not shutil.which('ffmpeg'):
        await interaction.followup.send("Install FFmpeg on the bot's computer, then try again.", ephemeral=True)
        return
    lock = interaction.client.voice_locks.setdefault(interaction.guild.id, asyncio.Lock())
    if lock.locked():
        await interaction.followup.send("A voice operation is in progress. Try again shortly.", ephemeral=True)
        return
    async with lock:
        voice = await music_access(interaction)
        if voice is None:
            return
        if voice.is_playing() or voice.is_paused():
            await interaction.followup.send("Audio is already playing. Use /stop before starting another video.", ephemeral=True)
            return
        permissions = voice.channel.permissions_for(interaction.guild.me)
        if not permissions.speak:
            await interaction.followup.send("I need Speak permission in this voice channel.", ephemeral=True)
            return
        source = None
        try:
            result = await extract_audio(url)
            stream, title = result[:2]
            headers = result[2] if len(result) > 2 else {}
            if await music_access(interaction) is not voice:
                return
            source = discord.FFmpegOpusAudio(
                stream, before_options=('-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -rw_timeout 15000000' + ''.join(f' -headers \"{k}: {v}\\r\\n\"' for k, v in headers.items())),
                options='-vn',
            )
            loop = asyncio.get_running_loop()
            def finished(error):
                if error:
                    log.error("Audio playback failed: %s", type(error).__name__)
                    asyncio.run_coroutine_threadsafe(
                        interaction.followup.send("Audio playback failed. Try another video.", ephemeral=True), loop)
            voice.play(source, after=finished)
            source = None  # The audio player now owns cleanup.
        except (ValueError, OSError, discord.DiscordException, asyncio.TimeoutError) as error:
            message = str(error) if isinstance(error, ValueError) else "Couldn't start audio. Check FFmpeg and try another video."
            await interaction.followup.send(message, ephemeral=True)
            return
        finally:
            if source is not None:
                source.cleanup()
        await interaction.followup.send(f"Now playing: **{discord.utils.escape_markdown(title[:200])}**", ephemeral=True)


@app_commands.command(name="stop", description="Stop audio playback without leaving voice.")
@app_commands.guild_only()
async def stop(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    voice = await music_access(interaction)
    if voice is None:
        return
    lock = interaction.client.voice_locks.setdefault(interaction.guild.id, asyncio.Lock())
    async with lock:
        voice.stop()
        await interaction.followup.send("Playback stopped.", ephemeral=True)


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
