import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace

from bot import play, stop
from music import youtube_url
import test_voice


class MusicTests(unittest.IsolatedAsyncioTestCase):
    def interaction(self):
        interaction = test_voice.VoiceTests().interaction()
        channel = interaction.user.voice.channel
        channel.permissions_for.return_value.speak = True
        voice = Mock(channel=channel)
        voice.is_connected.return_value = True
        voice.is_playing.return_value = False
        voice.is_paused.return_value = False
        interaction.guild.voice_client = voice
        return interaction

    def test_urls(self):
        for value in ('https://youtu.be/abcdefghijk?t=12', 'https://www.youtube.com/watch?v=abcdefghijk&list=123', 'https://youtube.com/shorts/abcdefghijk'):
            self.assertEqual(youtube_url(value), 'https://www.youtube.com/watch?v=abcdefghijk')
        for value in ('file:///etc/passwd', 'https://example.com/watch?v=abcdefghijk', 'https://youtube.com/playlist?list=123', 'https://youtube.com.evil.test/watch?v=abcdefghijk'):
            with self.assertRaises(ValueError):
                youtube_url(value)

    async def test_play_and_stop(self):
        interaction = self.interaction()
        with patch('bot.shutil.which', return_value='/usr/bin/ffmpeg'), patch('bot.extract_audio', new_callable=AsyncMock, return_value=('https://audio.example/test', 'Test')), patch('bot.discord.FFmpegOpusAudio') as source:
            await play.callback(interaction, 'https://youtu.be/abcdefghijk')
            interaction.guild.voice_client.play.assert_called_once()
            source.return_value.cleanup.assert_not_called()
        await stop.callback(interaction)
        interaction.guild.voice_client.stop.assert_called_once()

    async def test_extraction_failure(self):
        interaction = self.interaction()
        with patch('bot.shutil.which', return_value='ffmpeg'), patch('bot.extract_audio', new_callable=AsyncMock, side_effect=ValueError('Unavailable')):
            await play.callback(interaction, 'https://youtu.be/abcdefghijk')
        interaction.guild.voice_client.play.assert_not_called()
        self.assertEqual(interaction.followup.send.call_args.args[0], 'Unavailable')

    async def test_no_overlapping_playback(self):
        interaction = self.interaction()
        interaction.guild.voice_client.is_playing.return_value = True
        with patch('bot.shutil.which', return_value='ffmpeg'), patch('bot.extract_audio', new_callable=AsyncMock) as extract:
            await play.callback(interaction, 'https://youtu.be/abcdefghijk')
            extract.assert_not_awaited()

    async def test_other_channel_rejected(self):
        interaction = self.interaction()
        interaction.user.voice = SimpleNamespace(channel=SimpleNamespace(id=99))
        with patch('bot.shutil.which', return_value='ffmpeg'), patch('bot.extract_audio', new_callable=AsyncMock) as extract:
            await play.callback(interaction, 'https://youtu.be/abcdefghijk')
            extract.assert_not_awaited()

    async def test_failed_play_cleans_source(self):
        interaction = self.interaction()
        interaction.guild.voice_client.play.side_effect = ValueError('Failed')
        with patch('bot.shutil.which', return_value='ffmpeg'), patch('bot.extract_audio', new_callable=AsyncMock, return_value=('https://audio.example/test', 'Test')), patch('bot.discord.FFmpegOpusAudio') as source:
            await play.callback(interaction, 'https://youtu.be/abcdefghijk')
            source.return_value.cleanup.assert_called_once()
