#!/usr/bin/env bash
# Run from any directory, including from a fish shell: ./start.sh
set -Eeuo pipefail
trap 'printf "\nn00bot setup stopped. Fix the error above and run this script again.\n" >&2' ERR
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

case "${1:-}" in
  --help|-h)
    printf '%s\n' 'Usage: ./start.sh [--check|--update|--offline]' \
      '--update fast-forwards the current branch from origin, then builds and starts.' \
      'Installs missing Docker tools on Arch/CachyOS or Ubuntu/Debian, builds n00bot, and starts it.' \
      '--check builds and validates configuration without starting the live bot.' \
      '--offline skips the Git update check.' \
      'System installation and Docker access may request sudo. Existing .env and data are preserved.'
    exit 0 ;;
  --check|--update|--offline|'') ;;
  *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
esac
[[ $# -le 1 ]] || { printf 'Pass at most one option.\n' >&2; exit 2; }

# Check only: never overwrite local changes or silently switch branches.
if [[ ${1:-} != --offline && ${N00BOT_UPDATED:-0} != 1 ]] && command -v git >/dev/null && [[ -d .git ]]; then
  branch=$(git symbolic-ref --quiet --short HEAD || true)
  if [[ -z $branch ]] || ! git remote get-url origin >/dev/null 2>&1; then
    if [[ ${1:-} == --update ]]; then
      printf 'Updates require a branch checkout with an origin remote.\n' >&2; exit 1
    fi
  elif GIT_TERMINAL_PROMPT=0 git fetch --quiet origin "$branch"; then
    target=$(git rev-parse FETCH_HEAD)
    if [[ $(git rev-parse HEAD) != "$target" ]]; then
      if [[ ${1:-} == --update ]]; then
        if [[ -n $(git status --porcelain) ]]; then
          printf 'Local changes block updating. Back up/review them first; nothing was overwritten.\n' >&2
          git status --short
          exit 1
        fi
        git merge --ff-only "$target"
        export N00BOT_UPDATED=1
        exec bash ./start.sh
      fi
      printf 'Origin differs from this checkout. Run ./start.sh --update to fast-forward safely.\n'
    else
      printf 'Git checkout is up to date.\n'
    fi
  else
    if [[ ${1:-} == --update ]]; then
      printf 'Could not fetch origin; update aborted. Check GitHub access.\n' >&2; exit 1
    fi
    printf 'Update check unavailable; continuing with local source.\n' >&2
  fi
elif [[ ${1:-} == --update && ${N00BOT_UPDATED:-0} != 1 ]]; then
  printf 'Automatic updates require Git and a cloned repository. Release archives must be replaced manually.\n' >&2
  exit 1
fi

as_root() {
  if [[ $EUID -eq 0 ]]; then "$@"; else sudo -- "$@"; fi
}

install_docker() {
  [[ $(uname -s) == Linux ]] || {
    printf 'Install Docker Desktop, start it, then rerun this script.\n' >&2; exit 1;
  }
  # This is trusted operating-system metadata, never the project's .env.
  source /etc/os-release
  printf 'Installing missing Docker tools for %s. System changes may require sudo.\n' "$ID"
  case " $ID ${ID_LIKE:-} " in
    *' arch '*|*' cachyos '*)
      as_root pacman -S --needed --noconfirm docker docker-compose docker-buildx ;;
    *)
      if [[ $ID != ubuntu && $ID != debian ]]; then
        printf 'Automatic installation supports Arch/CachyOS, Ubuntu, and Debian. Install Docker Engine, Compose, and Buildx for your OS, then rerun.\n' >&2
        exit 1
      fi
      # Existing Docker installations are not removed or replaced automatically.
      if command -v docker >/dev/null; then
        as_root apt-get update
        as_root apt-get install -y docker-compose-plugin docker-buildx-plugin
      else
        as_root apt-get update
        as_root apt-get install -y ca-certificates curl
        as_root install -m 0755 -d /etc/apt/keyrings
        as_root curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
        as_root chmod a+r /etc/apt/keyrings/docker.asc
        printf 'Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker.asc\n' \
          "$ID" "${UBUNTU_CODENAME:-$VERSION_CODENAME}" "$(dpkg --print-architecture)" |
          as_root tee /etc/apt/sources.list.d/docker.sources >/dev/null
        as_root apt-get update
        as_root apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
      fi ;;
  esac
}

if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1 || ! docker buildx version >/dev/null 2>&1; then
  install_docker
fi

docker_cmd=(docker)
if ! docker info >/dev/null 2>&1; then
  if command -v systemctl >/dev/null && [[ -d /run/systemd/system ]]; then
    as_root systemctl enable --now docker
  fi
  if ! docker info >/dev/null 2>&1; then
    if [[ $EUID -ne 0 ]]; then docker_cmd=(sudo --preserve-env=BOT_UID,BOT_GID docker); fi
    "${docker_cmd[@]}" info >/dev/null
  fi
fi
"${docker_cmd[@]}" compose version >/dev/null
"${docker_cmd[@]}" buildx version >/dev/null

# Match the data owner's UID/GID without changing permissions on existing data.
mkdir -p data
if [[ $(uname -s) == Linux ]]; then
  export BOT_UID="${BOT_UID:-$(id -u)}"
  export BOT_GID="${BOT_GID:-$(id -g)}"
  # Existing data may have been created by root or an older container user.
  # Align ownership with the UID/GID Compose will pass to the container.
  if ! (umask 077; touch data/.n00bot-write-check && rm -f data/.n00bot-write-check) 2>/dev/null; then
    printf 'The data directory is not writable; updating ownership to %s:%s.\n' "$BOT_UID" "$BOT_GID"
    as_root chown -R "$BOT_UID:$BOT_GID" data
  fi
