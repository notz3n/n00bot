"""Single-track YouTube playback helpers."""

import asyncio
import json
import logging
import re
import shutil
import sys
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


async def extract_audio(url: str) -> tuple[str, str, dict[str, str]]:
    deno = shutil.which('deno') or str(Path(sys.executable).parent / 'deno')
    cookie_args = []
    cookie_file = Path('/app/youtube-cookies.txt')
    if cookie_file.is_file():
        runtime_cookie_file = Path('/tmp/youtube-cookies-runtime.txt')
        shutil.copyfile(cookie_file, runtime_cookie_file)
        cookie_args = ['--cookies', str(runtime_cookie_file)]
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'yt_dlp', '--no-playlist', '--no-warnings',
        '--no-progress', '--dump-single-json', '--skip-download',
        '--socket-timeout', '15', '--retries', '1', '--extractor-retries', '1',
        '--js-runtimes', f'deno:{deno}', *cookie_args, '--extractor-args', 'youtube:player_client=android', '-f', 'bestaudio[ext=webm]/bestaudio/best', '--', url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    if process.returncode:
        details = stderr.decode(errors='replace').strip().splitlines()
        log_details = details[-1][:240] if details else 'no yt-dlp diagnostic output'
        log.error('yt-dlp extraction failed: %s', ' | '.join(details[-5:]) if details else log_details)
        raise ValueError(f'YouTube could not provide audio for this video ({log_details}). Try another public video or update yt-dlp.')
    data = json.loads(stdout)
    stream = data.get('url', '')
    if not stream:
        # Some yt-dlp formats expose separate streams in requested_formats.
        stream = next((item.get('url', '') for item in data.get('requested_formats', [])
                       if item.get('acodec') not in (None, 'none')), '')
    if urlparse(stream).scheme != 'https':
        raise ValueError('YouTube did not return a playable audio stream.')
    return stream, data.get('title', 'YouTube audio'), data.get('http_headers', {})
