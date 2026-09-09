"""The Dockerfile and `docker-compose.yml`, as a drift guard rather than a smoke test.

Starting a container in a unit test would be slow, would need Docker, and would
test Docker. What this file tests is the thing a running container cannot tell
you: whether the compose file and the Python it starts still **agree**.

Three ways they can silently disagree, and each is a test here:

**A renamed environment variable.** `server/__main__.py` reads
``AGENTPROPS_STORE``, ``AGENTPROPS_HTTP_HOST`` and ``AGENTPROPS_HTTP_PORT``.
Rename one on either side and the container starts perfectly, serves happily,
and uses the *default* store - a SQLite file inside the container, thrown away
on the next ``docker compose up``. Nothing logs it and no test would notice,
which is the worst kind of green.

The three are written in **two** places and the split is deliberate: the store
URL is per-profile so it is in each service's ``environment``, and the HTTP pair
is the same for both so it is in the image. YAML's ``<<`` replaces a mapping
rather than merging into it, so an anchored ``environment:`` would have been
discarded by both services and would have looked shared while being dead. So
this file checks both places.

**A store URL whose scheme nothing dispatches.** ``context_for`` routes on the
URL prefix. A compose file saying ``mongo://`` instead of ``mongodb://`` falls
through to the SQLite branch and the service quietly serves a file named
``mongo://mongo:27017/agentprops``.

**A missing health dependency.** ``docker compose --profile local up`` is
required by the acceptance criteria to give "a working service with Mongo in
**one command**". Without ``condition: service_healthy`` that is a race the
service loses on a cold start, and it looks like a working setup that is flaky
on other people's machines.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from agentprops.server.__main__ import (
    HTTP_HOST_ENV_VAR,
    HTTP_PORT_ENV_VAR,
    STORE_ENV_VAR,
)
from agentprops.server.app import DEFAULT_HTTP_PORT
from agentprops.service.context import (
    _MONGO_PREFIXES,
    _POSTGRES_PREFIXES,
)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
COMPOSE_PATH: Final[Path] = REPO_ROOT / "docker-compose.yml"
DOCKERFILE_PATH: Final[Path] = REPO_ROOT / "Dockerfile"
DOCKERIGNORE_PATH: Final[Path] = REPO_ROOT / ".dockerignore"

#: The two profiles `docs/build-handoff.md` section 2 names, and the database
#: each one is for.
PROFILES: Final[dict[str, str]] = {"local": "mongo", "shared": "postgres"}


def compose() -> dict[str, Any]:
    """The parsed compose file, with the ``x-service`` anchor resolved.

    ``yaml.safe_load`` implements the ``<<`` merge key, so the two service
    definitions come back with the shared block already applied - which is the
    thing being asserted about them.
    """
    document: dict[str, Any] = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    return document


def services() -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = compose()["services"]
    return found


def service_for(profile: str) -> dict[str, Any]:
    """The one service in ``profile`` that is not a database."""
    databases = set(PROFILES.values())
    matching = [
        definition
        for name, definition in services().items()
        if profile in definition.get("profiles", []) and name not in databases
    ]
    assert len(matching) == 1, f"profile {profile!r} has {len(matching)} non-database services"
    return matching[0]


def test_the_container_files_exist() -> None:
    """`docs/build-handoff.md` section 3 puts all three at the repository root."""
    assert DOCKERFILE_PATH.is_file()
    assert COMPOSE_PATH.is_file()
    assert DOCKERIGNORE_PATH.is_file()


def test_the_compose_file_declares_exactly_two_profiles() -> None:
    """``local`` and ``shared``, and nothing else.

    Both halves matter. A third profile would be a deployment shape nobody
    documented; a *missing* one is an acceptance criterion that cannot be run.
    """
    declared = {
        profile for definition in services().values() for profile in definition.get("profiles", [])
    }
    assert declared == set(PROFILES)


def test_every_service_is_in_a_profile() -> None:
    """A service with no profile starts on **every** ``docker compose up``.

    Which would mean ``--profile local`` also starting Postgres - two databases,
    one of them unused, and a port conflict on the machine of anyone who
    already runs one.
    """
    profileless = [
        name for name, definition in services().items() if not definition.get("profiles")
    ]
    assert not profileless, f"these services start in every profile: {profileless}"


@pytest.mark.parametrize("profile", sorted(PROFILES), ids=lambda name: str(name))
def test_each_service_names_the_store_variable_the_entry_point_reads(profile: str) -> None:
    """The drift guard that matters most, in the direction that fails silently.

    A renamed variable on either side leaves a container that starts, serves,
    and uses the default SQLite file inside itself - discarded on the next
    ``up``. Read from `server/__main__.py`'s own constant rather than spelled
    again here, so there is one source for the name.

    The store is the **only** thing a service's ``environment`` carries, which
    is what makes "the only difference between the two profiles is the store
    URL" literally true rather than nearly true. The HTTP pair is the image's -
    see the test below.
    """
    environment = service_for(profile)["environment"]
    assert list(environment) == [STORE_ENV_VAR], (
        f"the {profile} service environment is {sorted(environment)}, not just the store"
    )


def test_the_image_sets_the_http_host_and_port_the_entry_point_reads() -> None:
    """The other half, in the other file, for the reason the module docstring gives.

    ``0.0.0.0`` and not ``127.0.0.1``: a container listening on loopback is
    unreachable from the host, which is a working server that nothing can talk
    to. The port is checked against ``DEFAULT_HTTP_PORT`` so the image, the
    compose port mapping and the Python default cannot drift into three numbers.
    """
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert f"{HTTP_HOST_ENV_VAR}=0.0.0.0" in dockerfile, (
        "the image does not bind 0.0.0.0, so the container is unreachable from the host"
    )
    assert f"{HTTP_PORT_ENV_VAR}={DEFAULT_HTTP_PORT}" in dockerfile
    assert f"EXPOSE {DEFAULT_HTTP_PORT}" in dockerfile


@pytest.mark.parametrize(
    ("profile", "prefixes"),
    [("local", _MONGO_PREFIXES), ("shared", _POSTGRES_PREFIXES)],
    ids=["local-is-mongo", "shared-is-postgres"],
)
def test_each_profile_points_at_the_backend_it_is_for(
    profile: str, prefixes: tuple[str, ...]
) -> None:
    """The scheme has to be one ``context_for`` dispatches, not one that reads well.

    ``mongo://`` instead of ``mongodb://`` falls through to the SQLite branch and
    the service serves a file whose name is the URL. Checked against the
    dispatch's own prefix tuples, so a change to either side has to be a change
    to both.
    """
    environment = service_for(profile)["environment"]
    assert STORE_ENV_VAR in environment, (
        f"the {profile} service sets no {STORE_ENV_VAR}; it has {sorted(environment)}"
    )
    url = environment[STORE_ENV_VAR]
    assert url.startswith(prefixes), f"the {profile} store URL {url!r} dispatches elsewhere"


@pytest.mark.parametrize("profile", sorted(PROFILES), ids=lambda name: str(name))
def test_the_service_waits_for_its_database_to_be_healthy(profile: str) -> None:
    """ "One command", as the acceptance criteria phrase it, needs this.

    Without ``condition: service_healthy`` the service starts while the database
    is still initialising, and whether ``docker compose up`` works depends on
    which machine it is run on. ``service_started`` is the default and is not
    enough: Postgres accepts connections part-way through creating its database.
    """
    database = PROFILES[profile]
    depends = service_for(profile)["depends_on"]
    assert database in depends, f"the {profile} service does not wait for {database}"
    assert depends[database]["condition"] == "service_healthy"


@pytest.mark.parametrize("database", sorted(set(PROFILES.values())), ids=lambda name: str(name))
def test_each_database_declares_a_healthcheck(database: str) -> None:
    """Which is what makes the dependency above mean anything.

    ``condition: service_healthy`` against an image with no healthcheck never
    becomes satisfied, so ``up`` hangs rather than racing - a different failure,
    equally confusing, and this is the half that prevents it.
    """
    definition = services()[database]
    assert "healthcheck" in definition, f"{database} has no healthcheck to depend on"
    assert definition["healthcheck"]["test"], f"{database}'s healthcheck runs nothing"


def test_the_shared_profile_migrates_before_it_serves() -> None:
    """A Postgres database is migrated, and by nothing else.

    ``context_for`` deliberately does **not** create a schema for a SQL URL -
    `server/__main__.py` recorded the reason at M4 ("a URL argument that
    silently ran ``create_schema`` against a migrated Postgres database would be
    worse than not having one") - so the ``shared`` profile has to run
    ``alembic upgrade head`` itself. Without this the service starts and every
    tool answers "no such table".
    """
    command = service_for("shared")["command"]
    text = " ".join(command) if isinstance(command, list) else str(command)
    assert "alembic" in text and "upgrade head" in text
    assert "agentprops.server" in text, "the shared profile migrates and never serves"


def test_the_local_profile_needs_no_migration_step() -> None:
    """Mongo has no DDL, and ``create_schema`` on it is idempotent index declaration.

    So the ``local`` profile is genuinely one command, with no command override
    at all - and asserting the *absence* is what stops someone adding an
    ``alembic upgrade head`` there that would fail against a document store.
    """
    assert "command" not in service_for("local")


def test_the_image_runs_as_a_non_root_user() -> None:
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "\nUSER " in dockerfile, "the image runs as root"
    assert "\nUSER root" not in dockerfile


def test_the_image_bakes_in_no_store() -> None:
    """One image, three backends. The URL lives in the compose file and nowhere else.

    A ``CMD`` naming a store would make the image a deployment rather than a
    build, and the ``local``/``shared`` split would need two of them.
    """
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    command = next(line for line in dockerfile.splitlines() if line.startswith("CMD "))
    assert "--store" not in command
    assert "mongodb://" not in dockerfile and "postgresql://" not in dockerfile


def test_the_image_carries_alembic_and_its_migrations() -> None:
    """The ``shared`` profile runs ``alembic`` inside the container.

    ``alembic.ini`` resolves ``script_location`` relative to its own directory,
    so ``src/`` has to sit beside it in the image - which is why both are copied
    rather than relying on the installed package.
    """
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "COPY alembic.ini" in dockerfile
    assert "COPY src ./src" in dockerfile


def test_the_build_context_excludes_the_local_virtualenv() -> None:
    """The one ignore rule that is a correctness bug rather than a size one.

    A developer's `.venv/` is a Windows or macOS virtualenv on most machines,
    and copying it into a Linux image produces an interpreter that cannot start
    - after a long, silent upload of a few hundred megabytes.
    """
    ignored = {
        line.strip()
        for line in DOCKERIGNORE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert ".venv/" in ignored
    assert ".git/" in ignored


def test_the_build_installs_from_the_committed_lockfile() -> None:
    """``uv sync --locked``, so the image holds the versions CI tested.

    Without ``--locked`` the build re-resolves, and an image built on Tuesday
    can carry a different dependency tree than the one the suite passed
    against - which is the class of drift this whole project is about.
    """
    dockerfile = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "uv sync --locked" in dockerfile
    assert "--no-dev" in dockerfile, "the image ships the test dependencies"
