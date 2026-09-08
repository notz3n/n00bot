"""Regenerate the compatibility deployment copy from canonical source."""
import argparse
from pathlib import Path
import shutil
from build_release import FILES, ROOT


def sync(check=False):
    destination = ROOT / 'release' / 'n00bot'
    changed = []
    for name in FILES:
        source, target = ROOT / name, destination / name
        if not target.is_file() or source.read_bytes() != target.read_bytes():
            changed.append(name)
            if not check:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    if changed:
        print(('Out of sync: ' if check else 'Synchronized: ') + ', '.join(changed))
    return bool(changed)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    raise SystemExit(1 if sync(args.check) and args.check else 0)
