"""Build a fresh public-only Kakawa crawl snapshot.

This utility intentionally does not infer analytics or commercial-provider data.
It crawls only the approved ``.com.au`` domain and records explicit unavailable
states for evidence that requires credentials or a separately approved run.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from audit_engine.crawler import BoundedCrawler, CrawlConfig

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "fixtures" / "replay" / "kakawa_runtime_snapshot.json"
APPROVED_DOMAIN = "kakawachocolates.com.au"
SEED = f"https://{APPROVED_DOMAIN}/"


def unavailable_sources() -> dict[str, dict[str, str]]:
    environment_names = {
        "gsc": "GOOGLE_SEARCH_CONSOLE_ACCESS_TOKEN",
        "ga4": "GOOGLE_ANALYTICS_ACCESS_TOKEN",
        "semrush": "SEMRUSH_API_KEY",
        "pagespeed": "PAGESPEED_API_KEY",
        "openai": "OPENAI_API_KEY",
    }
    result: dict[str, dict[str, str]] = {}
    for source, environment_name in environment_names.items():
        configured = bool(os.getenv(environment_name, "").strip())
        result[source] = {
            "status": "unavailable",
            "reason": (
                "configured_but_not_collected_by_public_snapshot_builder"
                if configured
                else "credential_not_configured"
            ),
        }
    return result


def build_snapshot(*, max_pages: int, max_duration: float) -> dict[str, Any]:
    captured = datetime.now(UTC)
    crawler = BoundedCrawler(
        CrawlConfig(
            allowed_domains=(APPROVED_DOMAIN,),
            max_pages=max_pages,
            max_depth=8,
            max_duration_seconds=max_duration,
            request_timeout_seconds=20,
            max_body_bytes=5_000_000,
            max_redirects=5,
            min_host_delay_seconds=0.75,
            user_agent=(
                "Mozilla/5.0 (compatible; TrafficRadiusAudit/1.0; +https://trafficradius.com.au/)"
            ),
            obey_robots=True,
        )
    )
    result = crawler.crawl((SEED,))
    return {
        "fixture_version": "1.0.0",
        "fixture_kind": "runtime_public_crawl",
        "captured_at": captured.isoformat(),
        "as_of_date": captured.date().isoformat(),
        "project": {
            "name": "Kakawa Chocolates acceptance benchmark",
            "approved_domains": [APPROVED_DOMAIN],
            "seed_url": SEED,
        },
        "crawl": {
            "pages": [asdict(page) for page in result.pages],
            "failures": [asdict(failure) for failure in result.failures],
            "discovered_count": result.discovered_count,
            "stopped_reason": result.stopped_reason,
        },
        "sources": unavailable_sources(),
        "integrity_note": (
            "Only public crawl observations are present. Missing private or provider "
            "sources remain unavailable and no replacement metrics are inferred."
        ),
    }


def write_snapshot(snapshot: dict[str, Any], output: Path) -> None:
    resolved_parent = output.parent.resolve()
    if not resolved_parent.is_relative_to(PROJECT_ROOT):
        raise ValueError("Output must remain inside the project root")
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(snapshot, ensure_ascii=False, allow_nan=False, indent=2)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=output.parent,
        prefix=output.name + ".",
        suffix=".tmp",
    ) as handle:
        handle.write(serialized)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-pages", type=int, default=25_000)
    parser.add_argument("--max-duration", type=float, default=900)
    args = parser.parse_args()
    if not 1 <= args.max_pages <= 25_000:
        parser.error("--max-pages must be between 1 and 25000")
    if not 1 <= args.max_duration <= 7_200:
        parser.error("--max-duration must be between 1 and 7200 seconds")
    write_snapshot(
        build_snapshot(max_pages=args.max_pages, max_duration=args.max_duration),
        args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
