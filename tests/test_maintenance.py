import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bot import N00Bot


class MaintenanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        env = patch.dict(os.environ, {'DATA_DIR':self.directory.name, 'VOICE_ALONE_TIMEOUT':'120'})
        env.start()
        self.addCleanup(env.stop)
        self.client = N00Bot(1)
        self.addAsyncCleanup(self.client.close)
        self.channel = SimpleNamespace(id=20, voice_states={2:Mock()})
        self.voice = Mock(channel=self.channel, disconnect=AsyncMock())
        self.voice.is_connected.return_value = True
        self.guild = SimpleNamespace(id=1, me=SimpleNamespace(id=2), unavailable=False, voice_client=self.voice)
        self.client.temp_voice.recover = AsyncMock()
        self.player = SimpleNamespace(voice=self.voice, channel_id=20, stop=AsyncMock())
        self.client.players[1] = self.player

    async def test_alone_timeout_stops_music_then_disconnects(self):
        await self.client.maintain_once(self.guild, 0)
        await self.client.maintain_once(self.guild, 119)
        self.voice.disconnect.assert_not_awaited()
        await self.client.maintain_once(self.guild, 120)
        self.player.stop.assert_awaited_once()
        self.voice.disconnect.assert_awaited_once_with(force=True)

    async def test_returning_occupant_resets_timer(self):
        await self.client.maintain_once(self.guild, 0)
        self.channel.voice_states[3] = Mock()
        await self.client.maintain_once(self.guild, 119)
        self.channel.voice_states.pop(3)
        await self.client.maintain_once(self.guild, 120)
        await self.client.maintain_once(self.guild, 239)
        self.voice.disconnect.assert_not_awaited()
        await self.client.maintain_once(self.guild, 240)
        self.voice.disconnect.assert_awaited_once()

    async def test_disabled_timer_and_unavailable_guild_do_not_disconnect(self):
        self.client.alone_timeout = 0
        await self.client.maintain_once(self.guild, 0)
        await self.client.maintain_once(self.guild, 999)
        self.client.alone_timeout = 120
        await self.client.maintain_once(self.guild, 0)
        self.guild.unavailable = True
        await self.client.maintain_once(self.guild, 999)
        self.voice.disconnect.assert_not_awaited()
        self.assertFalse(self.client.alone_since)

    async def test_external_disconnect_cancels_downloads(self):
        self.guild.voice_client = None
        await self.client.maintain_once(self.guild, 0)
        self.player.stop.assert_awaited_once()
        self.assertFalse(self.client.players)

    async def test_unrelated_room_event_does_not_postpone_disconnect(self):
        await self.client.maintain_once(self.guild, 0)
        member = SimpleNamespace(id=3, guild=self.guild)
        self.client.temp_voice.update = AsyncMock()
        await self.client.on_voice_state_update(member, SimpleNamespace(channel=SimpleNamespace(id=50)), SimpleNamespace(channel=None))
        await self.client.maintain_once(self.guild, 120)
        self.voice.disconnect.assert_awaited_once()

    async def test_shutdown_cancels_maintenance(self):
        started = asyncio.Event()
        async def worker():
            started.set()
            await asyncio.Event().wait()
        self.client.maintenance_task = asyncio.create_task(worker())
        await started.wait()
        await self.client.close()
        self.assertTrue(self.client.maintenance_task.cancelled())

    async def test_slow_room_recovery_does_not_block_alone_timer(self):
        began = asyncio.Event()
        async def slow(guild):
            began.set()
            await asyncio.Event().wait()
        self.client.temp_voice.recover.side_effect = slow
        await self.client.maintain_once(self.guild, 0)
        await asyncio.wait_for(began.wait(), 1)
        await asyncio.wait_for(self.client.maintain_once(self.guild, 120), 1)
        self.voice.disconnect.assert_awaited_once()
        self.client.temp_voice.recover.assert_awaited_once()

    async def test_old_gateway_disconnect_does_not_cancel_replacement_player(self):
        self.client.temp_voice.update = AsyncMock()
        member = SimpleNamespace(id=2, guild=self.guild)
        await self.client.on_voice_state_update(member, SimpleNamespace(channel=SimpleNamespace(id=99)), SimpleNamespace(channel=None))
        self.player.stop.assert_not_awaited()
        self.assertIs(self.client.players[1], self.player)

    async def test_current_gateway_disconnect_cancels_player(self):
        self.guild.voice_client = None
        self.client.temp_voice.update = AsyncMock()
        member = SimpleNamespace(id=2, guild=self.guild)
        await self.client.on_voice_state_update(member, SimpleNamespace(channel=self.channel), SimpleNamespace(channel=None))
        self.player.stop.assert_awaited_once()
        self.assertFalse(self.client.players)
