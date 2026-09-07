import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deployment import data_directory, validate_runtime, run_bot


class DeploymentTests(unittest.IsolatedAsyncioTestCase):
    def test_configurable_data_directory_and_write_check(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'DATA_DIR': directory}), patch('deployment.shutil.which', return_value='/usr/bin/tool'):
            validate_runtime()
            self.assertEqual(data_directory(), Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_missing_dependency_message(self):
        with patch('deployment.shutil.which', return_value=None), patch('deployment.Path.is_file', return_value=False):
            with self.assertRaisesRegex(ValueError, 'Missing ffmpeg'):
                validate_runtime()

    async def test_cancel_closes_bot(self):
        entered = asyncio.Event()
        class FakeBot:
            closed = False
            async def __aenter__(self): return self
            async def __aexit__(self, *args): self.closed = True
            async def start(self, token):
                entered.set()
                await asyncio.Event().wait()
        bot=FakeBot()
        task=asyncio.create_task(run_bot(bot, 'test-token'))
        await entered.wait()
        task.cancel()
        await task
        self.assertTrue(bot.closed)
