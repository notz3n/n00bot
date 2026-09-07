"""Single-track YouTube playback helpers."""

import asyncio
import json
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse


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


async def extract_audio(url: str) -> tuple[str, str]:
    deno = shutil.which('deno') or str(Path(sys.executable).parent / 'deno')
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'yt_dlp', '--no-playlist', '--no-warnings',
        '--no-progress', '--dump-single-json', '--skip-download',
        '--socket-timeout', '15', '--retries', '1', '--extractor-retries', '1',
        '--js-runtimes', f'deno:{deno}', '-f', 'bestaudio/best', '--', url,
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
        raise ValueError('YouTube could not provide audio for this video. Try another public video, or update yt-dlp.')
    data = json.loads(stdout)
    stream = data.get('url', '')
    if urlparse(stream).scheme != 'https':
        raise ValueError('YouTube did not return a playable audio stream.')
    return stream, data.get('title', 'YouTube audio')
