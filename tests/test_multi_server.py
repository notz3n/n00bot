import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from bot import N00Bot, read_config
from temp_voice import TempVoice
from moderation import ModerationError


class MultiServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        env = patch.dict(os.environ, {'DATA_DIR': self.directory.name, 'TEMP_VOICE_LOBBY_ID': '10'})
        env.start()
        self.addCleanup(env.stop)
        self.client = N00Bot(1)
        self.addAsyncCleanup(self.client.close)

    async def test_managers_isolate_state_locks_and_environment_default(self):
        first, second = self.client.rooms_for(1), self.client.rooms_for(2)
        self.assertEqual(first.lobby_ids, {10})
        self.assertEqual(second.lobby_ids, set())
        self.assertIsNot(first.lock, second.lock)
        first.lobby_ids.add(11)
        second.lobby_ids.add(21)
        first.rooms.add(100)
        first.owners['100'] = 7
        first.save()
        second.save()
        self.assertEqual(TempVoice(None, 'Room', first.path).lobby_ids, {10, 11})
        self.assertEqual(TempVoice(None, 'Room', second.path).lobby_ids, {21})
        self.assertFalse(second.rooms)
        self.assertFalse(second.owners)

    async def test_all_servers_receive_commands_and_new_join_syncs(self):
        async def guilds(**kwargs):
            for guild_id in (1, 2):
                yield discord.Object(guild_id)
        self.client.fetch_guilds = guilds
        self.client.tree.sync = AsyncMock(return_value=[])
        await self.client.setup_hook()
        await self.client.on_guild_join(discord.Object(3))
        self.assertEqual([call.kwargs['guild'].id for call in self.client.tree.sync.call_args_list], [1, 2, 3])
        for guild_id in (1, 2, 3):
            group = self.client.tree.get_command('room', guild=discord.Object(guild_id))
            self.assertIsNotNone(group.get_command('setparent'))
            self.assertIsNotNone(group.get_command('removeparent'))
            self.assertIsNotNone(group.get_command('parents'))

    async def test_sync_failure_in_one_server_does_not_skip_others(self):
        async def guilds(**kwargs):
            yield discord.Object(1)
            yield discord.Object(2)
        self.client.fetch_guilds = guilds
        self.client.tree.sync = AsyncMock(side_effect=[discord.Forbidden(SimpleNamespace(status=403, reason='Denied'), 'Denied'), []])
        with self.assertLogs('bot', level='ERROR'):
            await self.client.setup_hook()
        self.assertEqual(self.client.tree.sync.await_count, 2)

    async def test_voice_events_route_to_their_own_server(self):
        for guild_id in (1, 2):
            self.client.rooms_for(guild_id).update = AsyncMock()
            guild = SimpleNamespace(id=guild_id, me=SimpleNamespace(id=99), voice_client=None)
            member = SimpleNamespace(id=7, guild=guild)
            before, after = SimpleNamespace(channel=None), SimpleNamespace(channel=Mock())
            await self.client.on_voice_state_update(member, before, after)
            self.client.rooms_for(guild_id).update.assert_awaited_once_with(member, before, after)
        self.client.rooms_for(1).update.assert_awaited_once()

    async def test_slow_recovery_in_one_server_does_not_block_another(self):
        began = asyncio.Event()
        async def slow(guild):
            began.set()
            await asyncio.Event().wait()
        self.client.rooms_for(1).recover = AsyncMock(side_effect=slow)
        self.client.rooms_for(2).recover = AsyncMock()
        for guild_id in (1, 2):
            guild = SimpleNamespace(id=guild_id, unavailable=False, me=Mock(), voice_client=None)
            await self.client.maintain_once(guild, 10)
        await asyncio.wait_for(began.wait(), 1)
        await asyncio.wait_for(self.client.recovery_tasks[2], 1)
        self.client.rooms_for(2).recover.assert_awaited_once()
        self.assertFalse(self.client.recovery_tasks[1].done())

    async def test_guild_removal_cancels_only_its_tasks_and_preserves_saved_state(self):
        manager = self.client.rooms_for(1)
        manager.save()
        task = asyncio.create_task(asyncio.Event().wait())
        self.client.recovery_tasks[1] = task
        self.client.players[1] = SimpleNamespace(stop=AsyncMock())
        other = SimpleNamespace(stop=AsyncMock())
        self.client.players[2] = other
        await self.client.on_guild_remove(discord.Object(1))
        self.assertTrue(task.cancelled())
        self.assertNotIn(1, self.client.room_managers)
        self.assertTrue(manager.path.exists())
        other.stop.assert_not_awaited()
        self.assertEqual(self.client.rooms_for(1).lobby_ids, {10})

    async def test_deleted_parent_is_removed_only_in_its_server(self):
        first, second = self.client.rooms_for(1), self.client.rooms_for(2)
        second.lobby_ids.add(20)
        first.rooms.add(100)
        first.parents['100'] = 10
        await self.client.on_guild_channel_delete(SimpleNamespace(id=10, guild=SimpleNamespace(id=1)))
        self.assertEqual(first.lobby_ids, set())
        self.assertEqual(first.rooms, {100})
        self.assertEqual(first.parents, {'100': 10})
        self.assertEqual(second.lobby_ids, {20})

    async def test_command_resolution_does_not_share_mutable_manager(self):
        group = self.client.tree.get_command('room')
        first, second = self.client.rooms_for(1), self.client.rooms_for(2)
        async def interaction(guild_id, channel_id):
            actor = SimpleNamespace(id=7, guild_permissions=discord.Permissions(manage_channels=True))
            guild = SimpleNamespace(id=guild_id, me=Mock(id=9), fetch_member=AsyncMock(return_value=actor))
            channel = Mock(spec=discord.VoiceChannel, id=channel_id, guild=guild, mention=f'<#{channel_id}>')
            channel.permissions_for.return_value = discord.Permissions.all()
            guild.fetch_channel = AsyncMock(return_value=channel)
            i = SimpleNamespace(guild=guild, user=actor, response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
            await group.setparent.callback(group, i, channel)
        await asyncio.gather(interaction(1, 11), interaction(2, 21))
        self.assertEqual(first.lobby_ids, {10, 11})
        self.assertEqual(second.lobby_ids, {21})

    def test_optional_legacy_server_config(self):
        with patch('bot.load_dotenv'), patch.dict(os.environ, {'DISCORD_TOKEN': 'test', 'DISCORD_GUILD_ID': ''}):
            self.assertEqual(read_config(), ('test', None))
        with patch('bot.load_dotenv'), patch.dict(os.environ, {'DISCORD_TOKEN': 'test', 'DISCORD_GUILD_ID': 'invalid'}):
            with self.assertRaises(ValueError):
                read_config()


class MigrationTests(unittest.TestCase):
    def test_old_single_parent_state_retains_rooms_owners_labels_and_locks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rooms.json'
            state = {'rooms': [20], 'lobby_id': 10, 'owners': {'20': 7},
                     'labels': {'20': {'name': 'Room {number}', 'status': ''}}, 'locks': {'20': {'7': None}}}
            path.write_text(json.dumps(state))
            manager = TempVoice(99, 'Room', path)
            self.assertEqual(manager.lobby_ids, {10})
            self.assertEqual(manager.parents, {'20': 10})
            manager.save()
            restored = TempVoice(99, 'Room', path)
            self.assertEqual(restored.owners, state['owners'])
            self.assertEqual(restored.labels, state['labels'])
            self.assertEqual(restored.room_locks, state['locks'])
            self.assertEqual(restored.parents, {'20': 10})

    def test_removing_all_parents_does_not_restore_environment_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rooms.json'
            manager = TempVoice(10, 'Room', path)
            manager.lobby_ids.clear()
            manager.save()
            self.assertEqual(TempVoice(10, 'Room', path).lobby_ids, set())
