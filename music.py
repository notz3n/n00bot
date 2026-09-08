"""Single-track YouTube playback helpers."""

import asyncio
import json
import logging
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

log = logging.getLogger('bot.music')


def youtube_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
        raise ValueError('Provide a YouTube video link.')
    if parsed.hostname == 'youtu.be':
        video_id = parsed.path.strip('/')
    elif parsed.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
        if parsed.path == '/watch':
            video_id = parse_qs(parsed.query).get('v', [''])[0]
        else:
            parts = parsed.path.strip('/').split('/')
            video_id = parts[1] if len(parts) == 2 and parts[0] in ('shorts', 'live', 'embed') else ''
    else:
        video_id = ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise ValueError('Provide a single YouTube video link, not a playlist or search.')
    return f'https://www.youtube.com/watch?v={video_id}'


async def extract_audio(url: str) -> tuple[str, str, tempfile.TemporaryDirectory]:
    """Download before playback; caller owns the returned temporary directory."""
    directory = tempfile.TemporaryDirectory(prefix='n00bot-audio-')
    try:
        deno = shutil.which('deno') or str(Path(sys.executable).parent / 'deno')
        cookie_args = []
        cookie_file = Path('/app/youtube-cookies.txt')
        if cookie_file.is_file():
            runtime_cookie_file = Path(directory.name) / 'cookies.txt'
            shutil.copyfile(cookie_file, runtime_cookie_file)
            cookie_args = ['--cookies', str(runtime_cookie_file)]
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'yt_dlp', '--no-playlist', '--no-progress',
            '--no-simulate', '--print', 'after_move:%(title)j',
            '--max-filesize', '100M', '--match-filter', '!is_live',
            '--socket-timeout', '15', '--retries', '1', '--extractor-retries', '1',
            '--js-runtimes', f'deno:{deno}', *cookie_args,
            '-f', 'bestaudio[ext=webm]/bestaudio/best',
            '-o', str(Path(directory.name) / 'audio.%(ext)s'), '--', url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
        if process.returncode:
            log.error('yt-dlp download failed (exit %s)', process.returncode)
            raise ValueError('YouTube audio download failed. Check cookies or try another public video.')
        files = [p for p in Path(directory.name).glob('audio.*')
                 if p.suffix not in ('.part', '.ytdl') and p.is_file()]
        if len(files) != 1 or not files[0].stat().st_size or not stdout.strip():
            raise ValueError('No audio downloaded. Live streams and files over 100 MiB are unsupported.')
        title = json.loads(stdout.decode().strip().splitlines()[-1])
        return str(files[0]), title, directory
    except BaseException:
        directory.cleanup()
        raise
