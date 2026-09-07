"""Create voice rooms from a lobby, tracking only rooms this bot owns."""

import asyncio
import json
import logging
import re
from pathlib import Path

import discord

log = logging.getLogger('bot.temp_voice')


class TempVoice:
    def __init__(self, lobby_id: int | None, template: str, path: Path, status_template: str = ''):
        self.lobby_id = lobby_id
        self.template = template
        self.status_template = status_template
        self.path = path
        self.lock = asyncio.Lock()
        state = json.loads(path.read_text()) if path.exists() else []
        # Accept the original room-ID list when upgrading existing installations.
        self.rooms = set(state if isinstance(state, list) else state['rooms'])
        self.labels = {} if isinstance(state, list) else state.get('labels', {})
        if not all(isinstance(room, int) and room > 0 for room in self.rooms):
            raise ValueError('Invalid temporary voice room state file')

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'rooms': sorted(self.rooms), 'labels': self.labels}))
        temporary.replace(self.path)

    async def renumber(self, guild):
        for number, room_id in enumerate(sorted(self.rooms), 1):
            channel = guild.get_channel(room_id)
            if not isinstance(channel, discord.VoiceChannel) or room_id == self.lobby_id:
                continue
            label = self.labels.get(str(room_id))
            if label is None:
                # Recover template values from old names where possible.
                pattern = self.template if '{number}' in self.template else self.template + ' {number}'
                regex = ''.join(r'(\d+)' if part == '{number}' else '(.*?)' if part in ('{user}', '{channel}') else re.escape(part)
                                for part in re.split(r'(\{user\}|\{channel\}|\{number\})', pattern))
                match = re.fullmatch(regex, channel.name)
                label = {'name': re.sub(r'\s+\d+$', '', channel.name) + ' {number}', 'status': ''}
                if match:
                    tokens = re.findall(r'\{(?:user|channel|number)\}', pattern)
                    values = dict(zip(tokens, match.groups()))
                    number_group = tokens.index('{number}') + 1
                    start, end = match.span(number_group)
                    label['name'] = channel.name[:start] + '{number}' + channel.name[end:]
                    if all(token in values for token in re.findall(r'\{(?:user|channel)\}', self.status_template)):
                        label['status'] = self.status_template
                        for token in ('{user}', '{channel}'):
                            if token in values:
                                label['status'] = label['status'].replace(token, values[token])
                self.labels[str(room_id)] = label
            name = label['name'].replace('{number}', str(number))[:100]
            try:
                if channel.name != name:
                    await channel.edit(name=name, reason='Renumber active temporary rooms')
                if label['status']:
                    await channel.edit(status=label['status'].replace('{number}', str(number))[:500], reason='Renumber active temporary rooms')
            except discord.HTTPException:
                log.exception('Could not renumber temporary room %s', room_id)
        self.save()

    async def delete_empty(self, channel):
        if channel.id not in self.rooms or channel.id == self.lobby_id or channel.voice_states:
            return
        try:
            await channel.delete(reason='Temporary voice channel is empty')
        except discord.NotFound:
            pass
        except discord.HTTPException:
            log.exception('Could not delete temporary voice channel %s', channel.id)
            return
        self.rooms.remove(channel.id)
        self.labels.pop(str(channel.id), None)
        self.save()
        await self.renumber(channel.guild)

    async def recover(self, guild):
        """Run only after the guild's gateway state is ready."""
        if guild.unavailable:
            return
        async with self.lock:
            for room_id in list(self.rooms):
                channel = guild.get_channel(room_id)
                if channel is None:
                    try:
                        await guild.fetch_channel(room_id)
                    except discord.NotFound:
                        self.rooms.discard(room_id)
                        self.labels.pop(str(room_id), None)
                    except discord.HTTPException:
                        pass
                    continue
                if isinstance(channel, discord.VoiceChannel):
                    await self.delete_empty(channel)
            await self.renumber(guild)

    async def update(self, member, before, after):
        if before.channel == after.channel:
            return
        async with self.lock:
            if before.channel is not None:
                current = member.guild.get_channel(before.channel.id)
                if current is not None:
                    await self.delete_empty(current)
            lobby = after.channel
            if member.bot or lobby is None or lobby.id != self.lobby_id:
                return
            if not isinstance(lobby, discord.VoiceChannel):
                return
            if member.voice is None or member.voice.channel != lobby:
                return
            permissions = lobby.permissions_for(member.guild.me)
            if not all((permissions.manage_channels, permissions.move_members, permissions.view_channel, permissions.connect)):
                log.error('Lobby %s requires Manage Channels, Move Members, View Channel and Connect', lobby.id)
                return
            number = len(self.rooms) + 1
            name = self.template.replace('{number}', str(number)).replace('{user}', member.display_name).replace('{channel}', lobby.name).strip()
            if '{number}' not in self.template:
                suffix = f' {number}'
                name = (name or 'Temporary voice')[:100 - len(suffix)] + suffix
            room = await lobby.clone(
                name=name[:100],
                category=lobby.category,
                reason='Join-to-create voice room',
            )
            # Clone preserves the lobby's category, then place the new room
            # immediately below the lobby in that category. Discord may shift
            # older temporary rooms down as new ones are added.
            try:
                # Discord's channel positions are ordered from the bottom of
                # the category upward, so the slot immediately below the
                # lobby is one position lower than the lobby's current value
                # after the clone has been inserted.
                await room.edit(position=max(0, lobby.position - 1), reason='Place temporary room below lobby')
            except discord.HTTPException:
                log.warning('Could not place room %s directly below lobby %s', room.id, lobby.id, exc_info=True)
            self.rooms.add(room.id)
            name_pattern = self.template.replace('{user}', member.display_name).replace('{channel}', lobby.name).strip()
            if '{number}' not in self.template:
                name_pattern = (name_pattern or 'Temporary voice')[:100 - len(f' {number}')] + ' {number}'
            self.labels[str(room.id)] = {
                'name': name_pattern,
                'status': self.status_template.replace('{user}', member.display_name).replace('{channel}', lobby.name).strip(),
            }
            try:
                self.save()
            except OSError:
                # Do not move anyone into a room we cannot persist ownership of.
                await room.delete(reason='Unable to save temporary room ownership')
                self.rooms.remove(room.id)
                self.labels.pop(str(room.id), None)
                raise
            try:
                if self.status_template.strip():
                    status = self.status_template.replace('{number}', str(number)).replace('{user}', member.display_name).replace('{channel}', lobby.name).strip()[:500]
                    try:
                        await room.edit(status=status, reason='Temporary voice room status')
                    except discord.HTTPException:
                        log.warning('Could not set status for room %s. Check Set Voice Channel Status and Manage Channels permissions.', room.id, exc_info=True)
                # The member may have left while Discord created the channel.
                if member.voice is None or member.voice.channel != lobby:
                    await self.delete_empty(room)
                    return
                await member.move_to(room, reason='Join-to-create voice room')
            except discord.HTTPException:
                await self.delete_empty(member.guild.get_channel(room.id) or room)
                raise
