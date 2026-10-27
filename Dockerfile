# Soundsave web player. The music folder and the database/cache are mounted
# from the host by docker-compose.yml; nothing user-owned is baked into the image.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --upgrade pip \
    && python -m pip install ".[web]"

RUN mkdir -p /music /app/data

EXPOSE 8765

# The startup reconciliation can take a few seconds on a large library; it is
# mostly stat() calls, tags are re-read only for new or changed files.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3)"

STOPSIGNAL SIGTERM

CMD ["soundsave", "serve", "--host", "0.0.0.0", "--port", "8765"]
