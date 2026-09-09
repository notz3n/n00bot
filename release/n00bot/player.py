"""Bounded, cancellable music queue with one download at a time."""

import asyncio
from collections import deque
from dataclasses import dataclass
import logging

import discord
from music import extract_audio

log = logging.getLogger('bot.music')


@dataclass
class Track:
    url: str
    requester: int
    title: str = ''
    state: str = 'waiting'


class MusicPlayer:
    def __init__(self, voice, lock, *, max_tracks=20, per_user=5):
        self.voice = voice
        self.channel_id = voice.channel.id
        self.lock = lock
        self.max_tracks = max_tracks
        self.per_user = per_user
        self.pending = deque()
        self.current = None
        self.task = None
        self.last_error = ''

    def enqueue(self, url, requester):
        tracks = list(self.pending) + ([self.current] if self.current else [])
        if len(tracks) >= self.max_tracks:
            raise ValueError(f'The queue is full ({self.max_tracks} tracks including the current track).')
        if sum(track.requester == requester for track in tracks) >= self.per_user:
            raise ValueError(f'You can have at most {self.per_user} tracks queued, including the current track.')
        self.pending.append(Track(url, requester))
        self.start()
        return len(tracks) + 1

    def start(self):
        if self.pending and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self.run(), name='music-queue')

    async def stop(self, *, clear=True):
        if clear:
            self.pending.clear()
        task = self.task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.task = None
        self.current = None
        self.voice.stop()
        if not clear:
            self.start()

    async def run(self):
        try:
            while self.pending:
                if not self.voice.is_connected() or self.voice.channel.id != self.channel_id:
                    self.pending.clear()
                    return
                track = self.current = self.pending.popleft()
                track.state = 'downloading'
                source = download = None
                started = False
                try:
                    stream, track.title, download = await extract_audio(track.url)
                    async with self.lock:
                        if not self.voice.is_connected() or self.voice.channel.id != self.channel_id:
                            self.pending.clear()
                            return
                        if not self.voice.channel.permissions_for(self.voice.guild.me).speak:
                            raise ValueError('Speak permission is missing.')
                        source = discord.FFmpegOpusAudio(stream, before_options='-nostdin', options='-vn')
                        loop = asyncio.get_running_loop()
                        done = loop.create_future()
                        owned_download = download

                        def completed(error, *, future=done, files=owned_download):
                            try:
                                files.cleanup()
                            except OSError:
                                log.exception('Could not remove completed audio download')
                            def resolve():
                                if not future.done():
                                    future.set_result(error)
                            if not loop.is_closed():
                                try:
                                    loop.call_soon_threadsafe(resolve)
                                except RuntimeError:
                                    pass  # The loop closed during final shutdown.

                        self.voice.play(source, after=completed)
                        started = True
                        source = download = None  # The audio player/callback owns these.
                        track.state = 'playing'
                    error = await done
                    if error:
                        raise ValueError('Audio playback failed. Try another video.')
                except (ValueError, OSError, discord.DiscordException, asyncio.TimeoutError) as error:
                    # Never expose extractor stderr, URLs with secrets, or raw exceptions.
                    self.last_error = f'{type(error).__name__}: a track failed; it was skipped.'
                    log.warning('Music track failed (%s); advancing queue', type(error).__name__)
                finally:
                    if started:
                        self.voice.stop()
                    if source is not None:
                        source.cleanup()
                    if download is not None:
                        download.cleanup()
                    self.current = None
        except asyncio.CancelledError:
            raise
        except Exception:
            self.pending.clear()
            self.last_error = 'Unexpected playback failure; queue cleared.'
            log.exception('Music queue stopped unexpectedly')
        finally:
            self.current = None
