"""Startup checks and signal-aware Discord lifecycle."""

import asyncio
import logging
import os
from pathlib import Path
import shutil
import signal
import tempfile


def data_directory():
    return Path(os.getenv('DATA_DIR', str(Path(__file__).parent / 'data')))


def validate_runtime():
    for executable in ('ffmpeg', 'deno'):
        import sys
        local = Path(sys.executable).parent / executable
        if not shutil.which(executable) and not (local.is_file() and os.access(local, os.X_OK)):
            raise ValueError(f'Missing {executable}. Install dependencies or use the Docker image.')
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=directory) as probe:
        probe.write(b'write check')


async def run_bot(bot, token):
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    registered = []
    def shutdown():
        logging.getLogger('bot').info('Shutdown requested; disconnecting from Discord.')
        task.cancel()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown)
            registered.append(sig)
        except (NotImplementedError, RuntimeError):
            pass
    try:
        async with bot:
            await bot.start(token)
    except asyncio.CancelledError:
        pass
    finally:
        for sig in registered:
            loop.remove_signal_handler(sig)
