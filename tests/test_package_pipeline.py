"""End-to-end tests for the audit deliverable package pipeline (Track D)."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from app import views
from app.domain.constants import RunState, StageStatus, UserRole
from app.domain.models import (
    Artifact,
    AuditRun,
    Client,
    PackageManifest,
    Project,
    RunStage,
    User,
)
from app.domain.storage import open_verified_artifact
from audit_engine.crawler import CrawledPage, CrawlResult
from audit_engine.tasks import run_website_audit
from exporters.tasks import build_audit_package

REPO_ROOT = Path(__file__).resolve().parents[1]
PROGRESS_KEYS = {
    "state", "label", "percent", "pages", "findings",
    "recommendations", "active", "failed", "message",
}


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def exists(self, name: str) -> bool:
        return name in self.objects

    def save(self, name: str, content) -> str:
        self.objects[name] = content.read()
        return name

    def open(self, name: str, mode: str = "rb") -> BytesIO:
        if mode != "rb":
            raise ValueError("Test storage is read-only after save")
        return BytesIO(self.objects[name])


@pytest.fixture
def storage(monkeypatch) -> MemoryStorage:
    memory = MemoryStorage()
    monkeypatch.setattr("app.domain.storage.default_storage", memory)
    return memory


def _seed_run(*, client_name: str = "Aurora Test Homewares", slug: str = "package-client") -> AuditRun:
    user = User.objects.create_user(
        username=f"{slug}-admin",
        password="Package-test-password-9911!",  # noqa: S106 - test credential
        role=UserRole.AGENCY_ADMIN,
        must_change_password=False,
    )
    client = Client.objects.create(name=client_name, slug=slug)
    project = Project.objects.create(
        client=client,
        name=f"{client_name} SEO",
        slug=slug,
        primary_domain="example.com.au",
        approved_domains=["example.com.au"],
        business_type=Project.BusinessType.ECOMMERCE,
    )
    return AuditRun.objects.create(
        project=project,
        profile="quick",
        idempotency_key=f"{slug}-run",
        rule_version="1.0.0",
        created_by=user,
    )


def _run_real_audit(run: AuditRun, monkeypatch) -> None:
    result = CrawlResult(
        pages=(
            CrawledPage(
                requested_url="https://example.com.au/",
                final_url="https://example.com.au/",
                status_code=200, content_type="text/html", body_sha256="a" * 64,
                title="Example home", meta_description=None, h1=("Welcome",),
                canonical_url=None, robots_directives=(),
                links=("https://example.com.au/shop",),
                redirect_chain=("https://example.com.au/",),
            ),
            CrawledPage(
                requested_url="https://example.com.au/shop",
                final_url="https://example.com.au/shop",
                status_code=200, content_type="text/html", body_sha256="b" * 64,
                title=None, meta_description=None, h1=(),
                canonical_url=None, robots_directives=(), links=(),
                redirect_chain=("https://example.com.au/shop",),
            ),
        ),
        failures=(), discovered_count=2, stopped_reason="queue_exhausted",
    )

    class FakeCrawler:
        def __init__(self, config):
            self.config = config

        def crawl(self, seeds):
            return result

    monkeypatch.setattr("audit_engine.tasks.BoundedCrawler", FakeCrawler)
    output = run_website_audit.run(str(run.pk))
    assert output["state"] == RunState.GATE_1_REVIEW


@pytest.mark.django_db
@pytest.mark.render
def test_package_pipeline_builds_verifiable_zip_manifest_and_progress(
    monkeypatch, settings, tmp_path, storage
):
    pytest.importorskip("exporters.run_data", reason="Track A exporters.run_data has not landed yet")
    pytest.importorskip(
        "exporters.xlsx_workbooks", reason="Track B exporters.xlsx_workbooks has not landed yet"
    )
    pytest.importorskip("exporters.pptx_deck", reason="Track C exporters.pptx_deck has not landed yet")
    pytest.importorskip(
        "exporters.markdown_summary", reason="Track C exporters.markdown_summary has not landed yet"
    )
    settings.AUTO_BUILD_PACKAGE = False

    run = _seed_run()
    _run_real_audit(run, monkeypatch)
    run.refresh_from_db()

    result = build_audit_package.run(str(run.pk))
    assert result.get("failed") is not True, f"Package build failed: {result.get('error')}"
    assert result["idempotent"] is False
    assert result["artifact_id"]

    artifact = Artifact.objects.get(pk=result["artifact_id"])
    assert artifact.artifact_type == "package"
    assert artifact.format == "zip"
    assert artifact.sha256 == result["sha256"]
    assert artifact.metadata["run_version"] == run.version

    with open_verified_artifact(artifact) as stream:
        payload = stream.read()
    archive = zipfile.ZipFile(BytesIO(payload))
    names = archive.namelist()
    package_name = artifact.metadata["package_name"]
    assert package_name.startswith("Aurora_Test_Homewares_SEO_Audit_Package_")
    assert all(name.startswith(package_name + "/") for name in names)
    assert f"{package_name}/AUDIT_RESULTS.md" in names
    assert f"{package_name}/06_QA_and_Manifest/package-manifest.json" in names
    assert f"{package_name}/06_QA_and_Manifest/checksums.sha256" in names

    top_folders = {name.split("/")[1] for name in names if name.count("/") >= 2}
    for expected in (
        "01_Audit_Reports",
        "02_Strategy_Documents",
        "03_Action_Plan",
        "04_Implementation_Deliverables",
        "06_QA_and_Manifest",
        "07_Executive_Deck",
    ):
        assert expected in top_folders, f"Missing package folder: {expected}"
    manifest_doc = json.loads(
        archive.read(f"{package_name}/06_QA_and_Manifest/package-manifest.json")
    )
    if any(entry["artifact_type"] == "content_brief" for entry in manifest_doc["files"]):
        assert "05_Content" in top_folders

    from openpyxl import load_workbook
    from pptx import Presentation
    from pypdf import PdfReader

    xlsx_member = next(name for name in names if name.endswith(".xlsx"))
    pdf_member = f"{package_name}/01_Audit_Reports/Enterprise_SEO_Audit_Report.pdf"
    pptx_member = f"{package_name}/07_Executive_Deck/Executive_Deck.pptx"
    assert pdf_member in names
    assert pptx_member in names
    for member in (xlsx_member, pdf_member, pptx_member):
        (tmp_path / Path(member).name).write_bytes(archive.read(member))
    assert load_workbook(tmp_path / Path(xlsx_member).name).sheetnames
    assert len(Presentation(tmp_path / Path(pptx_member).name).slides) >= 1
    assert len(PdfReader(tmp_path / Path(pdf_member).name).pages) >= 1

    manifest_row = PackageManifest.objects.get(run=run, version=run.version)
    assert manifest_row.package_artifact_id == artifact.pk
    assert manifest_row.package_sha256 == artifact.sha256

    progress = views._audit_progress_payload(run)
    assert set(progress) >= PROGRESS_KEYS
    assert progress["percent"] == 100
    assert progress["label"] == "Audit complete — package ready to download"
    assert progress["active"] is False

    repeat = build_audit_package.run(str(run.pk))
    assert repeat["idempotent"] is True
    assert repeat["artifact_id"] == str(artifact.pk)
    assert Artifact.objects.filter(run=run, artifact_type="package").count() == 1


@pytest.mark.django_db
def test_failed_package_build_returns_failure_dict_and_flags_progress(monkeypatch, storage):
    run = _seed_run(slug="package-fail")
    run.state = RunState.GATE_1_REVIEW
    run.save(update_fields=["state", "updated_at"])

    def explode(run_arg, *, actor=None, progress=None):
        if progress is not None:
            progress("Rendering audit workbooks")
        raise RuntimeError("boom: renderer unavailable")

    monkeypatch.setattr("exporters.tasks.build_package_for_run", explode)
    result = build_audit_package.run(str(run.pk))

    assert result["idempotent"] is False
    assert result.get("failed") is True
    assert result["artifact_id"] is None
    assert "boom" in result["error"]

    stage = RunStage.objects.get(run=run, name="packaging")
    assert stage.status == StageStatus.FAILED
    assert "boom" in stage.error_summary
    assert Artifact.objects.filter(run=run, artifact_type="package").count() == 0

    progress = views._audit_progress_payload(run)
    assert set(progress) >= PROGRESS_KEYS
    assert progress["label"] == "Audit ready — package build needs attention"
    assert progress["active"] is False
    assert progress["failed"] is False
    assert "boom" in progress["message"]


@pytest.mark.django_db
def test_progress_payload_reports_packaging_stage_while_running():
    run = _seed_run(slug="package-progress")
    run.state = RunState.GATE_1_REVIEW
    run.save(update_fields=["state", "updated_at"])
    RunStage.objects.create(
        run=run,
        name="packaging",
        sequence=30,
        status=StageStatus.RUNNING,
        checkpoint={"message": "Rendering audit workbooks"},
    )

    progress = views._audit_progress_payload(run)

    assert set(progress) >= PROGRESS_KEYS
    assert progress["percent"] == 90
    assert progress["label"] == "Building the deliverable package"
    assert progress["active"] is True
    assert progress["message"] == "Rendering audit workbooks"


@pytest.mark.render
def test_docx_strategy_title_and_subtitle_follow_the_client_name(tmp_path):
    from docx import Document

    from exporters.docx_reports import DOCXReportBuilder

    data = {
        "client": {"name": "Aurora Homewares", "domain": "aurorahomewares.com.au"},
        "run": {
            "id": "RUN-AURORA-TEST",
            "evidence_as_of": "2026-07-17",
            "overall_score_reason": "Withheld because weighted evidence coverage is below 70%.",
        },
        "executive_summary": "Observed crawl evidence supports a staged technical clean-up.",
        "strategy_sections": [],
        "opportunities": [],
        "measurement_plan": [],
        "sources": [],
        "limitations": ["Private analytics remain unavailable until a connection is approved."],
    }
    output = DOCXReportBuilder(tmp_path).strategy_report(data, tmp_path / "strategy.docx")
    document = Document(output)

    assert document.core_properties.title == "Aurora Homewares Enterprise SEO Strategy"
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "Aurora Homewares" in text
    assert "Kakawa" not in text


@pytest.mark.deployment
def test_embedded_worker_consumes_the_render_queue():
    entrypoint = (REPO_ROOT / "deployment" / "entrypoint.sh").read_text(encoding="utf-8")
    web_block = entrypoint.split("web)", 1)[1].split(";;", 1)[0]
    assert "--queues=analysis,render" in web_block


@pytest.mark.deployment
def test_package_render_dependencies_are_declared():
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dependency_block = pyproject.split("dependencies = [", 1)[1].split("\n]", 1)[0]
    assert '"openpyxl>=' in dependency_block
    assert '"python-pptx>=' in dependency_block
