# Legacy release directory

This directory is retained for compatibility. The canonical source is the repository root.
Build versioned archives with `python3 scripts/build_release.py` from the root.
The generated versioned archive and SHA-256 file exclude credentials and runtime data.
Use the root checkout for Git updates; the legacy copy is not an independent Git checkout.

Run `python3 scripts/sync_release.py` at the repository root to regenerate the
deployment copy, or use `--check` to detect differences before publishing.
