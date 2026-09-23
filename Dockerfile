FROM python:3.12.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN groupadd --gid 10001 bot && useradd --uid 10001 --gid bot --no-create-home bot \
    && mkdir /data && chown bot:bot /data
COPY pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock setuptools==80.9.0
COPY src ./src
COPY config ./config
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
USER bot
STOPSIGNAL SIGTERM
CMD ["python", "-m", "discord_tracker"]
