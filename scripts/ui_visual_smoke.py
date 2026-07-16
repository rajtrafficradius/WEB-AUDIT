"""Run an authenticated desktop/mobile browser smoke and save visual evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "exports" / ".ui-smoke"
EDGE_PATHS = (
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
)
PROJECT_PATH = re.compile(r"^/projects/[0-9a-f-]{36}/$")


def _inside_project(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(PROJECT_ROOT):
        raise ValueError("Visual smoke output must remain inside the project root")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _assert_page_contract(page: Page, label: str) -> dict[str, object]:
    checks = {
        "main_landmark": page.locator("main").count() == 1,
        "h1_present": page.locator("main h1").count() == 1,
        "skip_link": page.locator('a.skip-link[href="#main-content"]').count() == 1,
        "placeholder_links": page.locator('a[href="#"], form[action="#"]').count() == 0,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise AssertionError(f"{label} failed browser contract checks: {failed}")
    return checks


def _first_project_path(page: Page) -> str:
    links = page.locator("main a[href]")
    for index in range(links.count()):
        href = links.nth(index).get_attribute("href") or ""
        if PROJECT_PATH.fullmatch(href):
            return href
    raise AssertionError("The authenticated dashboard did not expose a scoped project detail link")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--username", default="visual-admin")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    password = os.getenv("SEO_STUDIO_SMOKE_PASSWORD", "")
    if not password:
        parser.error("SEO_STUDIO_SMOKE_PASSWORD is required and is never written to output")
    output = _inside_project(args.output)
    edge = next((candidate for candidate in EDGE_PATHS if candidate.is_file()), None)
    if edge is None:
        raise RuntimeError("Microsoft Edge is required for this local visual smoke")

    console_errors: list[str] = []
    results: dict[str, object] = {
        "schema_version": "1.0",
        "verified_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "browser": str(edge),
        "checks": {},
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(edge), headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        page = context.new_page()
        page.on(
            "console",
            lambda message: console_errors.append(message.text) if message.type == "error" else None,
        )
        response = page.goto(f"{args.base_url}/auth/login/", wait_until="networkidle")
        if response is None or response.status != 200:
            raise AssertionError("Login page did not return HTTP 200")
        page.locator('input[name="username"]').fill(args.username)
        page.locator('input[name="password"]').fill(password)
        page.locator('button[type="submit"]').click()
        page.wait_for_url(re.compile(rf"^{re.escape(args.base_url)}/?$"))
        page.wait_for_load_state("networkidle")
        results["checks"]["dashboard_desktop"] = _assert_page_contract(page, "dashboard")
        page.screenshot(path=str(output / "dashboard-desktop.png"), full_page=True)

        project_path = _first_project_path(page)
        project_response = page.goto(f"{args.base_url}{project_path}", wait_until="networkidle")
        if project_response is None or project_response.status != 200:
            raise AssertionError("Project detail did not return HTTP 200")
        results["checks"]["project_desktop"] = _assert_page_contract(page, "project detail")
        page.screenshot(path=str(output / "project-desktop.png"), full_page=True)

        page.set_viewport_size({"width": 390, "height": 844})
        page.goto(args.base_url, wait_until="networkidle")
        results["checks"]["dashboard_mobile"] = _assert_page_contract(page, "mobile dashboard")
        page.screenshot(path=str(output / "dashboard-mobile.png"), full_page=True)
        browser.close()

    results["console_errors"] = console_errors
    results["result"] = "PASS" if not console_errors else "FAIL"
    (output / "ui-smoke-result.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    if console_errors:
        raise AssertionError(f"Browser console errors were recorded: {console_errors}")
    print(json.dumps(results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
