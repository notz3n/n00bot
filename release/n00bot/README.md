# n00bot

A Discord bot for temporary voice rooms, YouTube music, and server moderation. Runs in one Discord server using Python and discord.py, with Docker Compose for deployment.

**Current release: 1.1.1** · [Changelog](CHANGELOG.md)

- **Voice rooms:** join-to-create channels, persistent owners, room controls, automatic numbering, and cleanup recovery.
- **Music:** automatic joining, a bounded queue, skip/stop controls, and disconnect when alone.
- **Moderation:** warnings, timeouts, bans, searchable case history, channel controls, and role assignment.
- **Help and diagnostics:** private `/help [topic]` guides and staff-only `/health` checks.

[Quick start](#quick-start) · [Configuration](#configuration) · [Commands](#commands) · [Voice rooms](#temporary-voice-rooms) · [Operations](#operations) · [Troubleshooting](#troubleshooting) · [Development](#development-and-releases)

## Quick start

### 1. Set up Discord

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications) and obtain its bot token. Keep the token in your local `.env`.
2. Under **Installation → Guild Install**, select `bot` and `applications.commands`, then install the bot in your server.
3. Enable **User Settings → Advanced → Developer Mode** in Discord. Right-click your server and copy its ID for `DISCORD_GUILD_ID`.
4. Leave **Interactions Endpoint URL** empty. The bot uses the Discord gateway; privileged intents are not required.

Grant only the bot permissions needed for your features:

