from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

REQUIRED_DOCUMENTS = {
    "ARCHITECTURE.md": (
        "Process topology",
        "Canonical evidence model",
        "Resumable workflow",
        "Missing provider credentials",
    ),
    "SECURITY.md": (
        "Roles and permissions",
        "DNS and socket validation",
        "File and import security",
        "forced change",
    ),
    "OPERATIONS.md": (
        "Staging deployment",
        "Production promotion",
        "Retry and resume",
        "Backup scheduling",
    ),
    "RECOVERY.md": (
        "Restore-test procedure",
        "Point-in-time recovery procedure",
        "Full environment recovery",
        "Recovery acceptance gate",
    ),
    "API.md": (
        "/api/v1/",
        "Idempotency-Key",
        "expected_version",
        "/readyz/",
    ),
    "KAKAWA_ACCEPTANCE.md": (
        "not an acceptance certificate",
        "Source availability matrix",
        "Global hard gates",
        "V18 negative-regression checks",
    ),
    "DATA_DICTIONARY.md": (
        "Availability",
        "Evidence metadata",
        "AuditRun",
        "Reconciliation invariants",
    ),
    "THREAT_MODEL.md": (
        "Trust boundaries",
        "Threat register",
        "prompt injection",
        "Current release gates and residual risks",
    ),
}


def test_required_documentation_is_present_and_substantive() -> None:
    for filename, required_phrases in REQUIRED_DOCUMENTS.items():
        path = DOCS / filename
        assert path.is_file(), f"Missing required documentation: {filename}"
        text = path.read_text(encoding="utf-8")
        assert len(text) >= 2_000, f"{filename} is unexpectedly short"
        assert "\x00" not in text
        for phrase in required_phrases:
            assert phrase.casefold() in text.casefold(), f"{filename} omits {phrase!r}"


def test_documentation_does_not_embed_machine_specific_windows_paths() -> None:
    drive_path = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\")
    for filename in REQUIRED_DOCUMENTS:
        text = (DOCS / filename).read_text(encoding="utf-8")
        assert drive_path.search(text) is None, f"{filename} contains a machine-specific path"


def test_health_contract_matches_django_and_railway_descriptors() -> None:
    django_urls = (ROOT / "app" / "urls.py").read_text(encoding="utf-8")
    assert 'path("healthz/"' in django_urls
    assert 'path("readyz/"' in django_urls

    descriptors = (
        ROOT / "deployment" / "railway.json",
        ROOT / "deployment" / "railway.staging.json",
        ROOT / "deployment" / "railway.production.json",
    )
    for descriptor in descriptors:
        value = json.loads(descriptor.read_text(encoding="utf-8"))
        assert value["deploy"]["healthcheckPath"] == "/readyz/", (
            f"{descriptor.name} must use the implemented readiness endpoint"
        )


def test_kakawa_contract_does_not_claim_unverified_live_success() -> None:
    text = (DOCS / "KAKAWA_ACCEPTANCE.md").read_text(encoding="utf-8").casefold()
    assert "not a claim that a live run occurred" in text
    assert "credentials are deployment inputs" in text
    assert "not evaluated" in text
