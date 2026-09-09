# The agent-props service, over streamable HTTP.
#
# Two stages. The first resolves dependencies with `uv` against the committed
# `uv.lock`, so the image contains the same versions CI tested; the second
# carries the resulting virtualenv onto a plain Python base with no build tools
# in it. That is the whole reason for two stages: a single-stage image ships
# `uv`, a package cache and a compiler toolchain to production for no benefit.
#
#   docker build -t agent-props .
#   docker compose --profile local up      # service + Mongo, for authoring
#   docker compose --profile shared up     # service + Postgres
#
# `--store sqlite <path>` needs no container at all and is what CI uses.

# --------------------------------------------------------------------------
# stage 1: resolve and install, exactly as the lockfile says
# --------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /src

# Dependencies first, from the manifest and the lock alone, so a source edit
# does not invalidate the layer that took the time. `--no-install-project`
# is what makes that split possible: the project itself is installed below,
# after its own files are copied.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# Then the project. `--no-editable` because an editable install points the
# virtualenv at /src, which stage 2 does not have.
COPY src ./src
COPY README.md ./
RUN uv sync --locked --no-dev --no-editable

# --------------------------------------------------------------------------
# stage 2: the runtime
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

# PYTHONUNBUFFERED so container logs appear as they happen rather than when a
# buffer fills, which is the difference between a readable `docker compose up`
# and a silent one. PYTHONDONTWRITEBYTECODE because stage 1 already compiled
# the bytecode it needs and the filesystem here is read-mostly.
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGENTPROPS_HTTP_HOST=0.0.0.0 \
    AGENTPROPS_HTTP_PORT=8000

COPY --from=build /opt/venv /opt/venv

WORKDIR /app

# `alembic.ini` and the migrations travel together, and both have to be here:
# the `shared` profile runs `alembic upgrade head` before serving, because a
# Postgres database is migrated and never created by the adapter. `alembic.ini`
# resolves `script_location` relative to its own directory, so `src/` has to sit
# beside it - which is why this is copied rather than relying on the installed
# package.
COPY alembic.ini ./
COPY src ./src

# A non-root user, created here rather than relying on one the base image
# happens to have. Nothing in this service writes to its own filesystem in
# either compose profile - the containerless SQLite mode does, and a caller
# using it mounts a volume - so the account owns nothing and needs no shell.
RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 agentprops
USER 10001

EXPOSE 8000

# A TCP connect rather than an HTTP request, deliberately: the MCP streamable
# HTTP transport answers `/mcp` only for a POST carrying a protocol handshake,
# so a `GET /` healthcheck would report unhealthy on a working server. What this
# needs to establish is that the process is up and listening, which is exactly
# what a connect establishes. No `curl` in the image either, which is one fewer
# thing to keep patched.
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=12 \
    CMD ["python", "-c", "import os,socket; socket.create_connection(('127.0.0.1', int(os.environ['AGENTPROPS_HTTP_PORT'])), 2).close()"]

# The store is not baked in. `AGENTPROPS_STORE` is read by
# `python -m agentprops.server`, so one image serves a Mongo, a Postgres or a
# SQLite file, and `docker-compose.yml` is the only place a URL appears.
CMD ["python", "-m", "agentprops.server", "--transport", "http"]
