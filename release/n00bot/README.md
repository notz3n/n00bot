# n00bot

A Discord bot for voice rooms, YouTube audio, and server moderation. Built with Python and discord.py, with Docker Compose deployment for a single server.

## Features

- Private `/help` tailored to the member's role and channel permissions.
- Voice-channel joining and single-track YouTube audio playback.
- Join-to-create voice rooms with inherited settings, custom names and statuses, and automatic cleanup.
- Active-room numbering that closes gaps when rooms are deleted.
- Warnings, timeouts, kick/ban, message cleanup, channel locking, and role assignment.
- Persistent state, runtime permission checks, and role-hierarchy enforcement.

## Discord setup

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications). Set its application name and bot username to `n00bot` if desired; these are separate from the local project name.
2. Under **Bot**, obtain the bot token. Store it only in your local `.env`.
3. Under **Installation → Guild Install**, select both `bot` and `applications.commands`. Open the installation link and choose **Add to server**. Confirm the bot appears in the server's member list.
4. Enable **User Settings → Advanced → Developer Mode** in Discord. Right-click the server, copy its ID, and use it for `DISCORD_GUILD_ID`.
5. Leave **Interactions Endpoint URL** empty. This bot uses the Discord gateway. Privileged intents are not required.

Grant permissions for the features you use:

| Feature | Bot permissions |
| --- | --- |
| Join voice | View Channel, Connect |
| Play audio | Speak, plus voice permissions |
| Temporary rooms | Manage Channels, Move Members, View Channel, Connect |
| Room status | Set Voice Channel Status, Manage Channels |
| Moderation | See the command table below |

Channel/category overrides also apply. Place the bot's role above the members and roles it must manage. Administrator permission is not required.

## Quick start: Docker Compose

For automatic setup on Arch/CachyOS, Ubuntu, or Debian, run:

```sh
./start.sh
```

The Bash script can be launched directly from fish. It installs missing Docker/Compose/Buildx tools (sudo may be required), starts Docker if needed, creates `.env` on first use, prompts privately for credentials when using the example placeholders and asks for an optional temporary-voice lobby channel ID when one is not configured, builds all runtime dependencies, validates configuration, and starts the container. Existing `.env` and `data/` are preserved. Run it again to rebuild and start after code changes. Stop any separately running local bot first.

Use `./start.sh --check` to install/build and validate without starting a live bot, or `./start.sh --help` for usage. Without an interactive terminal, provide a configured `.env` before running. On other systems, install Docker and its plugins first. Existing Ubuntu/Debian Docker installations need the official Docker repository configured for missing plugin packages; the script does not remove conflicting Docker packages automatically. Arch installations use the existing package database; update the system normally if package downloads are stale.

The script uses the host `data/` owner's UID/GID for that run. For later manual Compose commands, ensure `BOT_UID`/`BOT_GID` in `.env` match as described below. It can start a container but cannot confirm token validity until Discord login; inspect the displayed logs.

### Manual setup

Requires Docker Engine and Compose on a Linux host. Run commands from this project directory.

```sh
# New installations only; retain an existing .env.
cp -n .env.example .env
mkdir -p data
```

Edit `.env` using the configuration table below. Ensure `data/` is writable by the container user: `BOT_UID` and `BOT_GID` default to `1000`; use `id -u` and `id -g` to find your host user's IDs. Keep `.env` private, for example with `chmod 600 .env`.

```sh
docker compose build
docker compose run --rm --no-deps bot python bot.py --check
docker compose up -d
docker compose logs --tail=100 -f bot
```

The check validates local configuration, dependencies, temporary-room state loading, and data-directory write access without connecting to Discord. Token validity and server permissions are checked only when the bot connects or performs actions.

Wait for `Online as ...` and `cached=True available=True bot_member=True`, then try `/ping` and `/help`. Ctrl+C exits log viewing without stopping the container.

For migration from an existing installation, stop the old bot and transfer `.env` and the complete `data/` directory securely. **Run only one instance per bot token and data directory.** No inbound ports are published; the host needs outbound HTTPS/WebSocket access and outbound UDP for voice.

## Local development

