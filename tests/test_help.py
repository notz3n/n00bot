import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from bot import N00Bot, help_command
from moderation import visible_commands, COMMAND_PERMISSIONS


class HelpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(os.environ, {'DATA_DIR': directory.name})
        env.start()
        self.addCleanup(env.stop)
        self.client = N00Bot(1)
        self.addAsyncCleanup(self.client.close)
        self.group = self.client.tree.get_command('mod')
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

    def interaction(self):
        return SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()),
                               guild=SimpleNamespace(fetch_member=AsyncMock(return_value=self.member)), user=self.member,
                               channel=self.channel, client=self.client)

    async def render(self, topic='overview'):
        i = self.interaction()
        await help_command.callback(i, topic)
        i.guild.fetch_member.assert_awaited_once_with(1)
        self.assertTrue(i.followup.send.call_args.kwargs['ephemeral'])
        return i.followup.send.call_args.kwargs['embed']

    async def test_help_is_private_and_groups_public_commands(self):
        embed = await self.render()
        fields = {field.name: field.value for field in embed.fields}
        self.assertFalse(any('Moderation' in name or 'Diagnostics' in name for name in fields))
        music = fields['Voice & music · /help music']
        for name in ('join', 'leave', 'play', 'queue', 'nowplaying', 'skip', 'stop'):
            self.assertIn('/' + name, music)
        self.assertIn('ownership', fields['Temporary rooms · /help rooms'])

    async def test_moderation_topic_preserves_channel_permission_filter(self):
        self.member.guild_permissions = discord.Permissions(manage_messages=True, kick_members=True)
        self.channel.permissions_for.side_effect = lambda member: discord.Permissions.none()
        embed = await self.render('moderation')
        names = {field.name for field in embed.fields}
        self.assertIn('/mod kick member reason', names)
        self.assertFalse(any('purge' in name or 'warn' in name for name in names))

    async def test_room_help_shows_all_commands_and_ownership_rules(self):
        embed = await self.render('rooms')
        names = {field.name for field in embed.fields}
        self.assertIn('/room rename name', names)
        self.assertIn('/room limit members', names)
        self.assertIn('/room transfer member', names)
        self.assertIn('/room claim', names)
        detail = '\n'.join(field.value for field in embed.fields)
        self.assertIn('owner is absent', detail)
        self.assertIn('Manage Roles', detail)

    async def test_music_help_reflects_configured_timeout_and_optional_options(self):
        self.client.alone_timeout = 75
        embed = await self.render('music')
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn('/join [channel]', fields)
        self.assertIn('75 seconds', fields['Automatic disconnect'])
        self.assertIn('not a song-length limit', fields['Access & limits'])
        self.client.alone_timeout = 0
        disabled = await self.render('music')
        self.assertIn('disabled', disabled.fields[-1].value)

    async def test_staff_help_includes_full_case_syntax_and_diagnostics(self):
        self.member.guild_permissions = discord.Permissions.all()
        overview = await self.render()
        self.assertTrue(any('Diagnostics' in field.name for field in overview.fields))
        moderation = await self.render('moderation')
        names = {field.name for field in moderation.fields}
        self.assertIn('/mod history member [page] [action]', names)
        self.assertIn('/mod case case_id', names)

    async def test_all_topics_fit_discord_embed_limits(self):
        self.member.guild_permissions = discord.Permissions.all()
        for topic in ('overview', 'music', 'rooms', 'moderation', 'diagnostics'):
            embed = await self.render(topic)
            self.assertLessEqual(len(embed), 6000, topic)
            self.assertLessEqual(len(embed.fields), 25, topic)
            for field in embed.fields:
                self.assertLessEqual(len(field.name), 256)
                self.assertLessEqual(len(field.value), 1024)

    async def test_dm_help_has_only_general_commands(self):
        i = self.interaction()
        i.guild = None
        await help_command.callback(i, 'music')
        payload = i.followup.send.call_args.kwargs
        self.assertTrue(payload['ephemeral'])
        self.assertIn('/ping', payload['embed'].description)
        self.assertNotIn('/play', payload['embed'].description)
        self.assertFalse(payload['embed'].fields)

    async def test_public_commands_have_no_role_gate(self):
        client=N00Bot(1)
        for name in ('help','join','leave','play','stop','mod'):
            self.assertIsNone(client.tree.get_command(name).default_permissions)
        await client.close()
