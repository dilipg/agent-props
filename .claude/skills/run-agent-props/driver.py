"""Drive the agent-props web app: screenshots, rendered text, and the review flow.

Why this file exists rather than a `chromium-cli` heredoc: `chromium-cli` is not
installed on this machine, and the `playwright` on PATH is a **Node** CLI (v1.40.1
via bun), not the Python package. So we install the Python package ephemerally and
point it at the Chrome already on disk:

    uv run --with playwright python .claude/skills/run-agent-props/driver.py flow

`--with` keeps it out of `pyproject.toml`, and `executable_path` stops Playwright
downloading a second browser.

Commands
--------
    up                 report whether both ports answer
    text               dump the rendered text of the landing page
    shot NAME          one screenshot of the landing page
    flow               the whole review flow, a screenshot per step
    eval 'JS'          evaluate JS in the page and print the result

Every command exits non-zero on failure, so it composes in a shell `&&` chain.
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

WEB = "http://127.0.0.1:5173"
SERVICE_PORT = 8000
WEB_PORT = 5173
# NOT /tmp: on this host `/tmp` resolves differently under Git Bash than under
# Python, so a screenshot the driver wrote could not be found by a shell `ls`.
# Repo-relative is unambiguous for both. Gitignored.
SHOTS = Path(__file__).resolve().parents[3] / ".shots"
# The Chrome that ships with Windows/WSL hosts. Override with AGENTPROPS_CHROME.
CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe"


def listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def require_up() -> None:
    """Fail loudly rather than screenshotting Vite's error page."""
    missing = [
        name
        for name, port in (("service :8000", SERVICE_PORT), ("web :5173", WEB_PORT))
        if not listening(port)
    ]
    if missing:
        sys.exit(
            f"not listening: {', '.join(missing)}\n"
            "Start them first - see SKILL.md 'Run (agent path)'."
        )


def browser(pw):
    # `pw` is untyped on purpose: playwright is an ephemeral --with install,
    # so importing its types at module scope would break `up`, which must run
    # without it.
    import os

    return pw.chromium.launch(
        executable_path=os.environ.get("AGENTPROPS_CHROME", CHROME),
        headless=True,
    )


def page_of(pw, errors: list[str]):
    b = browser(pw)
    page = b.new_page(viewport={"width": 1440, "height": 950})
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on(
        "console",
        lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None,
    )
    page.goto(WEB, wait_until="networkidle", timeout=30_000)
    return b, page


def rows(page) -> list[str]:
    """The dataset titles currently listed, so a step's effect is readable."""
    return [t.strip() for t in page.locator("h2, h3").all_inner_texts() if t.strip()]


def snap(page, name: str, note: str = "") -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(1400)
    target = SHOTS / f"{name}.png"
    page.screenshot(path=str(target))
    print(f"[{name}] {note}")
    for row in rows(page)[:6]:
        print(f"    {row[:76]}")
    print(f"    -> {target}")


def cmd_flow() -> int:
    """The five states worth looking at, in the order a reviewer meets them."""
    from playwright.sync_api import sync_playwright

    require_up()
    errors: list[str] = []
    with sync_playwright() as pw:
        b, page = page_of(pw, errors)

        snap(page, "01-landing", "both datasets, unfiltered")

        # Free-text search over title AND intent. "repeat operator" appears only
        # in Priya's intent, never in a title - which is the point of the field.
        search = page.get_by_placeholder("repeat operator")
        search.fill("repeat operator")
        snap(page, "02-search", 'q="repeat operator" -> expect 1 row')
        search.fill("")

        author = page.get_by_placeholder("pnair")
        author.fill("pnair")
        snap(page, "03-author", "author=pnair -> expect 1 row")
        author.fill("")
        page.wait_for_timeout(800)

        # Detail: narrative and intent side by side, under explicit headings.
        page.locator("h2, h3").first.click()
        snap(page, "04-detail", "narrative | intent")

        for label in ("blueprint", "Blueprint"):
            tab = page.get_by_text(label, exact=True)
            if tab.count():
                tab.first.click()
                break
        snap(page, "05-graph", "read-only node graph, incl. the loop edge")

        b.close()

    return report(errors)


def cmd_shot(name: str) -> int:
    from playwright.sync_api import sync_playwright

    require_up()
    errors: list[str] = []
    with sync_playwright() as pw:
        b, page = page_of(pw, errors)
        snap(page, name)
        b.close()
    return report(errors)


def cmd_text() -> int:
    from playwright.sync_api import sync_playwright

    require_up()
    errors: list[str] = []
    with sync_playwright() as pw:
        b, page = page_of(pw, errors)
        page.wait_for_timeout(1400)
        body = page.inner_text("body")
        print(f"CHARS: {len(body)}")
        print(body)
        b.close()
    return report(errors)


def cmd_eval(script: str) -> int:
    from playwright.sync_api import sync_playwright

    require_up()
    errors: list[str] = []
    with sync_playwright() as pw:
        b, page = page_of(pw, errors)
        page.wait_for_timeout(1200)
        print(json.dumps(page.evaluate(script), indent=2, default=str))
        b.close()
    return report(errors)


def report(errors: list[str]) -> int:
    """A favicon 404 is expected and is not a failure; anything else is.

    Filter on the generic text too, not just the filename: Chrome's console line
    for a failed fetch is "Failed to load resource: ... 404" and never names the
    file, so a filter on "favicon.ico" alone silently matches nothing.
    """
    noise = ("favicon.ico", "Failed to load resource")
    real = [e for e in errors if not any(n in e for n in noise)]
    if real:
        print("---- page/console errors ----")
        for error in real[:12]:
            print(f"  {error[:200]}")
        return 1
    print("---- no page or console errors (favicon 404 ignored) ----")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help"}:
        print(__doc__)
        return 0
    command, *rest = argv
    if command == "up":
        for name, port in (("service", SERVICE_PORT), ("web", WEB_PORT)):
            print(f"{name:8} :{port}  {'UP' if listening(port) else 'DOWN'}")
        return 0 if listening(SERVICE_PORT) and listening(WEB_PORT) else 1
    if command == "flow":
        return cmd_flow()
    if command == "text":
        return cmd_text()
    if command == "shot":
        return cmd_shot(rest[0] if rest else "shot")
    if command == "eval":
        if not rest:
            sys.exit("eval needs a JS expression")
        return cmd_eval(rest[0])
    sys.exit(f"unknown command: {command}\n{__doc__}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
