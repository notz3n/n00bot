import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from bot import N00Bot, join, leave


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(os.environ, {'DATA_DIR': directory.name})
        env.start()
        self.addCleanup(env.stop)

    def interaction(self):
        channel = Mock(spec=discord.VoiceChannel)
        channel.id = 42
        channel.mention = "<#42>"
        channel.connect = AsyncMock()
        channel.permissions_for.return_value = SimpleNamespace(view_channel=True, connect=True)
        return SimpleNamespace(
            guild=SimpleNamespace(id=1, me=Mock(), unavailable=False, voice_client=None),
            user=SimpleNamespace(id=123, voice=SimpleNamespace(channel=channel), fetch_voice=AsyncMock(), guild_permissions=SimpleNamespace(move_members=False)),
            client=SimpleNamespace(voice_locks={}, players={}),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_registration(self):
        client = N00Bot(1)
        self.assertTrue(client.intents.voice_states)
        self.assertEqual({c.name for c in client.tree.get_commands()}, {'ping', 'help', 'join', 'leave', 'play', 'stop', 'mod', 'room', 'queue', 'skip', 'nowplaying', 'health'})
        await client.close()

    async def test_join(self):
        interaction = self.interaction()
        await join.callback(interaction)
        interaction.user.voice.channel.connect.assert_awaited_once_with(timeout=20.0, reconnect=False, self_deaf=True)
        interaction.response.defer.assert_awaited_once()

    async def test_no_voice(self):
        interaction = self.interaction()
        interaction.user.voice = None
        interaction.user.fetch_voice.side_effect = discord.NotFound(SimpleNamespace(status=404, reason='Not Found'), 'Not connected')
        await join.callback(interaction)
        self.assertIn('channel option', interaction.followup.send.call_args.args[0])

    async def test_explicit_channel_without_voice_state(self):
        interaction = self.interaction()
        channel = interaction.user.voice.channel
        channel.guild = interaction.guild
        interaction.user.voice = None
        await join.callback(interaction, channel)
        channel.connect.assert_awaited_once()
        interaction.user.fetch_voice.assert_not_awaited()

    async def test_explicit_channel_requires_user_permissions(self):
        interaction = self.interaction()
        channel = interaction.user.voice.channel
        channel.guild = interaction.guild
        channel.permissions_for.return_value.connect = False
        await join.callback(interaction, channel)
        channel.connect.assert_not_awaited()

    async def test_uncached_channel_option(self):
        interaction = self.interaction()
        channel = interaction.user.voice.channel
        channel.guild = interaction.guild
        option = Mock(spec=discord.app_commands.AppCommandChannel)
        option.resolve.return_value = None
        option.fetch = AsyncMock(return_value=channel)
        await join.callback(interaction, option)
        option.fetch.assert_awaited_once()
        channel.connect.assert_awaited_once()

    async def test_missing_bot_membership(self):
        interaction = self.interaction()
        interaction.guild.me = None
        await join.callback(interaction)
        self.assertIn('Guild Install', interaction.followup.send.call_args.args[0])

    async def test_channel_transform_accepts_uncached_reference(self):
        from bot import VoiceChannelOption
        option = Mock(spec=discord.app_commands.AppCommandChannel)
        option.resolve.return_value = None
        self.assertIs(await VoiceChannelOption().transform(self.interaction(), option), option)

    async def test_moderator_can_disconnect_without_voice_detection(self):
        interaction = self.interaction()
        interaction.user.guild_permissions.move_members = True
        interaction.guild.voice_client = Mock(channel=SimpleNamespace(id=99), disconnect=AsyncMock())
        await leave.callback(interaction)
        interaction.guild.voice_client.disconnect.assert_awaited_once_with(force=True)

    async def test_missing_cache_fetches_voice(self):
        interaction = self.interaction()
        state = interaction.user.voice
        interaction.user.voice = None
        interaction.user.fetch_voice.return_value = state
        await join.callback(interaction)
        interaction.user.fetch_voice.assert_awaited_once()
        state.channel.connect.assert_awaited_once()

    async def test_stage_has_specific_message(self):
        interaction = self.interaction()
        interaction.user.voice.channel = Mock(spec=discord.StageChannel)
        await join.callback(interaction)
        self.assertIn('Stage channel', interaction.followup.send.call_args.args[0])

    async def test_unresolved_channel(self):
        interaction = self.interaction()
        interaction.user.voice.channel = None
        interaction.user.fetch_voice.return_value = SimpleNamespace(channel=None)
        await join.callback(interaction)
        self.assertIn("can't see", interaction.followup.send.call_args.args[0])

    async def test_voice_lookup_failure(self):
        interaction = self.interaction()
        interaction.user.voice = None
        interaction.user.fetch_voice.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'Denied')
        await join.callback(interaction)
        self.assertIn("couldn't check", interaction.followup.send.call_args.args[0])

    async def test_permissions(self):
        interaction = self.interaction()
        interaction.user.voice.channel.permissions_for.return_value.connect = False
        await join.callback(interaction)
        interaction.user.voice.channel.connect.assert_not_awaited()

    async def test_already_connected(self):
        for channel_id in (42, 99):
            interaction = self.interaction()
            interaction.guild.voice_client = Mock(channel=SimpleNamespace(id=channel_id))
            await join.callback(interaction)
            interaction.user.voice.channel.connect.assert_not_awaited()

    async def test_timeout_cleanup(self):
        interaction = self.interaction()
        stale = Mock(disconnect=AsyncMock())
        async def fail(**kwargs):
            interaction.guild.voice_client = stale
            raise asyncio.TimeoutError
        interaction.user.voice.channel.connect.side_effect = fail
        await join.callback(interaction)
        stale.disconnect.assert_awaited_once_with(force=True)
        self.assertIn("couldn't connect", interaction.followup.send.call_args.args[0])

    async def test_leave(self):
        interaction = self.interaction()
        voice = Mock(channel=SimpleNamespace(id=42), disconnect=AsyncMock())
        interaction.guild.voice_client = voice
        await leave.callback(interaction)
        voice.disconnect.assert_awaited_once_with(force=True)

    async def test_leave_other_channel(self):
        interaction = self.interaction()
        voice = Mock(channel=SimpleNamespace(id=99), disconnect=AsyncMock())
        interaction.guild.voice_client = voice
        await leave.callback(interaction)
        voice.disconnect.assert_not_awaited()

    async def test_leave_not_connected(self):
        interaction = self.interaction()
        await leave.callback(interaction)
        self.assertIn('not in a voice', interaction.followup.send.call_args.args[0])

    async def test_dm(self):
        for command in (join, leave):
            interaction = self.interaction()
            interaction.guild = None
            await command.callback(interaction)
            interaction.response.send_message.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
