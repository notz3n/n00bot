# Changelog

## 1.1.0

- Disconnect after a configurable alone timeout (default 120 seconds); cancel downloads and clear music on disconnect.
- Retry temporary-room cleanup and numbering every 30 seconds, batch departures, and skip unchanged channel edits.
- Persist room owners and provide `/room rename`, `limit`, `lock`, `unlock`, `transfer`, and `claim`.
- Add a FIFO music queue, automatic joining, `/queue`, `/nowplaying`, and `/skip`. Limit the queue to 20 tracks and five per member, including the current track.
- Make `/stop` cancel downloads and clear the queue. Skip failed tracks and release temporary audio files; queues reset on restart.
- Add searchable `/mod case` and `/mod history`, migrate existing warnings, preserve removal history, and record explicit moderation outcomes.
- Add private `/health` diagnostics for staff with Manage Server, including storage and permissions checks and sanitized recent failures.
- Include new runtime modules in Docker, runtime fingerprints, and source releases.

## 1.0.2

- Append temporary rooms below the lobby in ascending room-number order.
- Restore matching channel order when rooms are renumbered after deletion or restart.

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
