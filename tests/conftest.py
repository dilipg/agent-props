"""Shared pytest fixtures and the one path constant every suite needs.

:data:`FIXTURES_DIR` is defined here and imported by the suites (``from
conftest import FIXTURES_DIR``) rather than recomputed per file, so a fixture
directory moves in one place. It is a module constant, not a pytest fixture,
because the round-trip and schema suites parametrise over the directory at
collection time and a fixture is not available then.

Pytest fixtures proper land as each milestone needs them: in-memory MCP client
wiring at M4, storage backend parametrisation at M3/M7.
"""

from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
