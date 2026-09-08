import shutil
import tempfile
import unittest
from pathlib import Path
from version import get_version, RUNTIME_FILES


class VersionTests(unittest.TestCase):
    def test_identity_survives_copy_and_changes_with_code(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for name in RUNTIME_FILES:
                shutil.copy2(root / name, target / name)
            original = get_version(root)
            self.assertEqual(original, get_version(target))
            with (target / 'music.py').open('a') as stream:
                stream.write('\n# updated code\n')
            self.assertNotEqual(original, get_version(target))

    def test_release_copy_matches(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(get_version(root), get_version(root / 'release' / 'n00bot'))
