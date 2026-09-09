"""Server moderation commands with runtime permissions and durable local state."""

import asyncio
import logging
from datetime import timedelta
from pathlib import Path
import sqlite3

import discord
from discord import app_commands

COMMAND_PERMISSIONS = {
    'warn': ('moderate_members', False),
    'case': ('moderate_members', False),
    'history': ('moderate_members', False),
    'warnings': ('moderate_members', False),
    'unwarn': ('moderate_members', False),
    'timeout': ('moderate_members', False),
    'untimeout': ('moderate_members', False),
    'kick': ('kick_members', False),
    'ban': ('ban_members', False),
    'unban': ('ban_members', False),
    'purge': ('manage_messages', True),
    'lock': ('manage_roles', True),
    'unlock': ('manage_roles', True),
    'role_add': ('manage_roles', False),
    'role_remove': ('manage_roles', False),
}


def visible_commands(group, member, channel):
    """Filter help by the same permission and channel scopes as execution."""
    for command in group.commands:
        permission, channel_scoped = COMMAND_PERMISSIONS[command.name]
        if channel_scoped and not isinstance(channel, discord.TextChannel):
            continue
        permissions = channel.permissions_for(member) if channel_scoped else member.guild_permissions
        if getattr(permissions, permission):
            yield command


class ModerationError(app_commands.CheckFailure):
    pass


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.initialize()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=0.25)
        try:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS warnings (id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER, member INTEGER, moderator INTEGER, reason TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP)')
                db.execute('CREATE TABLE IF NOT EXISTS locks (guild INTEGER, channel INTEGER, send INTEGER, threads INTEGER, public INTEGER, private INTEGER, PRIMARY KEY(guild, channel))')
                db.execute('CREATE TABLE IF NOT EXISTS cases (id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER NOT NULL, member INTEGER NOT NULL, moderator INTEGER NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP, status TEXT NOT NULL, warning_id INTEGER UNIQUE, related_case INTEGER)')
                db.execute('CREATE INDEX IF NOT EXISTS cases_member ON cases(guild,member,id)')
                db.execute("INSERT OR IGNORE INTO cases(guild,member,moderator,action,reason,created,status,warning_id) SELECT guild,member,moderator,'warn',reason,created,'succeeded',id FROM warnings")
                db.execute("UPDATE cases SET status='unknown' WHERE status='pending'")
        finally:
            db.close()

    def connect(self):
        return sqlite3.connect(self.path, timeout=0.25)

    def record_warning(self, guild, member, moderator, reason):
        db = self.connect()
        try:
            with db:
                warning_id = db.execute('INSERT INTO warnings(guild,member,moderator,reason) VALUES(?,?,?,?)', (guild,member,moderator,reason)).lastrowid
                case_id = db.execute("INSERT INTO cases(guild,member,moderator,action,reason,status,warning_id) VALUES(?,?,?,'warn',?,'succeeded',?)", (guild,member,moderator,reason,warning_id)).lastrowid
            return warning_id, case_id
        finally:
            db.close()

    def remove_warning(self, guild, warning_id, moderator):
        db = self.connect()
        try:
            with db:
                row = db.execute('SELECT member FROM warnings WHERE guild=? AND id=?', (guild,warning_id)).fetchone()
                if row is None:
                    return None
                original = db.execute('SELECT id FROM cases WHERE guild=? AND warning_id=?', (guild,warning_id)).fetchone()
                case_id = db.execute("INSERT INTO cases(guild,member,moderator,action,reason,status,related_case) VALUES(?,?,?,'unwarn',?,'succeeded',?)", (guild,row[0],moderator,f'Removed warning #{warning_id}',original[0] if original else None)).lastrowid
                db.execute('DELETE FROM warnings WHERE guild=? AND id=?', (guild,warning_id))
            return case_id
        finally:
            db.close()

    def query(self, sql, values=()):
        db = self.connect()
        try:
            with db:
                cursor = db.execute(sql, values)
                return cursor.fetchall(), cursor.lastrowid, cursor.rowcount
        finally:
            db.close()


def hierarchy(actor, target, bot, owner_id, *, bot_check=True):
    if target.id in (owner_id, actor.id, bot.id):
        raise ModerationError('You cannot target yourself, the server owner, or this bot.')
    if actor.id != owner_id and target.top_role >= actor.top_role:
        raise ModerationError('Your highest role must be above the target member’s highest role.')
    if bot_check and target.top_role >= bot.top_role:
        raise ModerationError('Move my bot role above the target member’s highest role.')


