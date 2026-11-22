# The app on the NAS: `finanze serve` over the one database, which the pages and
# `docker exec finanze finanze <command>` both change. How it is built, started and updated:
# deploy/README.md.
#
# Everything personal is in /data, the volume, read and written there (see deploy/README.md):
# finanze.yaml, the database and its backups, originals/, rules.yaml, rules.<owner>.yaml,
# instruments.yaml and categories.yaml. The image holds only the code.
# No network once it is built, except `finanze prices` (D10).

FROM python:3.11-slim AS build

# The uv of ./uv and of check.yml too: change the three together.
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv

# The environment is copied into the next stage: real files, compiled now (nothing is
# written when the image runs), built with the Python of the image, at its final path.
ENV UV_PROJECT_ENVIRONMENT=/opt/finanze \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /build

# What the lock names, without the development tools; a lock that no longer matches
# pyproject.toml stops the build. The dependencies first: they change less than the code.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev --no-editable


FROM python:3.11-slim

# Tesseract and its English data, for the scanned pension certificates (extractors/ocr.py),
# and nothing it merely recommends. The package lists are not kept.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/finanze /opt/finanze

ENV PATH=/opt/finanze/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Not root. deploy/compose.yaml names the user of the NAS that owns the data folder instead;
# nothing in the image belongs to this one, so any other works as well.
USER 10001:10001

# The folder `finanze` runs in, `docker exec` included: finanze.yaml is found here, and so is
# anything it names by a relative path.
WORKDIR /data
EXPOSE 8765

# /login is the one page that asks for no password. From inside, the app is 127.0.0.1,
# a name it always answers to.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/login', timeout=3)"]

# Everything else comes from /data/finanze.yaml: the database, the rules, the instruments,
# who may see the pages. Before listening, a database at an older schema is copied to
# /data/backups and brought up to date; when that fails, nothing listens and the container
# stops with the reason in its log.
CMD ["finanze", "serve", "--config", "/data/finanze.yaml", "--port", "8765"]
