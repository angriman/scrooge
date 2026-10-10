# The web app alone (`finanze serve`), to show a copy of the database from another machine.
# How it is built, started and updated: deploy/README.md.
#
# No Tesseract: nothing is imported where this runs. No network once it is built.

FROM python:3.11-slim AS build

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

COPY --from=build /opt/finanze /opt/finanze

ENV PATH=/opt/finanze/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Not root. deploy/compose.yaml names the user of the machine that may read the data instead;
# nothing in the image belongs to this one, so any other works as well.
USER 10001:10001

# /data is the one folder the app looks into: the config and the database, and the rules
# file a remembered category would go to. It is meant to be mounted read-only.
WORKDIR /data
EXPOSE 8765

# /login is the one page that asks for no password. From inside, the app is 127.0.0.1,
# a name it always answers to.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/login', timeout=3)"]

CMD ["finanze", "serve", \
     "--config", "/data/finanze.yaml", \
     "--db", "/data/finanze.db", \
     "--rules", "/data/rules.local.yaml", \
     "--port", "8765"]
