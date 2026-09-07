import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from bot import N00Bot, help_command
from moderation import Moderation, visible_commands, COMMAND_PERMISSIONS


class HelpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.group = Moderation(Path('/tmp/unused-help-test.sqlite3'))
        self.member = SimpleNamespace(id=1, guild_permissions=discord.Permissions.none())
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.permissions_for.side_effect = lambda member: member.guild_permissions

    def names(self):
        return {c.name for c in visible_commands(self.group, self.member, self.channel)}

    def test_regular_member(self):
        self.assertEqual(self.names(), set())

    def test_specific_staff_permissions(self):
        self.member.guild_permissions = discord.Permissions(kick_members=True, manage_roles=True)
        self.assertEqual(self.names(), {'kick','lock','unlock','role_add','role_remove'})

    def test_channel_overrides(self):
        self.member.guild_permissions = discord.Permissions(manage_messages=True)
        self.channel.permissions_for.side_effect = lambda member: discord.Permissions.none()
        self.assertEqual(self.names(), set())

    def test_admin_permissions(self):
        self.member.guild_permissions = discord.Permissions.all()
        self.assertEqual(self.names(), set(COMMAND_PERMISSIONS))

    async def test_help_is_private_and_fetches_member(self):
        i=SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()),
                          guild=SimpleNamespace(fetch_member=AsyncMock(return_value=self.member)), user=self.member,
                          channel=self.channel, client=SimpleNamespace(tree=SimpleNamespace(get_command=lambda name:self.group)))
        await help_command.callback(i)
        i.guild.fetch_member.assert_awaited_once_with(1)
        payload=i.followup.send.call_args.kwargs
        self.assertTrue(payload['ephemeral'])
        self.assertFalse(payload['embed'].fields)
        for name in ('join','leave','play','stop'):
            self.assertIn('/'+name, payload['embed'].description)

    async def test_public_commands_have_no_role_gate(self):
        client=N00Bot(1)
        for name in ('help','join','leave','play','stop','mod'):
            self.assertIsNone(client.tree.get_command(name).default_permissions)
        await client.close()
