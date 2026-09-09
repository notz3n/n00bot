FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/app/data \
    DENO_DIR=/tmp/deno \
    HOME=/tmp

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock \
    && python -m pip check
RUN groupadd --gid 1000 bot && useradd --uid 1000 --gid bot --no-create-home bot \
    && mkdir /app/data && chown bot:bot /app/data
COPY room_controls.py player.py diagnostics.py bot.py music.py moderation.py temp_voice.py deployment.py version.py VERSION ./
USER bot
CMD ["python", "bot.py"]