async def authorize(i, permission, *, bot_permission=None, target=None, channel=False):
    if i.guild is None or i.guild.me is None:
        raise ModerationError('Use this command in a server where the bot is installed.')
    await i.response.defer(ephemeral=True, thinking=True)
    # Refresh member roles rather than relying on an old member cache.
    actor = await i.guild.fetch_member(i.user.id)
    bot = await i.guild.fetch_member(i.guild.me.id)
    user_perms = i.channel.permissions_for(actor) if channel else actor.guild_permissions
    bot_perms = i.channel.permissions_for(bot) if channel else bot.guild_permissions
    if not getattr(user_perms, permission):
        raise ModerationError(f'You need {permission.replace("_", " ").title()} permission.')
    if bot_permission and not getattr(bot_perms, bot_permission):
        raise ModerationError(f'I need {bot_permission.replace("_", " ").title()} permission.')
    if target is not None:
        target = await i.guild.fetch_member(target.id)
        hierarchy(actor, target, bot, i.guild.owner_id, bot_check=bot_permission is not None)
    return actor, bot, target


def audit(i, reason):
    return f'{i.user} ({i.user.id}): {reason}'[:512]


class Moderation(app_commands.Group):
    def __init__(self, path):
        # No group-wide gate: staff may have Kick Members or Manage Roles without
        # Moderate Members. Each action still enforces its own permissions.
        super().__init__(name='mod', description='Server administration and moderation', guild_only=True)
        self.store = Store(path)
        self.locks = {}

    async def perform(self, i, member_id, action, reason, operation):
        # Reserve before Discord: if persistence fails, no moderation action runs.
        _, case_id, _ = self.store.query("INSERT INTO cases(guild,member,moderator,action,reason,status) VALUES(?,?,?,?,?,'pending')", (i.guild.id,member_id,i.user.id,action,reason))
        try:
            await operation()
        except BaseException as error:
            status = 'failed' if isinstance(error, (discord.Forbidden, discord.NotFound)) else 'unknown'
            try:
                self.store.query('UPDATE cases SET status=? WHERE id=?', (status,case_id))
            except (sqlite3.Error, OSError):
                logging.getLogger('bot.moderation').exception('Could not finalize failed moderation case')
            if status == 'unknown' and isinstance(error, Exception):
                raise ModerationError(f'Case #{case_id} has an unknown outcome. Check the Discord audit log before repeating this action.') from error
            raise
        try:
            self.store.query("UPDATE cases SET status='succeeded' WHERE id=?", (case_id,))
        except (sqlite3.Error, OSError) as error:
            logging.getLogger('bot.moderation').exception('Discord action succeeded but case finalization failed')
            raise ModerationError(f'The Discord action succeeded, but case #{case_id} could not be finalized. Check the audit log before repeating it.') from error
        return case_id

    @staticmethod
    def case_embed(rows, title):
        embed = discord.Embed(title=title)
        for case_id, member, moderator, action, reason, created, status, related in rows:
            detail = f'Member: <@{member}> • Moderator: <@{moderator}>\n{created} UTC • {status}\n{discord.utils.escape_markdown(reason[:450])}'
            if related:
                detail += f'\nRelated case: #{related}'
            embed.add_field(name=f'Case #{case_id} — {action}', value=detail, inline=False)
        if not rows:
            embed.description = 'No matching cases in this server.'
        return embed

    @app_commands.command(name='case', description='Look up a moderation case by its ID.')
    async def case(self, i: discord.Interaction, case_id: app_commands.Range[int, 1]):
        await authorize(i, 'moderate_members')
        rows, _, _ = self.store.query('SELECT id,member,moderator,action,reason,created,status,related_case FROM cases WHERE guild=? AND id=?', (i.guild.id,case_id))
        await i.followup.send(embed=self.case_embed(rows, 'Moderation case'), ephemeral=True)

    @app_commands.command(description='View a user’s moderation history, optionally filtered by action.')
    @app_commands.choices(action=[app_commands.Choice(name=value, value=value) for value in ('warn', 'unwarn', 'timeout', 'untimeout', 'kick', 'ban', 'unban')])
    async def history(self, i: discord.Interaction, member: discord.User, page: app_commands.Range[int, 1, 10000] = 1, action: str | None = None):
        await authorize(i, 'moderate_members')
        rows, _, _ = self.store.query('SELECT id,member,moderator,action,reason,created,status,related_case FROM cases WHERE guild=? AND member=? AND (? IS NULL OR action=?) ORDER BY id DESC LIMIT 5 OFFSET ?', (i.guild.id,member.id,action,action,(page-1)*5))
        await i.followup.send(embed=self.case_embed(rows, f'Moderation history — page {page}'), ephemeral=True)

    @app_commands.command(description='Record a warning for a member.')
    async def warn(self, i: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 1, 400]):
        _, _, member = await authorize(i, 'moderate_members', target=member)
        warning_id, case_id = self.store.record_warning(i.guild.id, member.id, i.user.id, reason)
        await i.followup.send(f'Warning #{warning_id} recorded for {member.mention}. Case #{case_id}.', ephemeral=True)

    @app_commands.command(description='View saved warnings for a user, 5 per page.')
    async def warnings(self, i: discord.Interaction, member: discord.User, page: app_commands.Range[int, 1, 10000] = 1):
        await authorize(i, 'moderate_members')
        rows, _, _ = self.store.query('SELECT id, moderator, reason, created FROM warnings WHERE guild=? AND member=? ORDER BY id DESC LIMIT 5 OFFSET ?', (i.guild.id, member.id, (page-1)*5))
        embed = discord.Embed(title=f'Warnings — page {page}')
        for warning_id, moderator, reason, created in rows:
            embed.add_field(name=f'#{warning_id} • {created} UTC', value=f'Moderator: <@{moderator}>\n{discord.utils.escape_markdown(reason)}', inline=False)
        if not rows:
            embed.description = 'No warnings on this page.'
        await i.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(description='Delete one warning by its ID.')
    async def unwarn(self, i: discord.Interaction, warning_id: app_commands.Range[int, 1]):
        await authorize(i, 'moderate_members')
        case_id = self.store.remove_warning(i.guild.id, warning_id, i.user.id)
        await i.followup.send(f'Warning removed. Case #{case_id}; original case history retained.' if case_id else 'Warning not found in this server.', ephemeral=True)

    @app_commands.command(description='Timeout a member for up to 28 days.')
    async def timeout(self, i: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: app_commands.Range[str, 1, 400]):
        _, _, member = await authorize(i, 'moderate_members', bot_permission='moderate_members', target=member)
        if member.bot or member.guild_permissions.administrator:
            raise ModerationError('Discord does not allow timeouts for bots or administrators.')
        case_id = await self.perform(i, member.id, 'timeout', f'{minutes} minutes: {reason}', lambda: member.timeout(timedelta(minutes=minutes), reason=audit(i, reason)))
        await i.followup.send(f'Timed out {member.mention} for {minutes} minutes. Case #{case_id}.', ephemeral=True)

    @app_commands.command(description='Remove a member’s timeout.')
    async def untimeout(self, i: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 1, 400]):
        _, _, member = await authorize(i, 'moderate_members', bot_permission='moderate_members', target=member)
        case_id = await self.perform(i, member.id, 'untimeout', reason, lambda: member.timeout(None, reason=audit(i, reason)))
        await i.followup.send(f'Timeout removed. Case #{case_id}.', ephemeral=True)

    @app_commands.command(description='Kick a member from this server.')
    async def kick(self, i: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 1, 400]):
        _, _, member = await authorize(i, 'kick_members', bot_permission='kick_members', target=member)
        case_id = await self.perform(i, member.id, 'kick', reason, lambda: member.kick(reason=audit(i, reason)))
        await i.followup.send(f'Kicked {member.mention}. Case #{case_id}.', ephemeral=True)

    @app_commands.command(description='Ban a member; existing messages are preserved.')
    async def ban(self, i: discord.Interaction, member: discord.Member, reason: app_commands.Range[str, 1, 400]):
        _, _, member = await authorize(i, 'ban_members', bot_permission='ban_members', target=member)
        case_id = await self.perform(i, member.id, 'ban', reason, lambda: i.guild.ban(member, delete_message_seconds=0, reason=audit(i, reason)))
        await i.followup.send(f'Banned {member.mention}. Case #{case_id}.', ephemeral=True)

    @app_commands.command(description='Unban a user by Discord user ID.')
    async def unban(self, i: discord.Interaction, user_id: str, reason: app_commands.Range[str, 1, 400]):
        await authorize(i, 'ban_members', bot_permission='ban_members')
        if not user_id.isascii() or not user_id.isdecimal() or not 0 < int(user_id) < 2**64:
            raise ModerationError('Provide a valid numeric Discord user ID.')
        case_id = await self.perform(i, int(user_id), 'unban', reason, lambda: i.guild.unban(discord.Object(id=int(user_id)), reason=audit(i, reason)))
        await i.followup.send(f'User unbanned. Case #{case_id}.', ephemeral=True)

    @app_commands.command(description='Delete unpinned messages among the most recent 1–100 messages.')
    async def purge(self, i: discord.Interaction, count: app_commands.Range[int, 1, 100]):
        if not isinstance(i.channel, discord.TextChannel):
            raise ModerationError('Use purge in a regular text channel.')
        _, bot, _ = await authorize(i, 'manage_messages', bot_permission='manage_messages', channel=True)
        if not i.channel.permissions_for(bot).read_message_history:
            raise ModerationError('I need Read Message History permission.')
        deleted = await i.channel.purge(limit=count, before=i.created_at, check=lambda m: not m.pinned, reason=audit(i, f'Purge up to {count} messages'))
        await i.followup.send(f'Deleted {len(deleted)} messages. Pinned messages were skipped.', ephemeral=True)

    async def channel_lock(self, i, unlocking, reason):
        if not isinstance(i.channel, discord.TextChannel):
            raise ModerationError('Use lock/unlock in a regular text channel.')
        await authorize(i, 'manage_roles', bot_permission='manage_roles', channel=True)
        async with self.locks.setdefault(i.channel.id, asyncio.Lock()):
            channel = await i.guild.fetch_channel(i.channel.id)
            rows, _, _ = self.store.query('SELECT send,threads,public,private FROM locks WHERE guild=? AND channel=?', (i.guild.id, channel.id))
            overwrite = channel.overwrites_for(i.guild.default_role)
            fields = ('send_messages', 'send_messages_in_threads', 'create_public_threads', 'create_private_threads')
            if unlocking:
                if not rows:
                    raise ModerationError('This channel has no saved lock to restore.')
                for field, value in zip(fields, rows[0]):
                    setattr(overwrite, field, None if value is None else bool(value))
            else:
                if rows:
                    raise ModerationError('This channel is already tracked as locked. Use /mod unlock first.')
                self.store.query('INSERT INTO locks VALUES(?,?,?,?,?,?)', (i.guild.id, channel.id, *(getattr(overwrite, field) for field in fields)))
                for field in fields:
                    setattr(overwrite, field, False)
            await channel.set_permissions(i.guild.default_role, overwrite=None if overwrite.is_empty() else overwrite, reason=audit(i, reason))
            if unlocking:
                self.store.query('DELETE FROM locks WHERE guild=? AND channel=?', (i.guild.id, channel.id))
            await i.followup.send('Previous @everyone settings restored.' if unlocking else 'Locked for @everyone. Explicit member/role allows and administrators can still send messages.', ephemeral=True)

    @app_commands.command(description='Deny @everyone sending messages in this channel and its threads.')
    async def lock(self, i: discord.Interaction, reason: app_commands.Range[str, 1, 400]):
        await self.channel_lock(i, False, reason)

    @app_commands.command(description='Restore the @everyone settings saved by /mod lock.')
    async def unlock(self, i: discord.Interaction, reason: app_commands.Range[str, 1, 400]):
        await self.channel_lock(i, True, reason)

    async def change_role(self, i, member, role, adding, reason):
        actor, bot, member = await authorize(i, 'manage_roles', bot_permission='manage_roles', target=member)
        roles = await i.guild.fetch_roles()
        role = discord.utils.get(roles, id=role.id)
        if role is None or role.is_default() or role.managed:
            raise ModerationError('Select a regular role, not @everyone or an integration-managed role.')
        if role >= bot.top_role or (actor.id != i.guild.owner_id and role >= actor.top_role):
            raise ModerationError('The role must be below both your highest role and my highest role.')
        if adding and actor.id != i.guild.owner_id and not actor.guild_permissions.administrator:
            if role.permissions.value & ~actor.guild_permissions.value:
                raise ModerationError('You cannot grant a role with permissions you do not have.')
        action = member.add_roles if adding else member.remove_roles
        await action(role, reason=audit(i, reason), atomic=True)
        await i.followup.send('Role added.' if adding else 'Role removed.', ephemeral=True)

    @app_commands.command(description='Assign an existing role to a member.')
    async def role_add(self, i: discord.Interaction, member: discord.Member, role: discord.Role, reason: app_commands.Range[str, 1, 400]):
        await self.change_role(i, member, role, True, reason)

    @app_commands.command(description='Remove an existing role from a member.')
    async def role_remove(self, i: discord.Interaction, member: discord.Member, role: discord.Role, reason: app_commands.Range[str, 1, 400]):
        await self.change_role(i, member, role, False, reason)
