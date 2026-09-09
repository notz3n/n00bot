import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from temp_voice import TempVoice


class TempVoiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'rooms.json'
        self.manager = TempVoice(10, "{user}'s {channel}", self.path)
        self.room = Mock(spec=discord.VoiceChannel)
        self.room.id = 20
        self.room.voice_states = {}
        self.room.delete = AsyncMock()
        self.room.edit = AsyncMock()
        self.lobby = Mock(spec=discord.VoiceChannel)
        self.lobby.id = 10
        self.lobby.position = 4
        self.lobby.category = Mock()
        self.lobby.name = 'Lobby'
        self.lobby.clone = AsyncMock(return_value=self.room)
        self.lobby.permissions_for.return_value = SimpleNamespace(manage_channels=True, move_members=True, view_channel=True, connect=True)
        self.guild = SimpleNamespace(unavailable=False, me=Mock(), get_channel=lambda channel_id: {10: self.lobby, 20: self.room}.get(channel_id))
        self.room.guild = self.guild
        self.member = SimpleNamespace(bot=False, display_name='Alex', guild=self.guild, voice=SimpleNamespace(channel=self.lobby), move_to=AsyncMock())

    async def test_create_move_and_persist(self):
        await self.manager.update(self.member, SimpleNamespace(channel=None), SimpleNamespace(channel=self.lobby))
        self.lobby.clone.assert_awaited_once_with(name="Alex's Lobby 1", category=self.lobby.category, reason='Join-to-create voice room')
        self.member.move_to.assert_awaited_once()
        self.assertEqual(TempVoice(10, 'Room', self.path).rooms, {20})
        self.room.edit.assert_awaited_once_with(position=5, reason='Place temporary room below lobby')

    async def test_status_template(self):
        self.manager.status_template = '{channel} {number} — Hosted by {user}'
        await self.manager.update(self.member, SimpleNamespace(channel=None), SimpleNamespace(channel=self.lobby))
        self.room.edit.assert_any_await(status='Lobby 1 — Hosted by Alex', reason='Temporary voice room status')
        self.member.move_to.assert_awaited_once()

    async def test_deleting_first_room_renumbers_name_and_status(self):
        other = Mock(spec=discord.VoiceChannel)
        other.id = 30
        other.name = 'room 2'
        other.edit = AsyncMock()
        self.guild.get_channel = lambda cid: {20:self.room,30:other}.get(cid)
        self.manager.rooms = {20,30}
        self.manager.labels = {'30': {'name':'room {number}', 'status':'Room {number} — Alex'}}
        await self.manager.delete_empty(self.room)
        self.assertEqual(self.manager.rooms, {30})
        other.edit.assert_any_await(name='room 1', reason='Renumber active temporary rooms')
        other.edit.assert_any_await(status='Room 1 — Alex', reason='Renumber active temporary rooms')

    async def test_legacy_explicit_number_migration(self):
        self.manager.template = 'Room #{number} — {user}'
        self.manager.status_template = 'Hosted by {user}, room {number}'
        self.manager.rooms = {20}
        self.room.name = 'Room #87 — Alex'
        await self.manager.renumber(self.guild)
        self.room.edit.assert_any_await(name='Room #1 — Alex', reason='Renumber active temporary rooms')
        self.room.edit.assert_any_await(status='Hosted by Alex, room 1', reason='Renumber active temporary rooms')

    async def test_status_failure_still_moves_member(self):
        self.manager.status_template = 'Welcome'
        self.room.edit.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'Denied')
        with self.assertLogs('bot.temp_voice', level='WARNING'):
            await self.manager.update(self.member, SimpleNamespace(channel=None), SimpleNamespace(channel=self.lobby))
        self.member.move_to.assert_awaited_once()
        self.assertIn(20, self.manager.rooms)

    async def test_status_length_limit(self):
        self.manager.status_template = 'x' * 600
        await self.manager.update(self.member, SimpleNamespace(channel=None), SimpleNamespace(channel=self.lobby))
        self.assertEqual(len(self.room.edit.call_args.kwargs['status']), 500)

    async def test_number_restarts_after_last_room_deleted(self):
        self.manager.template = '{channel}'
        self.lobby.name = 'room'
        await self.manager.update(self.member, SimpleNamespace(channel=None), SimpleNamespace(channel=self.lobby))
        self.assertEqual(self.lobby.clone.call_args.kwargs['name'], 'room 1')
        await self.manager.delete_empty(self.room)
        restored = TempVoice(10, '{channel}', self.path)
        await restored.update(self.member, SimpleNamespace(channel=None), SimpleNamespace(channel=self.lobby))
        self.assertEqual(self.lobby.clone.call_args.kwargs['name'], 'room 1')

    async def test_explicit_number_placeholder(self):
        self.manager.template = 'Room #{number} — {user}'
        await self.manager.update(self.member, SimpleNamespace(channel=None), SimpleNamespace(channel=self.lobby))
        self.assertEqual(self.lobby.clone.call_args.kwargs['name'], 'Room #1 — Alex')

    async def test_multiple_rooms_keep_number_and_order_after_restart(self):
        self.manager.template = 'Room #{number}'
        channels = {10: self.lobby}
        self.guild.get_channel = channels.get
        created = []
        async def clone(**kwargs):
            room = Mock(spec=discord.VoiceChannel)
            room.id = 20 + len(created)
            room.name = kwargs['name']
            room.voice_states = {room.id: Mock()}
            room.guild = self.guild
            room.edit = AsyncMock()
            room.delete = AsyncMock()
            channels[room.id] = room
            created.append(room)
            return room
        self.lobby.clone.side_effect = clone
        for _ in range(3):
            await self.manager.update(self.member, SimpleNamespace(channel=None), SimpleNamespace(channel=self.lobby))
        self.assertEqual([room.name for room in created], ['Room #1', 'Room #2', 'Room #3'])
        for number, room in enumerate(created, 1):
            room.edit.assert_any_await(position=4 + number, reason='Place temporary room below lobby')
        restored = TempVoice(10, 'Room #{number}', self.path)
        created[0].voice_states = {}
        await restored.delete_empty(created[0])
        for number, room in enumerate(created[1:], 1):
            room.edit.assert_any_await(name=f'Room #{number}', reason='Renumber active temporary rooms')
            room.edit.assert_any_await(position=4 + number, reason='Order temporary rooms below lobby')

    def test_legacy_state_migration(self):
        self.path.write_text('[20]')
        restored = TempVoice(10, 'Room', self.path)
        self.assertEqual(restored.rooms, {20})
        restored.save()
        self.assertEqual(TempVoice(10, 'Room', self.path).rooms, {20})

    async def test_long_name_preserves_automatic_suffix(self):
        self.manager.template = 'x' * 120
        await self.manager.update(self.member, SimpleNamespace(channel=None), SimpleNamespace(channel=self.lobby))
        name = self.lobby.clone.call_args.kwargs['name']
        self.assertEqual(len(name), 100)
        self.assertTrue(name.endswith(' 1'))

    async def test_delete_on_last_departure(self):
        self.manager.rooms.add(20)
        await self.manager.update(self.member, SimpleNamespace(channel=self.room), SimpleNamespace(channel=None))
        self.room.delete.assert_awaited_once()
        self.assertFalse(self.manager.rooms)

    async def test_occupied_and_untracked_channels_survive(self):
        await self.manager.delete_empty(self.room)
        self.manager.rooms.add(20)
        self.room.voice_states = {123: Mock()}
        await self.manager.delete_empty(self.room)
        self.room.delete.assert_not_awaited()

    async def test_lobby_never_deleted(self):
        self.manager.rooms.add(10)
        self.lobby.voice_states = {}
        await self.manager.delete_empty(self.lobby)
        self.lobby.delete.assert_not_called()

    async def test_bot_and_mute_events_ignored(self):
        state = SimpleNamespace(channel=self.lobby)
        await self.manager.update(self.member, state, state)
        self.member.bot = True
        await self.manager.update(self.member, SimpleNamespace(channel=None), state)
        self.lobby.clone.assert_not_awaited()

    async def test_move_failure_cleans_empty_room(self):
        self.member.move_to.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'Denied')
        with self.assertRaises(discord.Forbidden):
            await self.manager.update(self.member, SimpleNamespace(channel=None), SimpleNamespace(channel=self.lobby))
        self.room.delete.assert_awaited_once()
        self.assertFalse(self.manager.rooms)

    async def test_departure_during_creation(self):
        async def clone(**kwargs):
            self.member.voice = None
            return self.room
        self.lobby.clone.side_effect = clone
        await self.manager.update(self.member, SimpleNamespace(channel=None), SimpleNamespace(channel=self.lobby))
        self.member.move_to.assert_not_awaited()
        self.room.delete.assert_awaited_once()

    async def test_recovery(self):
        self.manager.rooms.add(20)
        self.manager.save()
        restored = TempVoice(10, 'Room', self.path)
        await restored.recover(self.guild)
        self.room.delete.assert_awaited_once()
        self.assertFalse(restored.rooms)

    async def test_delete_failure_retains_tracking(self):
        self.manager.rooms.add(20)
        self.room.delete.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Forbidden'), 'Denied')
        with self.assertLogs('bot.temp_voice', level='ERROR'):
            await self.manager.delete_empty(self.room)
        self.assertEqual(self.manager.rooms, {20})
