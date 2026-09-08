"""Build an allowlisted source release without credentials or data."""
import hashlib
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ['start.sh', 'bot.py', 'music.py', 'moderation.py', 'temp_voice.py',
         'deployment.py', 'Dockerfile', 'compose.yaml', 'requirements.txt',
         'requirements.lock', '.env.example', '.gitignore', '.dockerignore',
         'README.md', 'CHANGELOG.md', 'VERSION', 'scripts/build_release.py']


def build():
    version = (ROOT / 'VERSION').read_text().strip()
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    archive = output / f'n00bot-{version}.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name in FILES:
            tar.add(ROOT / name, arcname=f'n00bot-{version}/{name}')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / f'{archive.name}.sha256').write_text(f'{digest}  {archive.name}\n')
    print(archive)


if __name__ == '__main__':
    build()
