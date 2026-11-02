"""Owner controls for tracked temporary voice rooms."""

import discord
from discord import app_commands
from moderation import ModerationError


class RoomControls(app_commands.Group):
    def __init__(self, manager_for):
        super().__init__(name='room', description='Manage your temporary voice room', guild_only=True)
        self.manager_for = manager_for

    def get_manager(self, i):
        if i.guild is None or i.guild.me is None:
            raise ModerationError('Use this command in a server where the bot is installed.')
        return self.manager_for(i.guild.id)

    @app_commands.command(description='List this server’s parent voice lobbies.')
    async def parents(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True, thinking=True)
        manager = self.get_manager(i)
        visible = []
        hidden = 0
        for parent_id in sorted(manager.lobby_ids):
            channel = i.guild.get_channel(parent_id)
            if channel is not None and channel.permissions_for(i.user).view_channel:
                visible.append(f'<#{parent_id}>')
            else:
                hidden += 1
        text = '\n'.join(visible) or 'No visible parent lobbies configured.'
        if hidden:
            text += f'\n{hidden} other parent(s) unavailable or hidden.'
        # Discord allows up to 500 channels per server; split long lists.
        for start in range(0, len(text), 1900):
            await i.followup.send(text[start:start + 1900], ephemeral=True)

    @app_commands.command(description='Remove a parent lobby; existing temporary rooms still clean up.')
    @app_commands.describe(channel='Parent voice channel to disable in this server.')
    async def removeparent(self, i: discord.Interaction, channel: discord.VoiceChannel):
        await i.response.defer(ephemeral=True, thinking=True)
        manager = self.get_manager(i)
        actor = await i.guild.fetch_member(i.user.id)
        if not actor.guild_permissions.manage_channels:
            raise ModerationError('You need Manage Channels to remove a parent lobby.')
        if channel.guild.id != i.guild.id:
            raise ModerationError('Select a voice channel in this server.')
        async with manager.lock:
            if channel.id not in manager.lobby_ids:
                raise ModerationError('That channel is not a parent lobby.')
            manager.lobby_ids.remove(channel.id)
            try:
                manager.save()
            except OSError:
                manager.lobby_ids.add(channel.id)
                raise
        await i.followup.send('Parent lobby removed. Existing temporary rooms remain managed until empty.', ephemeral=True)

    @app_commands.command(description='Add a parent voice lobby in this server; existing parents are kept.')
    @app_commands.describe(channel='Voice channel members join to create a temporary room.')
    async def setparent(self, i: discord.Interaction, channel: discord.VoiceChannel):
        await i.response.defer(ephemeral=True, thinking=True)
        manager = self.get_manager(i)
        actor = await i.guild.fetch_member(i.user.id)
        if not actor.guild_permissions.manage_channels:
            raise ModerationError('You need Manage Channels to set the parent lobby.')
        channel = await i.guild.fetch_channel(channel.id)
        if not isinstance(channel, discord.VoiceChannel) or channel.guild.id != i.guild.id:
            raise ModerationError('Select a voice channel in this server.')
        bot = await i.guild.fetch_member(i.guild.me.id)
        permissions = channel.permissions_for(bot)
        if not all((permissions.manage_channels, permissions.move_members,
                    permissions.view_channel, permissions.connect)):
            raise ModerationError('I need Manage Channels, Move Members, View Channel and Connect in the parent lobby.')
        async with manager.lock:
            if channel.id in manager.rooms:
                raise ModerationError('Choose a permanent voice channel, not a temporary room.')
            previous = manager.lobby_ids.copy()
            manager.lobby_ids.add(channel.id)
            try:
                manager.save()
            except OSError:
                manager.lobby_ids = previous
                raise
        await i.followup.send(
            f'Parent lobby added: {channel.mention}. Join it to create a temporary room '
            'in its category. Other parents are unchanged. Saved for this server across restarts.', ephemeral=True)

    async def context(self, i, *, claim=False):
        manager = self.get_manager(i)
        actor = await i.guild.fetch_member(i.user.id)
        state = actor.voice
        if state is None or state.channel is None or state.channel.id not in manager.rooms:
            raise ModerationError('Join a temporary room managed by this bot first.')
        channel = await i.guild.fetch_channel(state.channel.id)
        if not isinstance(channel, discord.VoiceChannel) or channel.id in manager.lobby_ids:
            raise ModerationError('Select a temporary voice room.')
        staff = actor.guild_permissions.manage_channels
        owner = manager.owners.get(str(channel.id))
        if not claim and owner != actor.id and not staff:
            raise ModerationError('Only the room owner or staff with Manage Channels can do that.')
        bot = await i.guild.fetch_member(i.guild.me.id)
        if not channel.permissions_for(bot).manage_channels:
            raise ModerationError('I need Manage Channels in this room.')
        return channel, actor, bot

    async def apply_lock(self, channel, owner, bot, *, unlocking=False):
        """Restore only connect fields; submit all overwrite changes in one edit."""
        manager = self.manager_for(channel.guild.id)
        key = str(channel.id)
        saved = manager.room_locks.get(key)
        if unlocking and saved is None:
            raise ModerationError('This room has no saved lock to restore.')
        permissions = channel.permissions_for(bot)
        if not permissions.manage_roles:
            raise ModerationError('I need Manage Roles in this room to edit access permissions.')
        overwrites = channel.overwrites
        if saved is not None:
            for target, overwrite in list(overwrites.items()):
                if str(target.id) in saved:
                    overwrite.connect = saved[str(target.id)]
                    if overwrite.is_empty():
                        del overwrites[target]
        snapshot = {}
        if not unlocking:
            for target, value in ((channel.guild.default_role, False), (owner, True), (bot, True)):
                overwrite = overwrites.get(target, discord.PermissionOverwrite())
                snapshot[str(target.id)] = overwrite.connect
                overwrite.connect = value
                overwrites[target] = overwrite
        # Save the restore point before the external action. On failure /unlock
        # is safe, including after a restart or an ambiguous network response.
        manager.room_locks[key] = saved if saved is not None else snapshot
        manager.save()
        await channel.edit(overwrites=overwrites, reason='Temporary room access control')
        if unlocking:
            manager.room_locks.pop(key, None)
        else:
            manager.room_locks[key] = snapshot
        manager.save()

    @app_commands.command(description='Rename your room; its active-room number is kept.')
    async def rename(self, i: discord.Interaction, name: app_commands.Range[str, 1, 90]):
        await i.response.defer(ephemeral=True, thinking=True)
        manager = self.get_manager(i)
        async with manager.lock:
            channel, _, _ = await self.context(i)
            name = name.strip()
            if not name:
                raise ModerationError('Provide a nonempty room name.')
            label = manager.labels.setdefault(str(channel.id), {'name': 'Room {number}', 'status': ''})
            label['custom_name'] = name
            manager.save()
            manager.schedule_reconcile()
            await manager.renumber(i.guild)
            await i.followup.send('Room name saved. Its number stays automatic.', ephemeral=True)

    @app_commands.command(description='Set your room’s member limit; 0 means unlimited.')
    async def limit(self, i: discord.Interaction, members: app_commands.Range[int, 0, 99]):
        await i.response.defer(ephemeral=True, thinking=True)
        manager = self.get_manager(i)
        async with manager.lock:
            channel, _, _ = await self.context(i)
            await channel.edit(user_limit=members, reason='Temporary room owner changed capacity')
            await i.followup.send(f'Room limit: {members or "unlimited"}.', ephemeral=True)

    @app_commands.command(description='Lock new joins through @everyone; owner and bot retain access.')
    async def lock(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True, thinking=True)
        manager = self.get_manager(i)
        async with manager.lock:
            channel, _, bot = await self.context(i)
            if str(channel.id) in manager.room_locks:
                raise ModerationError('This room has a saved lock. Use /room unlock first.')
            owner_id = manager.owners.get(str(channel.id))
            if owner_id is None:
                raise ModerationError('Claim this room before locking it.')
            owner = await i.guild.fetch_member(owner_id)
            await self.apply_lock(channel, owner, bot)
            await i.followup.send('Locked for @everyone. Owner and bot retain access; explicit role/member allows and administrators may still join.', ephemeral=True)

    @app_commands.command(description='Restore the access settings saved by /room lock.')
    async def unlock(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True, thinking=True)
        manager = self.get_manager(i)
        async with manager.lock:
            channel, actor, bot = await self.context(i)
            await self.apply_lock(channel, actor, bot, unlocking=True)
            await i.followup.send('Previous room access settings restored.', ephemeral=True)

    @app_commands.command(description='Transfer ownership to a human currently in your room.')
    async def transfer(self, i: discord.Interaction, member: discord.Member):
        await i.response.defer(ephemeral=True, thinking=True)
        manager = self.get_manager(i)
        async with manager.lock:
            channel, _, bot = await self.context(i)
            target = await i.guild.fetch_member(member.id)
            if target.bot or target.id not in channel.voice_states:
                raise ModerationError('Choose a human currently in this room.')
            # Unlock before changing ownership, avoiding stale owner exceptions.
            if str(channel.id) in manager.room_locks:
                await self.apply_lock(channel, target, bot, unlocking=True)
            manager.owners[str(channel.id)] = target.id
            manager.save()
            await i.followup.send(f'Ownership transferred to {target.mention}. Any room lock was restored; the new owner can lock it again.', ephemeral=True)

    @app_commands.command(description='Claim a room whose owner has left, including rooms from older versions.')
    async def claim(self, i: discord.Interaction):
        await i.response.defer(ephemeral=True, thinking=True)
        manager = self.get_manager(i)
        async with manager.lock:
            channel, actor, bot = await self.context(i, claim=True)
            owner = manager.owners.get(str(channel.id))
            if owner in channel.voice_states and owner != actor.id:
                raise ModerationError('The owner is still in this room. Ask them to transfer ownership.')
            if str(channel.id) in manager.room_locks:
                await self.apply_lock(channel, actor, bot, unlocking=True)
            manager.owners[str(channel.id)] = actor.id
            manager.save()
            await i.followup.send('You now own this room. Any previous room lock was restored.', ephemeral=True)
