import os
import pty
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

    def interactive(self, answer):
        master, slave = pty.openpty()
        try:
            process = subprocess.Popen(['bash', str(self.root / 'start.sh'), '--check'], env=self.env,
                                       stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            os.write(master, answer.encode())
            stdout, stderr = process.communicate(timeout=10)
            return process.returncode, stdout, stderr
        finally:
            os.close(master)
            os.close(slave)

    def test_interactive_setup_adds_missing_lobby(self):
        code, _, stderr = self.interactive('456789\n')
        self.assertEqual(code, 0, stderr)
        self.assertIn('TEMP_VOICE_LOBBY_ID=456789\n', (self.root / '.env').read_text())
        self.assertIn('DISCORD_TOKEN=secret-test-token', (self.root / '.env').read_text())

    def test_blank_lobby_is_optional(self):
        code, _, stderr = self.interactive('\n')
        self.assertEqual(code, 0, stderr)
        self.assertIn('TEMP_VOICE_LOBBY_ID=\n', (self.root / '.env').read_text())

    def test_invalid_lobby_does_not_build(self):
        code, _, stderr = self.interactive('not-an-id\n')
        self.assertNotEqual(code, 0)
        self.assertIn('numeric or blank', stderr)
        self.assertNotIn('compose build', (self.root / 'calls').read_text())

    def test_configured_lobby_is_preserved(self):
        (self.root / '.env').write_text(self.config + 'TEMP_VOICE_LOBBY_ID=456789\n')
        code, _, stderr = self.interactive('\n')
        self.assertEqual(code, 0, stderr)
        self.assertNotIn('Temporary voice lobby channel ID', stderr)
        self.assertIn('TEMP_VOICE_LOBBY_ID=456789', (self.root / '.env').read_text())

    def test_cookie_directory_rejected(self):
        (self.root / 'youtube-cookies.txt').mkdir()
        result = self.run_script('--offline')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('regular file', result.stderr)
        self.assertNotIn('compose build', (self.root / 'calls').read_text())

    def test_cookie_file_generates_persistent_override(self):
        (self.root / 'youtube-cookies.txt').write_text('test-cookie')
        result = self.run_script('--offline')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('create_host_path: false', (self.root / 'compose.override.yaml').read_text())
        self.assertIn('entrypoint python', (self.root / 'calls').read_text())

    def test_update_archive_refused(self):
        result = self.run_script('--update')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('cloned repository', result.stderr)
        self.assertFalse((self.root / 'calls').exists())

    def test_update_refuses_dirty_checkout(self):
        git = self.root / 'tools' / 'git'
        git.write_text('''#!/bin/sh
case "$1" in
symbolic-ref) echo main;;
rev-parse) if [ "$2" = HEAD ]; then echo old; else echo new; fi;;
status) echo ' M music.py';;
merge) exit 99;;
esac
''')
        git.chmod(0o755)
        (self.root / '.git').mkdir()
        result = self.run_script('--update')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('nothing was overwritten', result.stderr)
        self.assertFalse((self.root / 'calls').exists())
