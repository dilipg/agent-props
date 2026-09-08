"""Shared pytest fixtures.

Empty at M0 by design. Fixtures land as each milestone needs them:
in-memory MCP client wiring at M4, storage backend parametrisation at M3/M7.
"""

from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
