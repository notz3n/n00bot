import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import discord
from moderation import Moderation, ModerationError, Store, hierarchy


class ModerationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'moderation.sqlite3'
        self.mod = Moderation(self.path)
        self.actor = SimpleNamespace(id=1, top_role=5, guild_permissions=discord.Permissions.all())
        self.bot = SimpleNamespace(id=2, top_role=10, guild_permissions=discord.Permissions.all())
        self.target = SimpleNamespace(id=3, top_role=1, mention='<@3>', bot=False,
                                      guild_permissions=discord.Permissions.none(), kick=AsyncMock(), timeout=AsyncMock(), add_roles=AsyncMock())
        self.guild = SimpleNamespace(id=100, owner_id=99, me=self.bot, ban=AsyncMock(), unban=AsyncMock())
        self.guild.fetch_member = AsyncMock(side_effect=lambda mid: {1:self.actor,2:self.bot,3:self.target}[mid])
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.id = 200
        self.channel.permissions_for.side_effect = lambda member: member.guild_permissions
        self.i = SimpleNamespace(guild=self.guild, user=self.actor, channel=self.channel, response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()), created_at=discord.utils.utcnow())

    async def test_unauthorized_kick_does_not_call_discord(self):
        self.actor.guild_permissions = discord.Permissions.none()
        with self.assertRaises(ModerationError):
            await self.mod.kick.callback(self.mod, self.i, self.target, 'Reason')
        self.target.kick.assert_not_awaited()

    def test_hierarchy_protects_owner_self_bot_and_peers(self):
        for target_id, role in ((99,1),(1,1),(2,1),(3,5),(3,10)):
            with self.assertRaises(ModerationError):
                hierarchy(self.actor, SimpleNamespace(id=target_id, top_role=role), self.bot,99)

    def test_owner_still_cannot_bypass_bot_hierarchy(self):
        self.actor.id = 99
        with self.assertRaises(ModerationError):
            hierarchy(self.actor, SimpleNamespace(id=3, top_role=10), self.bot,99)

    async def test_ban_preserves_messages(self):
        await self.mod.ban.callback(self.mod, self.i, self.target, 'Reason')
        self.assertEqual(self.guild.ban.call_args.kwargs['delete_message_seconds'], 0)

    async def test_admin_timeout_rejected(self):
        self.target.guild_permissions = discord.Permissions(administrator=True)
        with self.assertRaises(ModerationError):
            await self.mod.timeout.callback(self.mod, self.i, self.target, 10, 'Reason')
        self.target.timeout.assert_not_awaited()

    async def test_timeout_duration(self):
        await self.mod.timeout.callback(self.mod, self.i, self.target, 10, 'Reason')
        self.assertEqual(self.target.timeout.call_args.args[0].total_seconds(), 600)

    async def test_warning_persistence_and_server_isolation(self):
        await self.mod.warn.callback(self.mod, self.i, self.target, 'Reason')
        store = Store(self.path)
        rows, _, _ = store.query('SELECT id, reason FROM warnings WHERE guild=?', (100,))
        self.assertEqual(rows[0][1], 'Reason')
        self.guild.id = 101
        await self.mod.unwarn.callback(self.mod, self.i, rows[0][0])
        self.assertEqual(len(store.query('SELECT * FROM warnings')[0]), 1)

    async def test_purge_skips_pins(self):
        self.channel.purge = AsyncMock(return_value=[Mock(),Mock()])
        await self.mod.purge.callback(self.mod, self.i, 10)
        kwargs = self.channel.purge.call_args.kwargs
        self.assertFalse(kwargs['check'](SimpleNamespace(pinned=True)))
        self.assertTrue(kwargs['check'](SimpleNamespace(pinned=False)))
        self.assertEqual(kwargs['limit'], 10)

    async def test_lock_restores_tristate_after_restart(self):
        self.guild.default_role = Mock()
        overwrite = discord.PermissionOverwrite(send_messages=None, send_messages_in_threads=True, create_public_threads=False, attach_files=True)
        self.channel.overwrites_for.side_effect = lambda role: discord.PermissionOverwrite.from_pair(*overwrite.pair())
        async def apply(role, **kwargs):
            nonlocal overwrite
            overwrite = kwargs['overwrite']
        self.channel.set_permissions = AsyncMock(side_effect=apply)
        self.guild.fetch_channel = AsyncMock(return_value=self.channel)
        await self.mod.lock.callback(self.mod,self.i,'Reason')
        self.assertFalse(overwrite.send_messages)
        self.assertTrue(overwrite.attach_files)
        restored = Moderation(self.path)
        await restored.unlock.callback(restored,self.i,'Reason')
        self.assertIsNone(overwrite.send_messages)
        self.assertTrue(overwrite.send_messages_in_threads)
        self.assertFalse(overwrite.create_public_threads)
        self.assertTrue(overwrite.attach_files)
        self.assertFalse(restored.store.query('SELECT * FROM locks')[0])

    async def test_role_escalation_rejected(self):
        class Role:
            id=50
            managed=False
            permissions=discord.Permissions(administrator=True)
            def is_default(self): return False
            def __ge__(self, other): return False
        role=Role()
        self.actor.guild_permissions=discord.Permissions(manage_roles=True)
        self.guild.fetch_roles=AsyncMock(return_value=[role])
        with self.assertRaises(ModerationError):
            await self.mod.role_add.callback(self.mod,self.i,self.target,role,'Reason')
        self.target.add_roles.assert_not_awaited()
