import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from moderation import ModerationError
from room_controls import RoomControls
from temp_voice import TempVoice


class RoomTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'rooms.json'
        self.manager = TempVoice(10, 'Room', self.path)
        self.manager.rooms = {20}
        self.manager.owners = {'20': 1}
        self.manager.labels = {'20': {'name': 'Room {number}', 'status': ''}}
        self.group = RoomControls(self.manager)
        self.actor = Mock(spec=discord.Member, id=1, bot=False)
        self.actor.guild_permissions = discord.Permissions.none()
        self.bot = Mock(spec=discord.Member, id=2, bot=True)
        self.target = Mock(spec=discord.Member, id=3, bot=False, mention='<@3>')
        self.role = Mock(spec=discord.Role, id=100)
        self.room = Mock(spec=discord.VoiceChannel, id=20, name='Room 1', status=None, position=1)
        self.room.voice_states = {1: Mock(), 3: Mock()}
        self.room.overwrites = {self.role: discord.PermissionOverwrite(connect=None, speak=False),
                                self.actor: discord.PermissionOverwrite(connect=None, stream=True)}
        self.room.permissions_for.return_value = discord.Permissions.all()
        async def edit(**kwargs):
            for key, value in kwargs.items():
                if key != 'reason':
                    setattr(self.room, key, value)
            return self.room
        self.room.edit = AsyncMock(side_effect=edit)
        self.guild = SimpleNamespace(id=100, me=self.bot, default_role=self.role, get_channel=lambda cid: self.room if cid == 20 else None)
        self.guild.fetch_member = AsyncMock(side_effect=lambda mid: {1:self.actor, 2:self.bot, 3:self.target}[mid])
        self.guild.fetch_channel = AsyncMock(return_value=self.room)
        self.room.guild = self.guild
        self.actor.voice = SimpleNamespace(channel=self.room)
        self.i = SimpleNamespace(guild=self.guild, user=self.actor, response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))

    async def test_nonowner_rejected_and_staff_allowed(self):
        self.manager.owners['20'] = 3
        with self.assertRaises(ModerationError):
            await self.group.limit.callback(self.group, self.i, 5)
        self.room.edit.assert_not_awaited()
        self.actor.guild_permissions.manage_channels = True
        await self.group.limit.callback(self.group, self.i, 5)
        self.assertEqual(self.room.user_limit, 5)

    async def test_rename_preserves_literal_name_and_number_after_restart(self):
        await self.group.rename.callback(self.group, self.i, 'Raid {number}')
        self.assertEqual(self.room.name, 'Raid {number} 1')
        restored = TempVoice(10, 'Other', self.path)
        self.room.name = 'Old'
        await restored.renumber(self.guild)
        self.assertEqual(self.room.name, 'Raid {number} 1')
        self.assertEqual(restored.owners, {'20': 1})

    async def test_lock_restore_survives_restart_and_preserves_unrelated_fields(self):
        await self.group.lock.callback(self.group, self.i)
        self.assertFalse(self.room.overwrites[self.role].connect)
        self.assertTrue(self.room.overwrites[self.actor].connect)
        self.assertTrue(self.room.overwrites[self.bot].connect)
        self.room.overwrites[self.role].use_voice_activation = False
        restored = RoomControls(TempVoice(10, 'Other', self.path))
        await restored.unlock.callback(restored, self.i)
        self.assertIsNone(self.room.overwrites[self.role].connect)
        self.assertFalse(self.room.overwrites[self.role].speak)
        self.assertFalse(self.room.overwrites[self.role].use_voice_activation)
        self.assertIsNone(self.room.overwrites[self.actor].connect)
        self.assertTrue(self.room.overwrites[self.actor].stream)
        self.assertNotIn(self.bot, self.room.overwrites)
        self.assertFalse(restored.manager.room_locks)

    async def test_failed_lock_keeps_recoverable_snapshot(self):
        self.room.edit.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason='Denied'), 'Denied')
        with self.assertRaises(discord.Forbidden):
            await self.group.lock.callback(self.group, self.i)
        self.assertIn('20', TempVoice(10, 'Room', self.path).room_locks)
        with self.assertRaisesRegex(ModerationError, 'saved lock'):
            await self.group.lock.callback(self.group, self.i)

    async def test_permission_edit_requires_manage_roles(self):
        self.room.permissions_for.return_value.manage_roles = False
        with self.assertRaises(ModerationError):
            await self.group.lock.callback(self.group, self.i)
        self.room.edit.assert_not_awaited()

    async def test_transfer_restores_lock_and_persists_new_owner(self):
        await self.group.lock.callback(self.group, self.i)
        await self.group.transfer.callback(self.group, self.i, self.target)
        self.assertEqual(TempVoice(10, 'Room', self.path).owners['20'], 3)
        self.assertFalse(self.manager.room_locks)
        self.assertIsNone(self.room.overwrites[self.actor].connect)
        with self.assertRaises(ModerationError):
            await self.group.limit.callback(self.group, self.i, 1)

    async def test_transfer_rejects_absent_or_bot_target(self):
        for bot, present in ((True, True), (False, False)):
            self.target.bot = bot
            if not present:
                self.room.voice_states.pop(3)
            with self.assertRaises(ModerationError):
                await self.group.transfer.callback(self.group, self.i, self.target)
        self.assertEqual(self.manager.owners['20'], 1)

    async def test_claim_cannot_take_occupied_owners_room(self):
        self.manager.owners['20'] = 3
        with self.assertRaises(ModerationError):
            await self.group.claim.callback(self.group, self.i)
        self.room.voice_states.pop(3)
        await self.group.claim.callback(self.group, self.i)
        self.assertEqual(self.manager.owners['20'], 1)

    async def test_legacy_room_can_be_claimed(self):
        self.manager.owners.clear()
        await self.group.claim.callback(self.group, self.i)
        self.assertEqual(TempVoice(10, 'Room', self.path).owners['20'], 1)

    async def test_untracked_channel_is_never_modified(self):
        self.manager.rooms.clear()
        with self.assertRaises(ModerationError):
            await self.group.limit.callback(self.group, self.i, 2)
        self.room.edit.assert_not_awaited()
