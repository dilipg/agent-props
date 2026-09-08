"""M0's only test: the committed fixtures are present and parse as JSON.

Everything else in the suite is empty by design until M1 gives it models to
exercise. See docs/worked-example.md for what each fixture is and why.
"""

import json

from conftest import FIXTURES_DIR

FIXTURE_FILES = [
    FIXTURES_DIR / "blueprints" / "location-onboarding-1.0.0.json",
    FIXTURES_DIR / "datasets" / "priya-missing-docs.json",
    FIXTURES_DIR / "datasets" / "arun-escalated.json",
    FIXTURES_DIR / "broken" / "manifest.json",
]


def test_fixture_files_exist() -> None:
    for path in FIXTURE_FILES:
        assert path.is_file(), f"missing fixture: {path}"


def test_fixture_files_parse_as_json() -> None:
    for path in FIXTURE_FILES:
        with path.open(encoding="utf-8") as f:
            document = json.load(f)
        assert isinstance(document, dict), f"expected a JSON object at top level: {path}"