Use Python 3.14 for the tested dependency set and install FFmpeg on your system. The Python dependencies include Deno and yt-dlp's JavaScript challenge component.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
# New installations only:
cp -n .env.example .env
# Edit .env before continuing.
.venv/bin/python bot.py --check
.venv/bin/python bot.py
```

These commands work in fish without activation. If preferred, use `source .venv/bin/activate.fish` in fish or `source .venv/bin/activate` in bash. Stop the bot with Ctrl+C. Do not run locally while the Docker instance is running.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `DISCORD_TOKEN` | Bot token; required | None |
| `DISCORD_GUILD_ID` | Server receiving slash commands; required | None |
| `TEMP_VOICE_LOBBY_ID` | Voice lobby that triggers temporary-room creation | Blank: disabled |
| `TEMP_VOICE_NAME` | New room name template | `{user}'s room` |
| `TEMP_VOICE_STATUS` | New room status template | Blank: unset |
| `DATA_DIR` | Local persistent-data location | Project `data/`; Compose uses `/app/data` |
| `BOT_UID`, `BOT_GID` | Docker runtime user/group IDs | `1000`, `1000` |

Use `.env.example` as the starting point. Restart local processes after configuration changes; recreate Docker containers to load updated environment variables.

## Commands

| Command | Behavior |
| --- | --- |
| `/help` | Privately list commands allowed by your permissions |
| `/ping` | Check availability and gateway latency |
| `/join [channel]` | Join your voice channel or an explicitly selected channel |
| `/leave` | Disconnect from voice |
| `/play url` | Stream audio from one YouTube video |
| `/stop` | Stop playback and stay connected |

Voice/music commands need no staff role. Playback controls require you to be in the bot's channel. `/leave` also allows members with Move Members permission to disconnect it from elsewhere. The bot will not move from another occupied channel on `/join`; disconnect it there first. Stage channels are not supported.

Run `/join` before `/play`. Audio streams without saving a file. There is no queue: use `/stop` before another video. A video URL with playlist parameters plays only that video. Private/restricted videos and some YouTube/network responses may prevent playback. The bot joins self-deafened and does not record audio.

### Moderation

| Command | Purpose | Caller and bot permission |
| --- | --- | --- |
| `/mod warn member reason` | Save a warning | Caller: Moderate Members |
| `/mod warnings member [page]` | Read warnings, five per page | Caller: Moderate Members |
| `/mod unwarn warning_id` | Remove one warning | Caller: Moderate Members |
| `/mod timeout member minutes reason` | Timeout for 1–40,320 minutes | Moderate Members |
| `/mod untimeout member reason` | Remove timeout | Moderate Members |
| `/mod kick member reason` | Kick a member | Kick Members |
| `/mod ban member reason` | Ban without deleting messages | Ban Members |
| `/mod unban user_id reason` | Unban by numeric user ID | Ban Members |
| `/mod purge count` | Delete unpinned messages among the latest 1–100 | Manage Messages; bot also needs Read Message History |
| `/mod lock reason` | Deny @everyone sending and thread creation | Manage Roles |
| `/mod unlock reason` | Restore saved @everyone settings | Manage Roles |
| `/mod role_add member role reason` | Assign an existing role | Manage Roles |
| `/mod role_remove member role reason` | Remove an existing role | Manage Roles |

Moderation replies are private. The caller needs the listed permission, and Discord actions also require the bot's permission. Warning storage/read/removal only requires the caller's permission. Commands refresh member roles before checking access; member actions reject self-targeting, the owner, and this bot. Non-owner callers must outrank targets, and the bot must outrank targets of Discord actions. Timeouts cannot target administrators or bots.

Role commands manage existing roles. They reject @everyone, managed roles, roles at or above the caller/bot, and permission grants that a non-administrator caller does not hold. Warnings are records, without automatic punishment or DMs. Reasons include the acting moderator's ID in Discord audit logs where supported.

`/help` filters using role permissions and current-channel overrides. Discord's command picker may still display commands a member cannot execute. Restrictions under **Server Settings → Integrations** can further limit commands and are not reflected in the help filter.

Purge, lock, and unlock operate in regular text channels. Lock denies @everyone sending messages and creating threads; explicit member/role allows and administrators can still send. Unlock restores the saved settings while preserving unrelated permission fields. If locking fails after saving a snapshot, use unlock before retrying. Purge skips pinned messages among the requested number inspected.

## Temporary voice rooms

Create a regular voice lobby in the configured server. Copy its ID and set:

```dotenv
TEMP_VOICE_LOBBY_ID=123456789012345678
TEMP_VOICE_NAME="{channel}"
TEMP_VOICE_STATUS="Room {number} • Hosted by {user}"
```

Replace the example ID with yours. Each human entering the lobby gets a separate room cloned from its category, permission overwrites, bitrate, user limit, region, and video-quality settings. The new room is placed immediately below the lobby in the channel list; newer rooms appear closest to the lobby. The member is moved into it. Mute/deafen changes and bot arrivals do not create rooms.

| Placeholder | Value |
| --- | --- |
| `{user}` | Creator's display name at creation |
| `{channel}` | Lobby name at creation |
| `{number}` | Position among active tracked rooms, starting at 1 |

