from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.template.loader import get_template

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"

PAGE_TEMPLATES = (
    "base.html",
    "registration/login.html",
    "app/dashboard.html",
    "app/project_detail.html",
    "app/project_intake.html",
    "app/project_sources.html",
    "app/findings.html",
    "app/run_detail.html",
    "app/content_review.html",
    "app/action_plan.html",
    "app/approvals.html",
    "app/export_qa.html",
    "403.html",
    "404.html",
    "500.html",
    "error.html",
)


@pytest.mark.parametrize("template_name", PAGE_TEMPLATES)
def test_page_template_compiles(template_name: str) -> None:
    """Catch invalid tags, filters, inheritance, and missing template assets."""
    get_template(template_name)


def test_shell_has_keyboard_and_landmark_foundations() -> None:
    shell = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert 'class="skip-link"' in shell
    assert 'href="#main-content"' in shell
    assert '<main id="main-content"' in shell
    assert 'aria-label="Primary navigation"' in shell
    assert "data-nav-toggle" in shell


def test_login_fields_have_explicit_accessible_labels() -> None:
    login = (TEMPLATES / "registration" / "login.html").read_text(encoding="utf-8")
    assert '<label for="id_username">' in login
    assert '<label for="id_password">' in login
    assert 'autocomplete="username"' in login
    assert 'autocomplete="current-password"' in login
    assert 'role="alert"' in login


def test_templates_do_not_embed_machine_paths_or_external_assets() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8") for path in TEMPLATES.rglob("*.html")
    )
    assert "/home/" not in combined
    assert "C:\\" not in combined
    assert "cdn.jsdelivr" not in combined
    assert "fonts.googleapis" not in combined
    assert not re.search(r"\son(?:click|change|submit|keydown)=", combined, re.IGNORECASE)


def test_design_system_avoids_banned_visual_shortcuts() -> None:
    css = (STATIC / "css" / "studio.css").read_text(encoding="utf-8")
    assert "background-clip: text" not in css
    assert "linear-gradient(" not in css
    assert "radial-gradient(" not in css
    assert not re.search(r"border-(?:left|right):\s*[2-9]", css)
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ":focus-visible" in css
    assert "oklch(" in css


def test_progress_indicators_expose_accessible_values() -> None:
    run_template = (TEMPLATES / "app" / "run_detail.html").read_text(encoding="utf-8")
    qa_template = (TEMPLATES / "app" / "export_qa.html").read_text(encoding="utf-8")
    for template in (run_template, qa_template):
        assert 'role="progressbar"' in template
        assert 'aria-valuemin="0"' in template
        assert 'aria-valuemax="100"' in template
        assert "aria-valuenow=" in template


def test_local_static_assets_exist() -> None:
    assert (STATIC / "css" / "studio.css").is_file()
    assert (STATIC / "js" / "studio.js").is_file()
    assert (STATIC / "img" / "traffic-radius-mark.svg").is_file()
