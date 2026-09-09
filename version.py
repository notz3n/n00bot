"""Stable release number plus an automatic fingerprint of the running code."""
import hashlib
from pathlib import Path

RUNTIME_FILES = (
    'VERSION', 'version.py', 'bot.py', 'music.py', 'moderation.py',
    'temp_voice.py', 'room_controls.py', 'player.py', 'diagnostics.py', 'deployment.py', 'requirements.lock',
)


def get_version(root: Path | None = None) -> str:
    root = root if root is not None else Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in RUNTIME_FILES:
        content = (root / name).read_bytes()
        digest.update(name.encode() + b'\0' + str(len(content)).encode() + b'\0' + content)
    return f"{(root / 'VERSION').read_text().strip()}+src.{digest.hexdigest()[:12]}"


if __name__ == '__main__':
    print(get_version())
