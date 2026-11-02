import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from diagnostics import health, RecentFailures, storage_check
from moderation import ModerationError, Store


class HealthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        env = patch.dict(os.environ, {'DATA_DIR':self.directory.name})
        env.start()
        self.addCleanup(env.stop)
        self.store = Store(Path(self.directory.name) / 'mod.sqlite3')
        self.actor = SimpleNamespace(id=1, guild_permissions=discord.Permissions(manage_guild=True))
        self.bot = SimpleNamespace(id=2, guild_permissions=discord.Permissions.all())
        self.guild = SimpleNamespace(me=self.bot, id=1, voice_client=None, fetch_member=AsyncMock(side_effect=lambda mid: self.actor if mid == 1 else self.bot))
        self.client = SimpleNamespace(latency=0.05, players={}, moderation=SimpleNamespace(store=self.store), rooms_for=lambda guild_id: SimpleNamespace(lobby_ids=set(), rooms=set()), alone_timeout=120, failures=RecentFailures())
        self.i = SimpleNamespace(guild=self.guild, user=self.actor, client=self.client, response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))

    async def test_private_health_with_storage_and_permissions(self):
        await health.callback(self.i)
        payload = self.i.followup.send.call_args.kwargs
        self.assertTrue(payload['ephemeral'])
        fields = {field.name:field.value for field in payload['embed'].fields}
        self.assertIn('database OK', fields['Storage'])
        self.assertIn('50 ms', fields['Gateway'])
        self.assertIn('Disconnected', fields['Voice'])

    async def test_health_uses_current_server_and_lists_multiple_parents(self):
        manager = SimpleNamespace(lobby_ids={10, 11}, rooms={20}, status_template='')
        self.client.rooms_for = Mock(return_value=manager)
        channel = Mock()
        channel.permissions_for.return_value = discord.Permissions.all()
        self.guild.get_channel = Mock(side_effect=lambda cid: channel if cid == 10 else None)
        await health.callback(self.i)
        self.client.rooms_for.assert_called_once_with(1)
        fields = {field.name: field.value for field in self.i.followup.send.call_args.kwargs['embed'].fields}
        self.assertIn('2 parent(s), 1 tracked', fields['Temporary rooms'])
        self.assertIn('Parent <#10>: required permissions present', fields['Bot permissions'])
        self.assertIn('Parent 11: configured channel is unavailable', fields['Bot permissions'])

    async def test_many_parents_fit_health_embed(self):
        self.client.rooms_for = lambda guild_id: SimpleNamespace(lobby_ids=set(range(1, 501)), rooms=set(), status_template='')
        self.guild.get_channel = Mock(return_value=None)
        await health.callback(self.i)
        embed = self.i.followup.send.call_args.kwargs['embed']
        self.assertLessEqual(len(embed), 6000)
        self.assertTrue(all(len(field.value) <= 1024 for field in embed.fields))

    async def test_nonstaff_cannot_inspect_storage(self):
        self.actor.guild_permissions = discord.Permissions.none()
        with patch('diagnostics.storage_check') as check:
            with self.assertRaises(ModerationError):
                await health.callback(self.i)
        check.assert_not_called()
        self.i.followup.send.assert_not_awaited()

    def test_recent_failures_never_include_exception_text_or_log_arguments(self):
        events = RecentFailures()
        for _ in range(8):
            events.emit(logging.LogRecord('bot.music', logging.WARNING, '', 0, 'secret token %s', ('credential',), (ValueError, ValueError('private'), None)))
        self.assertEqual(len(events.events), 5)
        rendered = '\n'.join(events.events)
        self.assertIn('Music: ValueError', rendered)
        for secret in ('secret', 'credential', 'private'):
            self.assertNotIn(secret, rendered)

    def test_storage_failure_has_safe_message(self):
        with patch('diagnostics.tempfile.TemporaryFile', side_effect=PermissionError('secret path')):
            self.assertIn('Storage check failed', storage_check(self.store))
