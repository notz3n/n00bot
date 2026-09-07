import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class StartScriptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        project = Path(__file__).resolve().parents[1]
        shutil.copy(project / 'start.sh', self.root / 'start.sh')
        shutil.copy(project / '.env.example', self.root / '.env.example')
        tools = self.root / 'tools'
        tools.mkdir()
        docker = tools / 'docker'
        docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\nif [ "$*" = "compose build" ] && [ "${FAIL_BUILD:-}" = 1 ]; then exit 1; fi\n')
        docker.chmod(0o755)
        self.env = {**os.environ, 'PATH': f'{tools}:' + os.environ['PATH'], 'CALL_LOG': str(self.root / 'calls')}
        self.config = 'DISCORD_TOKEN=secret-test-token\nDISCORD_GUILD_ID=123\nTEMP_VOICE_NAME="Room {number}"\n'
        (self.root / '.env').write_text(self.config)

    def run_script(self, *args):
        return subprocess.run(['bash', str(self.root / 'start.sh'), *args], env=self.env, cwd='/tmp', input='', capture_output=True, text=True, timeout=10)

    def test_start_preserves_config_and_runs_from_other_directory(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / 'calls').read_text()
        self.assertIn('compose build', calls)
        self.assertIn('compose run --rm --no-deps bot python bot.py --check', calls)
        self.assertIn('compose up -d --no-build', calls)
        self.assertEqual((self.root / '.env').read_text(), self.config)
        self.assertNotIn('secret-test-token', result.stdout + result.stderr)

    def test_check_does_not_start(self):
        result = self.run_script('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('compose up', (self.root / 'calls').read_text())

    def test_build_failure_does_not_start(self):
        self.env['FAIL_BUILD'] = '1'
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('compose up', (self.root / 'calls').read_text())

    def test_new_install_requires_credentials(self):
        (self.root / '.env').unlink()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Edit .env', result.stderr)
        self.assertNotIn('compose build', (self.root / 'calls').read_text())
        self.assertEqual((self.root / '.env').stat().st_mode & 0o777, 0o600)

    def test_help_makes_no_docker_calls(self):
        result = self.run_script('--help')
        self.assertEqual(result.returncode, 0)
        self.assertFalse((self.root / 'calls').exists())
