# Changelog

## 1.0.1

- Report the semantic release version and automatic runtime source fingerprint in logs and `/ping`.
- Reconcile the standalone release copy with the canonical root source, including temporary-room placement.
- Add release-copy synchronization and drift checks.

## 1.0.0

First versioned source release of n00bot.

- Download YouTube audio with yt-dlp before local FFmpeg playback; clean temporary files after playback or failure.
- Check the configured Git origin on startup; use `--update` for a clean fast-forward, or `--offline` to skip the check.
- Mount an existing YouTube cookie file using an ignored Compose override and validate container read access.
- Preserve local configuration and data. Refuse updates over local edits.
- Build source archives from an explicit allowlist; exclude credentials, data, caches and duplicated release sources.

Downloads are limited to 100 MiB and three minutes; live streams are unsupported. Cookies must be exported manually and may expire. Docker startup validation is offline; confirm Discord login and playback in the logs.