fi

if [[ ! -e .env ]]; then
  (umask 077; cp .env.example .env)
fi
if grep -Eq '^DISCORD_TOKEN=(your_bot_token_here)?[[:space:]]*$|^DISCORD_GUILD_ID=(your_test_server_id_here)?[[:space:]]*$' .env; then
  if [[ ! -t 0 ]]; then
    printf 'Edit .env with DISCORD_TOKEN and DISCORD_GUILD_ID, then rerun. No bot was started.\n' >&2
    exit 1
  fi
  printf 'Enter credentials from the Discord Developer Portal (token input is hidden).\n'
  read -r -s -p 'Bot token: ' bot_token
  printf '\n'
  read -r -p 'Server ID: ' bot_guild
  [[ $bot_token =~ ^[A-Za-z0-9_.-]+$ && $bot_guild =~ ^[0-9]+$ ]] || {
    printf 'Invalid token characters or server ID; .env was not changed.\n' >&2; exit 1;
  }
  config_tmp=$(mktemp .env.setup.XXXXXX)
  while IFS= read -r line || [[ -n $line ]]; do
    case "$line" in
      DISCORD_TOKEN=*) printf 'DISCORD_TOKEN=%s\n' "$bot_token" ;;
      DISCORD_GUILD_ID=*) printf 'DISCORD_GUILD_ID=%s\n' "$bot_guild" ;;
      *) printf '%s\n' "$line" ;;
    esac
  done < .env > "$config_tmp"
  mv -- "$config_tmp" .env
  unset bot_token bot_guild
fi

if [[ -t 0 ]] && ! grep -Eq "^TEMP_VOICE_LOBBY_ID=['\"]?[0-9]+['\"]?[[:space:]]*(#.*)?$" .env; then
  printf '\nTemporary voice rooms: copy the lobby voice channel ID from Discord (Developer Mode).\n'
  read -r -p 'Temporary voice lobby channel ID (Enter to disable): ' bot_lobby
  [[ -z $bot_lobby || $bot_lobby =~ ^[0-9]+$ ]] || {
    printf 'The lobby channel ID must be numeric or blank; .env was not changed.\n' >&2; exit 1;
  }
  config_tmp=$(mktemp .env.setup.XXXXXX)
  lobby_written=false
  while IFS= read -r line || [[ -n $line ]]; do
    case "$line" in
      TEMP_VOICE_LOBBY_ID=*)
        printf 'TEMP_VOICE_LOBBY_ID=%s\n' "$bot_lobby"
        lobby_written=true ;;
      *) printf '%s\n' "$line" ;;
    esac
  done < .env > "$config_tmp"
  if [[ $lobby_written == false ]]; then
    printf 'TEMP_VOICE_LOBBY_ID=%s\n' "$bot_lobby" >> "$config_tmp"
  fi
  mv -- "$config_tmp" .env
  unset bot_lobby
fi

# Use a generated, ignored override rather than editing tracked compose.yaml.
# Reject Docker's accidentally-created directory before starting a container.
if [[ -e youtube-cookies.txt ]]; then
  if [[ ! -f youtube-cookies.txt || ! -s youtube-cookies.txt || ! -r youtube-cookies.txt ]]; then
    printf 'youtube-cookies.txt must be a readable, nonempty regular file.\n' >&2; exit 1
  fi
  if [[ -e compose.override.yaml ]] && ! grep -q '^# Generated by n00bot start.sh' compose.override.yaml; then
    printf 'Existing compose.override.yaml preserved; ensure it mounts /app/youtube-cookies.txt.\n'
  else
  cat > compose.override.yaml <<'YAML'
# Generated by n00bot start.sh
services:
  bot:
    volumes:
      - type: bind
        source: ./youtube-cookies.txt
        target: /app/youtube-cookies.txt
        read_only: true
        bind:
          create_host_path: false
YAML
  fi

  printf 'YouTube cookie mount configured; cookie validity is checked by YouTube during playback.\n'
else
  printf 'No youtube-cookies.txt: public playback may work, but YouTube may require cookies.\n'
fi

printf 'Building n00bot with Python, FFmpeg, Deno, and locked dependencies...\n'
"${docker_cmd[@]}" compose config --quiet
"${docker_cmd[@]}" compose build
if [[ -f youtube-cookies.txt ]]; then
  "${docker_cmd[@]}" compose run --rm --no-deps --entrypoint python bot -c "from pathlib import Path; p=Path('/app/youtube-cookies.txt'); assert p.is_file() and p.read_bytes(), 'Cookie file is unreadable by the container user'"
fi
"${docker_cmd[@]}" compose run --rm --no-deps bot python bot.py --check
if [[ ${1:-} == --check ]]; then
  printf 'Build and offline validation passed. No live bot started.\n'
  exit 0
fi
printf 'Starting n00bot. Stop any separately running copy of this bot first.\n'
"${docker_cmd[@]}" compose up -d --no-build
"${docker_cmd[@]}" compose ps
"${docker_cmd[@]}" compose logs --tail=20 bot
printf '\nContainer started. Confirm "Online as" in the logs to verify Discord login.\n'
printf 'Logs: %s compose logs -f bot\nStop: %s compose stop\n' "${docker_cmd[*]}" "${docker_cmd[*]}"
