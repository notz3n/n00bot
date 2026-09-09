import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace

import discord
from bot import play, stop, skip, leave, queue, nowplaying
from moderation import ModerationError
from player import MusicPlayer
from music import youtube_url
import test_voice

URL = 'https://youtu.be/abcdefghijk'


class MusicTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.i = test_voice.VoiceTests().interaction()
        channel = self.i.user.voice.channel
        channel.permissions_for.return_value.speak = True
        self.voice = Mock(channel=channel, guild=self.i.guild, disconnect=AsyncMock())
        self.voice.is_connected.return_value = True
        self.voice.is_playing.return_value = False
        self.i.guild.voice_client = self.voice
        self.completion = None
        self.played = asyncio.Event()
        def start(source, after):
            self.completion = after
            self.played.set()
        self.voice.play.side_effect = start
        self.voice.stop.side_effect = self.finish
        self.downloads = []
        async def extract(url):
            download = Mock()
            self.downloads.append(download)
            return '/tmp/audio.webm', 'Test', download
        self.extract = AsyncMock(side_effect=extract)
        for name, value in [('player.extract_audio', self.extract), ('bot.shutil.which', Mock(return_value='ffmpeg'))]:
            patcher = patch(name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        source = patch('player.discord.FFmpegOpusAudio')
        self.source = source.start()
        self.addCleanup(source.stop)
        self.addAsyncCleanup(self.cleanup_players)

    async def cleanup_players(self):
        for player in self.i.client.players.values():
            await player.stop()

    def finish(self, error=None):
        callback, self.completion = self.completion, None
        if callback:
            callback(error)

    async def started(self):
        await asyncio.wait_for(self.played.wait(), 1)
        self.played.clear()

    def test_urls(self):
        for value in ('https://youtu.be/abcdefghijk?t=12', 'https://www.youtube.com/watch?v=abcdefghijk&list=123', 'https://youtube.com/shorts/abcdefghijk'):
            self.assertEqual(youtube_url(value), 'https://www.youtube.com/watch?v=abcdefghijk')
        for value in ('file:///etc/passwd', 'https://example.com/watch?v=abcdefghijk', 'https://youtube.com/playlist?list=123', 'https://youtube.com.evil.test/watch?v=abcdefghijk'):
            with self.assertRaises(ValueError):
                youtube_url(value)

    async def test_play_and_stop_cleans_download(self):
        await play.callback(self.i, URL)
        await self.started()
        self.downloads[0].cleanup.assert_not_called()
        await stop.callback(self.i)
        self.downloads[0].cleanup.assert_called_once()
        self.assertIsNone(self.i.client.players[1].current)
        self.source.return_value.cleanup.assert_not_called()  # AudioPlayer owns it.

    async def test_fifo_queue_advances_only_on_completion(self):
        await play.callback(self.i, URL)
        await self.started()
        await play.callback(self.i, 'https://youtu.be/12345678901')
        self.extract.assert_awaited_once()
        self.finish()
        await self.started()
        self.assertEqual(self.extract.await_args.args[0], 'https://www.youtube.com/watch?v=12345678901')
        self.downloads[0].cleanup.assert_called_once()

    async def test_download_failure_skips_and_continues(self):
        download = Mock()
        self.extract.side_effect = [ValueError('Unavailable'), ('/tmp/audio.webm', 'Next', download)]
        await play.callback(self.i, URL)
        await play.callback(self.i, URL)
        with self.assertLogs('bot.music', level='WARNING'):
            await self.started()
        self.assertEqual(self.i.client.players[1].current.title, 'Next')
        self.assertIn('skipped', self.i.client.players[1].last_error)

    async def test_other_channel_rejected(self):
        other = Mock(spec=discord.VoiceChannel)
        other.id = 99
        self.i.user.voice.channel = other
        with self.assertRaises(ModerationError):
            await play.callback(self.i, URL)
        self.extract.assert_not_awaited()

    async def test_failed_play_cleans_source_and_download(self):
        self.voice.play.side_effect = ValueError('Failed')
        await play.callback(self.i, URL)
        with self.assertLogs('bot.music', level='WARNING'):
            await self.i.client.players[1].task
        self.source.return_value.cleanup.assert_called_once()
        self.downloads[0].cleanup.assert_called_once()

    async def test_ffmpeg_failure_cleans_download(self):
        self.source.side_effect = OSError('Failed')
        await play.callback(self.i, URL)
        with self.assertLogs('bot.music', level='WARNING'):
            await self.i.client.players[1].task
        self.downloads[0].cleanup.assert_called_once()

    async def test_play_autojoins(self):
        self.i.guild.voice_client = None
        self.i.user.voice.channel.connect.return_value = self.voice
        await play.callback(self.i, URL)
        await self.started()
        self.i.user.voice.channel.connect.assert_awaited_once_with(timeout=20.0, reconnect=False, self_deaf=True)

    async def test_auto_join_timeout_cleans_connection(self):
        self.i.guild.voice_client = None
        async def failed(**kwargs):
            self.i.guild.voice_client = self.voice
            raise asyncio.TimeoutError
        self.i.user.voice.channel.connect.side_effect = failed
        with self.assertRaises(ModerationError):
            await play.callback(self.i, URL)
        self.voice.disconnect.assert_awaited_once()
        self.extract.assert_not_awaited()

    async def test_queue_limits_include_current_track(self):
        await play.callback(self.i, URL)
        await self.started()
        for _ in range(4):
            await play.callback(self.i, URL)
        with self.assertRaisesRegex(ModerationError, 'at most 5'):
            await play.callback(self.i, URL)
        player = self.i.client.players[1]
        for user in range(200, 203):
            for _ in range(5):
                player.enqueue(URL, user)
        with self.assertRaisesRegex(ValueError, 'queue is full'):
            player.enqueue(URL, 300)

    async def test_skip_current_and_stop_clear_queue(self):
        await play.callback(self.i, URL)
        await self.started()
        await play.callback(self.i, URL)
        await skip.callback(self.i)
        await self.started()
        self.assertEqual(self.extract.await_count, 2)
        await play.callback(self.i, URL)
        await stop.callback(self.i)
        self.assertFalse(self.i.client.players[1].pending)
        self.assertIsNone(self.i.client.players[1].task)
        self.assertEqual(self.extract.await_count, 2)

    async def test_stop_cancels_download_without_waiting_for_voice_lock(self):
        began, cancelled = asyncio.Event(), asyncio.Event()
        async def download(url):
            began.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.extract.side_effect = download
        await play.callback(self.i, URL)
        await asyncio.wait_for(began.wait(), 1)
        await asyncio.wait_for(stop.callback(self.i), 1)
        self.assertTrue(cancelled.is_set())
        self.voice.play.assert_not_called()

    async def test_leave_clears_player(self):
        await play.callback(self.i, URL)
        await self.started()
        await play.callback(self.i, URL)
        await leave.callback(self.i)
        self.assertFalse(self.i.client.players)
        self.voice.disconnect.assert_awaited_once_with(force=True)
        self.downloads[0].cleanup.assert_called_once()

    async def test_queue_and_nowplaying_are_private(self):
        await play.callback(self.i, URL)
        await self.started()
        for command in (queue, nowplaying):
            await command.callback(self.i)
            payload = self.i.followup.send.call_args.kwargs
            self.assertTrue(payload['ephemeral'])
            self.assertIn('Test', payload['embed'].description)

    async def test_external_move_during_download_never_starts_audio(self):
        async def moved(url):
            self.voice.channel = SimpleNamespace(id=99)
            download = Mock()
            self.downloads.append(download)
            return '/tmp/audio.webm', 'Test', download
        self.extract.side_effect = moved
        await play.callback(self.i, URL)
        await self.i.client.players[1].task
        self.voice.play.assert_not_called()
        self.downloads[0].cleanup.assert_called_once()
