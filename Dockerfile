# The app in a container: `scrooge serve` over the one database, which the pages and
# `docker compose exec scrooge scrooge <command>` both change. How it is started and updated,
# on any machine with Docker: deploy/README.md. Built for x86-64 and 64-bit ARM.
#
# Everything personal is in /data, the volume, read and written there (see deploy/README.md):
# the config, the database and its backups, originals/, the rules, instruments and categories,
# as `scrooge init --container` makes them in /data/users/<name>/, or in /data itself.
# The image holds only the code. No network once it is built, except `scrooge prices` (D10).

# The frontend at /app (frontend/, `make web`): built here, so that the image needs no Node.
# Node 22 is the oldest SvelteKit takes; its lock first, it changes less than the code.
# Built on the builder's own processor, whatever the image is for: the result is the same files.
FROM --platform=$BUILDPLATFORM node:22-slim AS web

WORKDIR /web/frontend
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci
COPY frontend ./
# Into ../src/scrooge/web/spa, as `make web` does: /web/src/scrooge/web/spa.
RUN npm run build


FROM python:3.11-slim AS build

# The uv of ./uv and of check.yml too: change the three together.
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv

# The environment is copied into the next stage: real files, compiled now (nothing is
# written when the image runs), built with the Python of the image, at its final path.
ENV UV_PROJECT_ENVIRONMENT=/opt/scrooge \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /build

# What the lock names, without the development tools; a lock that no longer matches
# pyproject.toml stops the build. The dependencies first: they change less than the code.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY LICENSE ./
COPY src ./src
COPY --from=web /web/src/scrooge/web/spa ./src/scrooge/web/spa
RUN uv sync --locked --no-dev --no-editable


FROM python:3.11-slim

# Tesseract and its English data, for the scanned pension certificates (extractors/ocr.py),
# and nothing it merely recommends. The package lists are not kept.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/scrooge /opt/scrooge

ENV PATH=/opt/scrooge/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Not root. deploy/compose.yaml names the user that owns the data folder instead (.env);
# nothing in the image belongs to this one, so any other works as well.
USER 10001:10001

# The folder `scrooge` runs in, `docker compose exec` included: the config is found here
# (/data/scrooge.yaml, else the one setup in /data/users/), and so is anything it names by a
# relative path.
WORKDIR /data
EXPOSE 8765

# /login is the one page that asks for no password. From inside, the app is 127.0.0.1,
# a name it always answers to.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/login', timeout=3)"]

# Everything else comes from the config: the database, the rules, the instruments, who may
# see the pages. Without one the container stops, and its log says to run `scrooge init`.
# Before listening, a database at an older schema is copied to backups/ next to it and
# brought up to date; when that fails, nothing listens and the container stops with the
# reason in its log.
CMD ["scrooge", "serve", "--port", "8765"]