Names get a numerical suffix automatically unless `{number}` is explicitly placed. With a lobby named `room`, `{channel}` produces `room 1`, `room 2`, and so on. When a room is deleted, remaining rooms are renumbered in creation order, including configured statuses. When all rooms disappear, numbering starts again at 1. Names are limited to 100 characters and statuses to 500.

The bot deletes only rooms it tracks, once the last occupant leaves. Bots count as occupants: use `/leave` if n00bot is still playing there. Tracking and naming templates survive restarts; startup cleans empty tracked rooms and reconciles numbers. Existing rooms retain their saved templates; changed `.env` templates apply to new rooms. Legacy names are migrated using the current template where possible.

If setting a status fails, room creation and moving still proceed, with a warning in the logs. If deletion or renumbering fails, correct permissions and restart to retry. While the bot is offline, rooms cannot be created; rejoin the lobby after it returns.

## Operations and backups

```sh
docker compose ps
docker compose logs --tail=100 bot
docker compose stop
docker compose start
```

The container runs as a non-root user with a read-only application filesystem and writable data/tmp mounts. It automatically restarts after crashes unless explicitly stopped. Graceful shutdown has a 30-second allowance, and logs rotate at 10 MB across three files. Configure Docker to start at boot for host-reboot recovery.

### Updates

```sh
# After updating source files:
docker compose up -d --build
# After changing .env:
docker compose up -d --force-recreate
```

Both preserve host `data/`. Do not install dependencies inside a running container. Python versions are pinned in `requirements.lock`; `requirements.txt` records direct dependencies. For dependency updates, use a fresh Python 3.14 environment, intentionally update direct versions, install, test, and regenerate the lock with `python -m pip freeze > requirements.lock`, retaining the `[voice]` and `[default]` extras. Rebuild and verify the image. Keep the previous source and lock for rollback. Base-image tags and OS packages may change on rebuild, so the image is not bit-for-bit reproducible.

### Back up persistent data

Stop the bot for a consistent SQLite/JSON backup:

```sh
docker compose stop
tar -czf ../n00bot-data-backup.tar.gz data
docker compose start
```

Choose a new backup filename each time and store `.env` separately in secure storage. To restore: stop the bot, move the current `data/` aside as a rollback copy, extract a trusted backup into the project, verify ownership matches the runtime user, then start. An older backup omits rooms or moderation records created afterward.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `cached=False` or Discord error `10004` | Install the bot into the correct server with the `bot` scope; verify server ID |
| Voice channel not detected | Select the `/join channel` option; check bot membership and View Channel/Connect permissions |
| No audio | Speak permission, server mute, FFmpeg, outbound UDP, and whether YouTube allows that video/network |
| YouTube extraction fails | Update yt-dlp deliberately and rebuild; try another public video |
| `/app/data` permission denied | Host directory ownership and `BOT_UID`/`BOT_GID`; recreate container |
| Container repeatedly restarts | Inspect startup logs for configuration, token, or state-file errors |
| Moderation action denied | Caller/bot permissions, channel overrides, and role hierarchy |
| fish cannot source `activate` | Use `activate.fish` or invoke `.venv/bin/python` directly |

Tokens and data are excluded from the Docker image and Git. Avoid sharing expanded Compose configuration or container environment dumps, which may contain the token.

## Project layout and tests

```text
bot.py                Discord client and public commands
deployment.py         Startup validation and signal handling
moderation.py         Moderation commands and SQLite storage
music.py              YouTube URL validation and audio extraction
temp_voice.py         Temporary-room lifecycle and numbering
tests/                Automated tests
Dockerfile            Runtime image with Python and FFmpeg
compose.yaml          Single-instance deployment
start.sh              Install, build, validate, and start
requirements.txt      Direct Python dependencies
requirements.lock     Tested pinned dependency set
.env.example          Configuration template
data/                 Runtime state (ignored by Git)
```

```sh
.venv/bin/python -B -m unittest discover -s tests -q
docker compose config --quiet
```

Tests exercise command behavior, permissions, room lifecycle, and shutdown with mocked Discord calls; they do not replace live server testing. New public commands are registered in `N00Bot.__init__`. Startup sync replaces this application's commands in the configured server, so use a dedicated development application when experimenting.

References: [Discord app setup](https://docs.discord.com/developers/quick-start/getting-started), [discord.py API](https://discordpy.readthedocs.io/en/stable/api.html), [yt-dlp runtime setup](https://github.com/yt-dlp/yt-dlp/wiki/EJS), and [Docker Compose services](https://docs.docker.com/reference/compose-file/services/).