| Feature | Bot permissions |
| --- | --- |
| Join voice | View Channel, Connect |
| Music | View Channel, Connect, Speak |
| Create temporary rooms | View Channel, Connect, Manage Channels, Move Members |
| Room lock/unlock | Manage Channels, Manage Roles |
| Room status | Set Voice Channel Status, Manage Channels |
| Moderation | See the [moderation table](#moderation) |

Channel and category overrides apply. Place the bot's role above the members and roles it must manage. Administrator is not required.

### 2. Start the bot

From a clone or unpacked release on Arch/CachyOS, Ubuntu, or Debian:

```sh
bash start.sh
```

The script installs missing Docker tools, starts Docker, prepares `.env`, prompts privately for credentials and an optional voice lobby, builds the image, checks configuration, and starts the bot. Existing `.env` and `data/` are preserved. It also works from fish.

| Option | Behavior |
| --- | --- |
| `--check` | Install/build and validate without connecting to Discord |
| `--update` | Fetch and fast-forward the current branch, then rebuild and start; requires a clean Git checkout |
| `--offline` | Skip the Git update check; dependency installation/build may still need internet |
| `--help` | Show script options |

Git clones check for updates on normal runs but only apply them with `--update`. Git authentication is required for private repositories. Noninteractive runs need a configured `.env` in advance.

Stop any separately running copy first. **Run only one instance per bot token and data directory.** Wait for `Online as ...`, the runtime version, and `cached=True available=True bot_member=True` in the logs, then try `/ping` and `/help`.

<details>
<summary>Manual Docker Compose setup</summary>

Install Docker Engine and Compose, then run from the project directory:

```sh
# New installations only; preserve an existing .env.
cp -n .env.example .env
mkdir -p data
```

Edit `.env` using the table below. Set `BOT_UID` and `BOT_GID` to the host data owner's IDs (`id -u` and `id -g`), and keep `.env` private, for example with `chmod 600 .env`.

```sh
docker compose build
docker compose run --rm --no-deps bot python bot.py --check
docker compose up -d
docker compose logs --tail=100 -f bot
```

The check validates configuration, dependencies, persistent state, and storage without connecting to Discord. Token validity and server permissions are checked online. Ctrl+C exits log viewing without stopping the container.

The setup script uses the data owner's UID/GID for its run; later manual Compose commands use the values in `.env`. On other Linux distributions, install Docker and its plugins manually. Existing Ubuntu/Debian Docker installations may need the official Docker repository configured for missing plugins. The script does not remove conflicting Docker packages. On Arch, update the system normally if package downloads are stale.

</details>

## Configuration

Start with `.env.example`. Restart local processes or recreate Docker containers after changing `.env`.

| Variable | Purpose | Default |
| --- | --- | --- |
| `DISCORD_TOKEN` | Bot token; required | None |
| `DISCORD_GUILD_ID` | Server receiving slash commands; required | None |
| `TEMP_VOICE_LOBBY_ID` | Join-to-create lobby channel ID | Blank: disabled |
| `TEMP_VOICE_NAME` | New room name template | `{user}'s room` |
| `TEMP_VOICE_STATUS` | New room status template | Blank: unset |
| `VOICE_ALONE_TIMEOUT` | Seconds alone before disconnecting; 0 disables, maximum 86400 | `120` |
| `DATA_DIR` | Persistent data directory | Local `data/`; Docker uses `/app/data` |
| `BOT_UID`, `BOT_GID` | Docker user/group; match the host data owner | `1000`, `1000` |

### YouTube cookies

If YouTube requires authentication, place a nonempty Netscape-format `youtube-cookies.txt` in the project root before running `start.sh`. Cookie export requires your account access and is not automated.

Setup creates an ignored `compose.override.yaml` mount at `/app/youtube-cookies.txt`. Existing custom overrides are preserved and must supply the mount themselves. The cookie file must be readable by the container user; setup rejects a directory at that path. Cookie validity cannot be checked offline, and cookies may expire. Never commit the file to GitHub.

## Commands

`/help` shows an overview. Select `/help topic` for **Voice & music**, **Temporary rooms**, **Moderation**, or **Staff diagnostics**. All help replies are private. Moderation commands are filtered by your role permissions and current-channel overrides; Integrations restrictions and bot permissions can further limit access. Room commands remain visible with their ownership requirements explained.

In the tables below, `[brackets]` mark optional inputs.

### General and diagnostics

| Command | Behavior | Access |
| --- | --- | --- |
| `/ping` | Show gateway latency and runtime version | Everyone |
| `/help [topic]` | Browse commands, examples, and access requirements | Everyone |
| `/health` | Private voice, queue, storage, permission, and recent-failure checks | Manage Server |

Health checks include data-directory write access and SQLite integrity. The five most recent failure summaries show UTC time, subsystem, and severity or exception class; they reset on restart. Credentials, configuration values, and raw exceptions are not shown. Target hierarchy and channel overrides still apply to individual actions.

### Voice and music

| Command | Behavior |
| --- | --- |
| `/join [channel]` | Join your voice channel, or select one explicitly |
| `/leave` | Disconnect and clear the queue |
| `/play url` | Join if disconnected and queue one YouTube video |
| `/queue` | Privately show current and waiting tracks |
| `/nowplaying` | Privately show the current track or download status |
| `/skip` | Cancel the current track/download and advance |
| `/stop` | Cancel playback/downloads, clear the queue, and stay connected |

Join a regular voice channel, then use `/play` with a YouTube video link. No staff role is needed. Music controls require you to be in the bot's channel; Move Members also permits `/leave` from elsewhere. `/join` and `/play` never move the bot from another connected channel. Stage channels are unsupported.

- **Queue limits:** 20 tracks total, five per member, including the current download or playback. Queues reset on stop, disconnect, a move of the bot, or restart.
- **Downloads:** one at a time, with a 100 MiB cap and a three-minute download deadline. This is not a song-length limit. Temporary files are removed after playback or failure.
- **Supported links:** individual YouTube videos. A video link with playlist parameters plays only that video; playlist queues and live streams are unsupported. Private/restricted videos and some network responses may prevent playback.
- **Failures:** unsuccessful tracks are skipped. `/queue` and `/nowplaying` show the most recent music failure.
- **Alone timeout:** by default, the bot clears music and disconnects after two minutes as the only occupant. Checks run about every five seconds; returning occupants reset the timer. Other bots count as occupants. The bot joins self-deafened and does not record audio.

### Temporary room controls

Use these commands inside a tracked temporary room. The creator owns it; staff with Manage Channels can also use its controls. Claim is available to an occupant when the owner is absent.

| Command | Behavior |
| --- | --- |
| `/room rename name` | Set a name up to 90 characters; keep the automatic number suffix |
| `/room limit members` | Set capacity from 0–99; 0 means unlimited |
| `/room lock` | Deny @everyone joining; owner and bot retain access |
| `/room unlock` | Restore saved Connect settings, preserving unrelated permissions |
| `/room transfer member` | Give ownership to a human currently in the room |
| `/room claim` | Claim a room whose owner has left, including rooms from older releases |

Ownership and custom names survive restarts. Transfer and claim restore any saved lock first; the new owner can lock again. Claim cannot take over while the owner is present. Room locking requires the bot's Manage Roles permission. Explicit role/member allows and administrators can still join. If locking fails after saving a restore point, use `/room unlock` before retrying.

### Moderation

All replies are private. Unless marked **caller only**, the listed permission is required for both caller and bot.

| Command | Behavior | Permission |
| --- | --- | --- |
| `/mod warn member reason` | Record a warning | Moderate Members; caller only |
| `/mod warnings member [page]` | List warnings, five per page | Moderate Members; caller only |
| `/mod unwarn warning_id` | Remove a warning; retain case history | Moderate Members; caller only |
| `/mod case case_id` | Look up a case | Moderate Members; caller only |
| `/mod history member [page] [action]` | List cases, five per page, optionally filtered by action | Moderate Members; caller only |
| `/mod timeout member minutes reason` | Timeout for 1–40,320 minutes | Moderate Members |
| `/mod untimeout member reason` | Remove a timeout | Moderate Members |
| `/mod kick member reason` | Kick a member | Kick Members |
| `/mod ban member reason` | Ban without deleting messages | Ban Members |
| `/mod unban user_id reason` | Unban by numeric user ID | Ban Members |
| `/mod purge count` | Delete unpinned messages among the latest 1–100 | Manage Messages; bot also needs Read Message History |
| `/mod lock reason` | Deny @everyone sending messages and creating threads | Manage Roles |
| `/mod unlock reason` | Restore saved @everyone settings | Manage Roles |
| `/mod role_add member role reason` | Assign an existing role | Manage Roles |
| `/mod role_remove member role reason` | Remove an existing role | Manage Roles |

**Member and role checks.** Commands refresh member roles before checking access. Member actions reject self-targeting, the owner, and this bot. Non-owner callers must outrank targets; the bot must outrank targets of Discord actions. Timeouts cannot target administrators or bots. Role commands reject @everyone, managed roles, roles at or above the caller/bot, and grants of permissions a non-administrator caller does not hold.

**Case history.** Warn, unwarn, timeout, untimeout, kick, ban, and unban produce case IDs with member, moderator, reason, UTC timestamp, and outcome. Existing warnings migrate while retaining their warning IDs and timestamps. Removing a warning adds a linked removal case; it does not erase the original history. Warnings do not trigger automatic punishments or DMs.

External actions reserve a `pending` case, then record `succeeded`, `failed`, or `unknown`. Pending cases found after restart become unknown. **Check Discord's audit log before repeating an action with an unknown outcome.** Moderation actions are not retried automatically. Reasons include the moderator's ID in Discord audit logs where supported; channel and role actions retain their existing audit-log behavior.

**Text-channel controls.** Purge, lock, and unlock operate in regular text channels. Purge skips pins among the requested number inspected. Lock applies to @everyone; explicit role/member allows and administrators can still send. Unlock restores saved settings while preserving unrelated fields. If locking fails after saving a snapshot, use `/mod unlock` before retrying.

## Temporary voice rooms

Create a regular voice lobby, copy its ID, and configure:

```dotenv
TEMP_VOICE_LOBBY_ID=123456789012345678
TEMP_VOICE_NAME="{channel}"
TEMP_VOICE_STATUS="Room {number} • Hosted by {user}"
```

Each human entering the lobby gets a separate room and is moved into it. Rooms inherit the lobby's category, permission overwrites, bitrate, user limit, region, and video-quality settings. They appear below the lobby in creation order. Bot arrivals and mute/deafen changes do not create rooms.

| Placeholder | Value |
| --- | --- |
| `{user}` | Creator's display name at creation |
| `{channel}` | Lobby name at creation |
| `{number}` | Position among active tracked rooms, starting at 1 |

Names receive a number suffix unless `{number}` is placed explicitly. Deleting a room closes numbering gaps in remaining names and configured statuses. Numbering starts at 1 again when all rooms are gone. Names are limited to 100 characters; statuses to 500. Saved templates apply to existing rooms; `.env` changes apply to new ones. Legacy names are migrated where possible.

Only tracked rooms are deleted, after the last occupant leaves. n00bot's alone timeout lets rooms empty once it disconnects; other bots still keep rooms occupied. Tracking survives restarts. Recovery checks empty rooms and numbering at startup and every 30 seconds while the server is ready. Rapid departures are batched, and unchanged channel settings avoid repeat edits.

A failed status update does not prevent room creation or moving the member. Failed cleanup and renumbering are retried after permissions are corrected. While the bot is offline, rooms cannot be created; rejoin the lobby after it returns.

## Operations

```sh
docker compose ps
docker compose logs --tail=100 bot
docker compose stop
docker compose start
```

The container runs as a non-root user with a read-only application filesystem, writable data/tmp mounts, and rotating logs (10 MB × 3). It restarts after crashes unless explicitly stopped and has a 30-second graceful-shutdown allowance. Configure Docker to start at boot. No inbound ports are published; outbound HTTPS/WebSocket and UDP voice access are needed.

### Update

For a clean Git checkout, use `bash start.sh --update`. To rebuild changes already present locally:

```sh
docker compose up -d --build
```

After changing only `.env`, use `docker compose up -d --force-recreate`. Both preserve `data/`. Update release archives by unpacking a newer release and securely transferring `.env`, the complete `data/` directory, and cookies after stopping the old instance.

### Back up and restore

Stop the bot for a consistent SQLite/JSON backup:

```sh
docker compose stop
tar -czf ../n00bot-data-backup.tar.gz data
docker compose start
```

Choose a new backup filename each time. Store `.env` and cookies separately and securely. To restore, stop the bot, move the current `data/` aside as a rollback copy, extract a trusted backup, verify ownership, and start. Older backups omit records and tracked rooms created afterward.

## Troubleshooting

Start with `/health` if you have Manage Server, then inspect `docker compose logs --tail=100 bot`.

| Symptom | Check |
| --- | --- |
| Missing commands or Discord error `10004` | Correct server ID; install with `bot` and `applications.commands`; check startup sync logs |
| Voice channel not detected | Try `/join channel`; check View Channel and Connect |
| No audio or skipped tracks | `/nowplaying`, Speak permission, server mute, FFmpeg, outbound UDP, YouTube/cookie access |
| YouTube extraction fails | Try another public video; deliberately update yt-dlp and rebuild if needed |
| Temporary room remains or numbering is wrong | Occupants, bot permissions, and recovery logs; retries run every 30 seconds |
| Room controls denied | Join the room; check ownership or Manage Channels; locking also needs the bot's Manage Roles |
| Moderation denied | Caller/bot permissions, channel overrides, and role hierarchy |
| `/app/data` permission denied | Data-directory ownership and `BOT_UID`/`BOT_GID`; recreate the container |
| Container repeatedly restarts | Startup logs for configuration, credentials, dependencies, or persistent-state errors |
| fish cannot activate the virtual environment | Use `activate.fish` or call `.venv/bin/python` directly |

Tokens, cookies, and runtime data are excluded from Git and the Docker image. Avoid sharing expanded Compose configuration or environment dumps, which may contain credentials.

## Development and releases

Use Python 3.14 and install FFmpeg on the host. Locked Python dependencies include Deno and yt-dlp's JavaScript challenge component.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
# New installations only; then edit .env.
cp -n .env.example .env
.venv/bin/python bot.py --check
.venv/bin/python bot.py
```

These commands work in fish without activation. Stop with Ctrl+C. Do not run a local instance while the Docker bot is running. Startup sync replaces this application's commands in the configured server; use a dedicated development application when experimenting.

### Test and package

```sh
python3 scripts/sync_release.py
.venv/bin/python -B -m unittest discover -s tests -q
docker compose config --quiet
python3 scripts/sync_release.py --check
python3 scripts/build_release.py
```

Tests use mocked Discord calls and do not replace live server testing. The repository root is canonical; synchronize `release/n00bot` before committing. The release builder writes an allowlisted source archive and SHA-256 checksum to `dist/`, excluding secrets, runtime data, and the duplicate release copy.

`VERSION` holds the release number. Increment it per release: patch for fixes, minor for features, major for breaking changes. `/ping` and startup logs append `+src.<fingerprint>`, derived from runtime code and locked dependencies. Run `python3 version.py` to inspect it offline; `git rev-parse HEAD` identifies the exact Git revision, including documentation changes.

For dependency updates, use a fresh Python 3.14 environment, update deliberately, test, and regenerate `requirements.lock` with `python -m pip freeze`, preserving the `[voice]` and `[default]` extras. Rebuild the image and retain the previous source and lock for rollback. Do not install packages inside running containers. Base-image tags and OS packages can change on rebuild.

| Files | Responsibility |
| --- | --- |
| `bot.py`, `deployment.py` | Commands, voice maintenance, configuration, shutdown |
| `temp_voice.py`, `room_controls.py` | Room lifecycle, numbering, ownership, and access |
| `music.py`, `player.py` | YouTube downloads and music queue |
| `moderation.py`, `diagnostics.py` | Moderation storage/actions and private health checks |
| `start.sh`, `compose.yaml`, `Dockerfile` | Setup and deployment |
| `tests/`, `scripts/`, `version.py` | Verification, source packaging, and runtime identity |

References: [Discord setup](https://docs.discord.com/developers/quick-start/getting-started), [discord.py](https://discordpy.readthedocs.io/en/stable/api.html), [yt-dlp runtime setup](https://github.com/yt-dlp/yt-dlp/wiki/EJS), [Docker Compose](https://docs.docker.com/reference/compose-file/services/).
