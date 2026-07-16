"""Cookie-session authentication endpoints with CSRF protection."""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from audit_engine.models import VerifiedFact
from generation import FactPack, GenerationConfig, GenerationPurpose, OpenAIBoundary
from generation.openai_boundary import GenerationStatus
from generation.quality import validate_claims
from integrations.import_service import ImportStorageError, persist_validated_import
from integrations.uploads import ImportLimits, UploadValidationError

from .domain.audit import record_event
from .domain.constants import (
    ApprovalDecision,
    AvailabilityStatus,
    ReviewStatus,
    RunState,
    Severity,
    UserRole,
)
from .domain.models import (
    ActionItem,
    Approval,
    Artifact,
    AuditRun,
    ClaimLedger,
    Client,
    Connection,
    ContentDraft,
    Finding,
    PackageManifest,
    Project,
    QAResult,
    RunStage,
    SourceImport,
)
from .domain.permissions import (
    accessible_projects,
    can_approve_gate,
    can_download_artifact,
    can_manage_project,
    can_review_project,
)
from .domain.storage import ArtifactIntegrityError, open_verified_artifact
from .domain.workflow import (
    ALLOWED_TRANSITIONS,
    ApprovalRequired,
    InvalidTransition,
    QualityGateFailed,
    TransitionConflict,
    decide_approval,
    transition_run,
)
from .errors import error_response


class BrowserLoginForm(forms.Form):
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput)


class BrowserPasswordChangeForm(forms.Form):
    current_password = forms.CharField(widget=forms.PasswordInput)
    new_password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)
    next = forms.CharField(required=False, widget=forms.HiddenInput)

    def clean(self):
        cleaned = super().clean()
        new_password = cleaned.get("new_password")
        confirmation = cleaned.get("confirm_password")
        if new_password and confirmation and new_password != confirmation:
            self.add_error("confirm_password", "The new passwords do not match.")
        return cleaned


def _wants_html(request) -> bool:
    accept = request.headers.get("Accept", "").casefold()
    return request.content_type != "application/json" and "text/html" in accept


def _safe_next(request, value: str, *, fallback: str = "dashboard") -> str:
    candidate = str(value or "").strip()
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return reverse(fallback)


def _browser_login_error(request, message: str, status: int):
    form = BrowserLoginForm(request.POST or None)
    form.add_error(None, message)
    return render(
        request,
        "registration/login.html",
        {"form": form, "next": request.POST.get("next", request.GET.get("next", ""))},
        status=status,
    )


def _payload(request) -> dict:
    if request.content_type == "application/json":
        try:
            value = json.loads(request.body or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return value if isinstance(value, dict) else {}
    return request.POST.dict()


def _client_address(request) -> str:
    # Do not trust X-Forwarded-For here; deployment must normalize REMOTE_ADDR.
    return request.META.get("REMOTE_ADDR", "")[:64]


@require_GET
@ensure_csrf_cookie
def csrf_cookie(request):
    return JsonResponse({"csrf": "set"})


@require_http_methods(["GET", "POST"])
@ensure_csrf_cookie
@csrf_protect
def login_view(request):
    if request.method == "GET":
        if request.user.is_authenticated:
            return redirect(_safe_next(request, request.GET.get("next", "")))
        return render(
            request,
            "registration/login.html",
            {"form": BrowserLoginForm(), "next": request.GET.get("next", "")},
        )

    data = _payload(request)
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    address = _client_address(request)
    throttle_key = f"login:{address}:{username.casefold()[:150]}"
    failures = int(cache.get(throttle_key, 0) or 0)
    if failures >= 8:
        if _wants_html(request):
            return _browser_login_error(
                request,
                "Unable to sign in. Wait a few minutes and try again.",
                429,
            )
        return error_response(
            request,
            "authentication_throttled",
            "Unable to sign in. Wait a few minutes and try again.",
            status=429,
            retryable=True,
        )
    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active:
        cache.set(throttle_key, failures + 1, timeout=900)
        if _wants_html(request):
            return _browser_login_error(request, "Invalid username or password.", 401)
        return error_response(
            request, "invalid_credentials", "Invalid username or password.", status=401
        )
    if user.temporary_password_expires_at and user.temporary_password_expires_at <= timezone.now():
        cache.set(throttle_key, failures + 1, timeout=900)
        if _wants_html(request):
            return _browser_login_error(request, "Invalid username or password.", 401)
        return error_response(
            request, "invalid_credentials", "Invalid username or password.", status=401
        )
    cache.delete(throttle_key)
    login(request, user)
    request.session.cycle_key()
    record_event(request=request, actor=user, event_type="auth.login", object_instance=user)
    if _wants_html(request):
        return redirect(_safe_next(request, data.get("next", "")))
    return JsonResponse(
        {
            "user": {"id": str(user.pk), "username": user.username, "role": user.role},
            "must_change_password": user.must_change_password,
        }
    )


@require_POST
@csrf_protect
@login_required
def logout_view(request):
    user = request.user
    record_event(request=request, actor=user, event_type="auth.logout", object_instance=user)
    logout(request)
    if _wants_html(request):
        return redirect("login")
    return JsonResponse({"status": "signed_out"})


@require_http_methods(["GET", "POST"])
@ensure_csrf_cookie
@csrf_protect
@login_required
def change_password_view(request):
    if request.method == "GET":
        return render(
            request,
            "registration/change_password.html",
            {
                "form": BrowserPasswordChangeForm(initial={"next": request.GET.get("next", "")}),
            },
        )

    wants_html = _wants_html(request)
    form = BrowserPasswordChangeForm(request.POST or None) if wants_html else None
    if form is not None and not form.is_valid():
        return render(
            request, "registration/change_password.html", {"form": form}, status=400
        )
    data = form.cleaned_data if form is not None else _payload(request)
    current = str(data.get("current_password", ""))
    new_password = str(data.get("new_password", ""))
    if not request.user.check_password(current):
        if form is not None:
            form.add_error("current_password", "Current password is incorrect.")
            return render(
                request, "registration/change_password.html", {"form": form}, status=400
            )
        return error_response(
            request, "invalid_current_password", "Current password is incorrect.", status=400
        )
    try:
        validate_password(new_password, user=request.user)
    except ValidationError as exc:
        if form is not None:
            for message in exc.messages:
                form.add_error("new_password", message)
            return render(
                request, "registration/change_password.html", {"form": form}, status=400
            )
        return error_response(
            request,
            "password_validation_failed",
            "The new password does not meet the password policy.",
            status=400,
            field_errors={"new_password": list(exc.messages)},
        )
    if request.user.check_password(new_password):
        if form is not None:
            form.add_error("new_password", "Password must be different from the current password.")
            return render(
                request, "registration/change_password.html", {"form": form}, status=400
            )
        return error_response(
            request,
            "password_reuse",
            "Choose a password different from the current password.",
            status=400,
            field_errors={"new_password": ["Password must be different."]},
        )
    request.user.set_password(new_password)
    request.user.must_change_password = False
    request.user.temporary_password_expires_at = None
    request.user.password_changed_at = timezone.now()
    request.user.save(
        update_fields=[
            "password",
            "must_change_password",
            "temporary_password_expires_at",
            "password_changed_at",
        ]
    )
    update_session_auth_hash(request, request.user)
    request.session.cycle_key()
    record_event(
        request=request,
        actor=request.user,
        event_type="auth.password_changed",
        object_instance=request.user,
    )
    if form is not None:
        messages.success(request, "Your password was changed. Welcome to the studio.")
        return redirect(_safe_next(request, data.get("next", "")))
    return JsonResponse({"status": "password_changed"})


@require_GET
@login_required
def current_user_view(request):
    user = request.user
    return JsonResponse(
        {
            "user": {
                "id": str(user.pk),
                "username": user.username,
                "display_name": user.get_full_name() or user.username,
                "role": user.role,
                "must_change_password": user.must_change_password,
            }
        }
    )


WORKFLOW_STATES = (
    RunState.DRAFT,
    RunState.COLLECTING,
    RunState.AUDITING,
    RunState.GATE_1_REVIEW,
    RunState.PLANNING,
    RunState.GENERATING,
    RunState.GATE_2_REVIEW,
    RunState.FINAL_QA,
    RunState.PACKAGED,
    RunState.APPROVED,
)


def _normalise_domain(value: str) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        raise forms.ValidationError("Enter a domain.")
    parsed = urlsplit(candidate if "://" in candidate else f"https://{candidate}")
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or parsed.username
        or parsed.password
        or parsed.port
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise forms.ValidationError("Enter a hostname or a root HTTP(S) URL.")
    try:
        host = (parsed.hostname or "").encode("idna").decode("ascii").casefold().rstrip(".")
    except UnicodeError as exc:
        raise forms.ValidationError("Enter a valid domain.") from exc
    labels = host.split(".")
    if (
        not host
        or len(host) > 253
        or len(labels) < 2
        or any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or not all(char.isalnum() or char == "-" for char in label)
            for label in labels
        )
    ):
        raise forms.ValidationError("Enter a valid public domain.")
    return host


def _lines(value: str, *, limit: int = 100) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()][:limit]


class ProjectIntakeForm(forms.Form):
    client_name = forms.CharField(max_length=255)
    name = forms.CharField(required=False, max_length=255)
    business_type = forms.ChoiceField(choices=Project.BusinessType.choices)
    locale = forms.ChoiceField(required=False, choices=(("en-AU", "English (Australia)"), ("en-GB", "English (United Kingdom)"), ("en-US", "English (United States)")))
    business_summary = forms.CharField(max_length=5000)
    crawl_data_file = forms.FileField(
        required=False,
        allow_empty_file=False,
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".cdx,.cdd,.xml,application/xml,text/xml",
                "aria-describedby": "crawl-data-help",
            }
        ),
    )
    primary_domain = forms.CharField(max_length=2048)
    approved_domains = forms.CharField(required=False, max_length=10000)
    cms_platform = forms.CharField(required=False, max_length=80)
    primary_market = forms.CharField(required=False, max_length=255)
    conversion_goals = forms.CharField(required=False, max_length=5000)
    priority_offerings = forms.CharField(required=False, max_length=5000)
    competitors = forms.CharField(required=False, max_length=10000)
    verified_facts = forms.CharField(required=False, max_length=20000)
    prohibited_claims = forms.CharField(required=False, max_length=10000)
    brand_voice = forms.CharField(required=False, max_length=255)
    review_owner = forms.CharField(required=False, max_length=255)

    def clean_primary_domain(self):
        return _normalise_domain(self.cleaned_data["primary_domain"])

    def clean_crawl_data_file(self):
        uploaded = self.cleaned_data.get("crawl_data_file")
        if uploaded and uploaded.size > ImportLimits().max_file_bytes:
            raise forms.ValidationError("Upload exceeds the configured 50 MB limit.")
        return uploaded

    def clean(self):
        cleaned = super().clean()
        client_name = str(cleaned.get("client_name") or "").strip()
        initial_name = str(self.initial.get("name") or "").strip()
        generated_name = f"{client_name[:245].rstrip()} SEO Audit" if client_name else ""
        cleaned["name"] = str(cleaned.get("name") or initial_name or generated_name).strip()
        cleaned["locale"] = str(
            cleaned.get("locale") or self.initial.get("locale") or "en-AU"
        )
        initial_goals = self.initial.get("conversion_goals") or ""
        if isinstance(initial_goals, list | tuple):
            initial_goals = "\n".join(str(value) for value in initial_goals)
        cleaned["conversion_goals"] = str(
            cleaned.get("conversion_goals")
            or initial_goals
            or "Improve qualified organic visibility and conversions"
        ).strip()
        approved: list[str] = []
        for value in _lines(cleaned.get("approved_domains", ""), limit=100):
            try:
                approved.append(_normalise_domain(value))
            except forms.ValidationError as exc:
                self.add_error("approved_domains", exc)
        primary = cleaned.get("primary_domain")
        if primary:
            cleaned["domain_allowlist"] = sorted({primary, *approved})
        return cleaned


class ActionCreateForm(forms.Form):
    title = forms.CharField(max_length=500)
    description = forms.CharField(max_length=10000)
    week = forms.IntegerField(min_value=1, max_value=16)
    owner_label = forms.CharField(required=False, max_length=120)
    impact = forms.DecimalField(min_value=0, max_value=100, max_digits=5, decimal_places=2)
    evidence_confidence = forms.DecimalField(min_value=0, max_value=100, max_digits=5, decimal_places=2)
    reach = forms.DecimalField(min_value=0, max_value=100, max_digits=5, decimal_places=2)
    business_criticality = forms.DecimalField(min_value=0, max_value=100, max_digits=5, decimal_places=2)
    dependency_urgency = forms.DecimalField(min_value=0, max_value=100, max_digits=5, decimal_places=2)
    effort = forms.DecimalField(min_value=0, max_value=100, max_digits=5, decimal_places=2)
    risk_class = forms.ChoiceField(choices=ActionItem._meta.get_field("risk_class").choices)


def _project_for_user(request, project_id) -> Project:
    return get_object_or_404(accessible_projects(request.user), pk=project_id)


def _run_for_user(request, run_id) -> AuditRun:
    return get_object_or_404(
        AuditRun.objects.select_related("project", "project__client").filter(
            project__in=accessible_projects(request.user)
        ),
        pk=run_id,
    )


def _require_manage(user, project: Project) -> None:
    if not can_manage_project(user, project):
        raise PermissionDenied("Project management permission is required.")


def _workflow_stages(state: str) -> list[SimpleNamespace]:
    labels = dict(RunState.choices)
    current_index = WORKFLOW_STATES.index(state) if state in WORKFLOW_STATES else -1
    return [
        SimpleNamespace(
            number=index + 1,
            label=labels[value],
            complete=current_index > index,
            current=current_index == index,
        )
        for index, value in enumerate(WORKFLOW_STATES)
    ]


def _unique_slug(queryset, value: str, *, maximum: int = 120) -> str:
    base = (slugify(value) or "project")[:maximum].strip("-") or "project"
    candidate = base
    counter = 2
    while queryset.filter(slug=candidate).exists():
        suffix = f"-{counter}"
        candidate = f"{base[: maximum - len(suffix)]}{suffix}"
        counter += 1
    return candidate


def _project_initial(project: Project) -> dict:
    facts = project.brand_facts if isinstance(project.brand_facts, dict) else {}
    project.business_summary = facts.get("business_summary", "")
    project.primary_market = facts.get("primary_market", "")
    project.priority_offerings = "\n".join(facts.get("priority_offerings", []))
    project.verified_facts = "\n".join(facts.get("verified_facts", []))
    project.brand_voice = facts.get("brand_voice", "")
    return {
        "client_name": project.client.name,
        "name": project.name,
        "business_type": project.business_type,
        "locale": project.locale,
        "business_summary": facts.get("business_summary", ""),
        "primary_domain": f"https://{project.primary_domain}",
        "approved_domains": "\n".join(
            value for value in project.approved_domains if value != project.primary_domain
        ),
        "cms_platform": project.cms_platform,
        "primary_market": facts.get("primary_market", ""),
        "conversion_goals": "\n".join(project.conversion_goals or []),
        "priority_offerings": "\n".join(facts.get("priority_offerings", [])),
        "competitors": "\n".join(facts.get("competitors", [])),
        "verified_facts": "\n".join(facts.get("verified_facts", [])),
        "prohibited_claims": "\n".join(project.prohibited_claims or []),
        "brand_voice": facts.get("brand_voice", ""),
        "review_owner": facts.get("review_owner", ""),
    }


def _apply_project_form(project: Project, form: ProjectIntakeForm) -> Project:
    data = form.cleaned_data
    existing_facts = project.brand_facts if isinstance(project.brand_facts, dict) else {}
    project.name = data["name"]
    project.primary_domain = data["primary_domain"]
    project.approved_domains = data["domain_allowlist"]
    project.locale = data["locale"]
    project.country_code = data["locale"].rsplit("-", 1)[-1].upper()
    project.business_type = data["business_type"]
    project.cms_platform = data["cms_platform"]
    project.conversion_goals = _lines(data["conversion_goals"])
    project.prohibited_claims = _lines(data["prohibited_claims"])
    project.brand_facts = {
        "business_summary": data["business_summary"],
        "primary_market": data["primary_market"],
        "priority_offerings": _lines(data["priority_offerings"]),
        "competitors": _lines(data["competitors"]),
        "verified_facts": _lines(data["verified_facts"], limit=250),
        "brand_voice": data["brand_voice"],
        "review_owner": data["review_owner"],
    }
    if existing_facts.get("ai_intake_brief"):
        project.brand_facts["ai_intake_brief"] = existing_facts["ai_intake_brief"]
    project.full_clean()
    project.save()
    return project

AI_INTAKE_BRIEF_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "audit_focus", "claims", "unavailable_items"],
    "properties": {
        "summary": {"type": "string", "minLength": 20, "maxLength": 1200},
        "audit_focus": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {"type": "string", "minLength": 3, "maxLength": 240},
        },
        "claims": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "fact_keys", "evidence_ids"],
                "properties": {
                    "text": {"type": "string", "minLength": 3, "maxLength": 500},
                    "fact_keys": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 4,
                                    "items": {"type": "string", "minLength": 1, "maxLength": 255},
                    },
                    "evidence_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 4,
                                    "items": {"type": "string", "minLength": 36, "maxLength": 36},
                    },
                },
            },
        },
        "unavailable_items": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "reason"],
                "properties": {
                    "source": {"type": "string", "minLength": 1, "maxLength": 120},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 500},
                },
            },
        },
    },
}


def _generate_ai_intake_brief(*, project: Project, actor=None, request=None) -> str:
    """Create a small evidence-bounded AI brief without blocking project creation."""

    if not settings.OPENAI_INTAKE_GENERATION_ENABLED:
        return "disabled"
    captured_at = timezone.now()
    raw_facts = {
        "client_name": project.client.name,
        "primary_domain": project.primary_domain,
        "business_type": project.business_type,
        "business_summary": project.brand_facts.get("business_summary", ""),
    }
    facts: list[VerifiedFact] = []
    evidence_ids: set[str] = set()
    for key, value in raw_facts.items():
        evidence_id = str(uuid4())
        evidence_ids.add(evidence_id)
        facts.append(VerifiedFact(key, value, (evidence_id,), captured_at))
    fact_pack = FactPack(
        project_id=str(project.pk),
        approved_domains=tuple(project.approved_domains),
        facts=tuple(facts),
        available_evidence_ids=frozenset(evidence_ids),
        unavailable_sources={
            "crawl": "Website crawl has not run yet.",
            "analytics": "Analytics sources have not been connected yet.",
        },
    )
    config = GenerationConfig(
        final_model=settings.OPENAI_STRATEGY_MODEL,
        extraction_model=settings.OPENAI_EXTRACTION_MODEL,
        max_output_tokens=1800,
    )
    try:
        result = OpenAIBoundary(config=config).generate_structured(
            task=(
                "Create a concise SEO audit intake brief and 1-5 evidence-supported audit focus "
                "areas. Do not infer products, locations, performance, rankings, competitors or "
                "metrics. Record missing crawl and analytics evidence under unavailable_items."
            ),
            fact_pack=fact_pack,
            schema_name="seo_intake_brief",
            schema=AI_INTAKE_BRIEF_SCHEMA,
            purpose=GenerationPurpose.EXTRACTION,
        )
        issues = validate_claims(result.data, fact_pack) if result.data else ()
        high_issues = [
            issue
            for issue in issues
            if issue.severity.value in {Severity.CRITICAL, Severity.HIGH}
        ]
        available = result.status is GenerationStatus.AVAILABLE and not high_issues
        payload = {
            "status": "available" if available else result.status.value,
            "requested_model": result.ledger.requested_model,
            "returned_model": result.ledger.returned_model,
            "prompt_version": result.ledger.prompt_version,
            "request_sha256": result.ledger.request_sha256,
            "response_sha256": result.ledger.response_sha256,
            "input_tokens": result.ledger.input_tokens,
            "output_tokens": result.ledger.output_tokens,
            "attempts": result.ledger.attempts,
            "quality_issues": [issue.code for issue in high_issues],
            "unavailable_reason": result.unavailable_reason,
        }
        if available:
            project.brand_facts["ai_intake_brief"] = {
                "generated_at": result.ledger.finished_at.isoformat(),
                "model": result.ledger.returned_model or result.ledger.requested_model,
                "prompt_version": result.ledger.prompt_version,
                "data": dict(result.data or {}),
            }
            project.save(update_fields=["brand_facts", "updated_at"])
        record_event(
            event_type="generation.intake_brief",
            actor=actor,
            request=request,
            object_instance=project,
            payload=payload,
        )
        return "available" if available else "unavailable"
    except Exception:
        record_event(
            event_type="generation.intake_brief",
            actor=actor,
            request=request,
            object_instance=project,
            payload={
                "status": "unavailable",
                "unavailable_reason": "AI brief generation failed safely.",
            },
        )
        return "unavailable"

@login_required

@require_GET
def dashboard_view(request):
    projects = list(accessible_projects(request.user).filter(status=Project.Status.ACTIVE))
    coverages = []
    for project in projects:
        latest = project.audit_runs.first()
        project.evidence_coverage = latest.evidence_coverage if latest else None
        project.profile = project.default_profile
        if latest:
            coverages.append(latest.evidence_coverage)
    project_ids = [project.pk for project in projects]
    pending = list(
        Approval.objects.select_related("run__project")
        .filter(run__project_id__in=project_ids, decision=ApprovalDecision.PENDING)
        .order_by("requested_at")[:10]
    )
    attention_items = [
        SimpleNamespace(
            project=item.run.project,
            severity="review",
            label=item.get_gate_display(),
            summary=f"{item.run.get_state_display()} is waiting for a recorded decision.",
        )
        for item in pending
    ]
    qa_exception_count = QAResult.objects.filter(
        run__project_id__in=project_ids,
        status=QAResult.Status.FAIL,
        severity__in=(Severity.CRITICAL, Severity.HIGH),
    ).count()
    last_heartbeat = RunStage.objects.filter(run__project_id__in=project_ids).aggregate(
        value=Max("heartbeat_at")
    )["value"]
    return render(
        request,
        "app/dashboard.html",
        {
            "today": timezone.localdate(),
            "projects": projects,
            "active_project_count": len(projects),
            "client_count": len({project.client_id for project in projects}),
            "average_coverage": (
                round(sum(coverages) / len(coverages), 1) if coverages else None
            ),
            "pending_approval_count": len(pending),
            "qa_exception_count": qa_exception_count,
            "attention_items": attention_items,
            "worker_health": "Recent heartbeat" if last_heartbeat else "No heartbeat recorded",
            "last_heartbeat": last_heartbeat,
            "export_queue_count": RunStage.objects.filter(
                run__project_id__in=project_ids, name="packaging", status="pending"
            ).count(),
        },
    )


def _project_create_context(form: ProjectIntakeForm) -> dict:
    return {
        "form": form,
        "is_create": True,
        "project": SimpleNamespace(
            pk="00000000-0000-0000-0000-000000000000",
            name="",
            client=SimpleNamespace(name=""),
            business_summary="",
            primary_domain="",
            cms_platform="",
            primary_market="",
            conversion_goals="",
            priority_offerings="",
            verified_facts="",
            prohibited_claims="",
            brand_voice="",
        ),
    }


@login_required
@require_http_methods(["GET", "POST"])
def project_create_view(request):
    if not (request.user.is_superuser or request.user.role == UserRole.AGENCY_ADMIN):
        raise PermissionDenied("Agency administrator permission is required.")
    form = ProjectIntakeForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        crawl_import = None
        try:
            with transaction.atomic():
                client = Client.objects.filter(
                    name__iexact=form.cleaned_data["client_name"]
                ).first()
                if client is None:
                    client = Client.objects.create(
                        name=form.cleaned_data["client_name"],
                        slug=_unique_slug(
                            Client.objects.all(), form.cleaned_data["client_name"]
                        ),
                    )
                project = Project(
                    client=client,
                    name=form.cleaned_data["name"],
                    slug=_unique_slug(client.projects.all(), form.cleaned_data["name"]),
                    primary_domain=form.cleaned_data["primary_domain"],
                    approved_domains=form.cleaned_data["domain_allowlist"],
                    locale=form.cleaned_data["locale"],
                    country_code=form.cleaned_data["locale"].rsplit("-", 1)[-1].upper(),
                    business_type=form.cleaned_data["business_type"],
                    default_profile="standard",
                )
                _apply_project_form(project, form)
                record_event(
                    event_type="project.created",
                    actor=request.user,
                    request=request,
                    object_instance=project,
                )
                uploaded = form.cleaned_data.get("crawl_data_file")
                if uploaded:
                    crawl_import, created = persist_validated_import(
                        project=project,
                        actor=request.user,
                        source_type="crawl_data_file",
                        uploaded=uploaded,
                    )
                    record_event(
                        event_type=(
                            "source_import.accepted"
                            if created
                            else "source_import.idempotent"
                        ),
                        actor=request.user,
                        request=request,
                        project=project,
                        object_instance=crawl_import,
                        payload={
                            "source_type": crawl_import.source_type,
                            "sha256": crawl_import.sha256,
                            "origin": "project_setup",
                        },
                    )
        except UploadValidationError as exc:
            form.add_error("crawl_data_file", exc.safe_message)
            messages.error(
                request,
                "The crawl file failed validation. No project or upload was created.",
            )
            return render(
                request,
                "app/project_intake.html",
                _project_create_context(form),
                status=400,
            )
        except ImportStorageError:
            form.add_error(
                "crawl_data_file",
                "Private storage verification failed. No project or upload was created.",
            )
            messages.error(request, "The crawl file could not be stored safely.")
            return render(
                request,
                "app/project_intake.html",
                _project_create_context(form),
                status=503,
            )
        ai_status = _generate_ai_intake_brief(
            project=project, actor=request.user, request=request
        )
        if crawl_import and ai_status == "available":
            messages.success(
                request,
                "Project created, crawl data imported, and its AI audit brief is ready.",
            )
        elif crawl_import:
            messages.success(
                request,
                "Project created and crawl data imported. AI generation can be retried later.",
            )
        elif ai_status == "available":
            messages.success(request, "Project created and its AI audit brief is ready.")
        else:
            messages.success(request, "Project created. AI generation can be retried later.")
        target = "project-sources" if request.POST.get("continue") == "sources" else "project-detail"
        return redirect(target, project_id=project.pk)
    status = 400 if request.method == "POST" else 200
    return render(
        request,
        "app/project_intake.html",
        _project_create_context(form),
        status=status,
    )

@login_required
@require_http_methods(["GET", "POST"])
def project_intake_view(request, project_id):
    project = _project_for_user(request, project_id)
    _require_manage(request.user, project)
    form = ProjectIntakeForm(
        request.POST or None, request.FILES or None, initial=_project_initial(project)
    )
    if request.method == "POST" and form.is_valid():
        if request.user.is_superuser or request.user.role == UserRole.AGENCY_ADMIN:
            project.client.name = form.cleaned_data["client_name"]
            project.client.save(update_fields=["name", "updated_at"])
        _apply_project_form(project, form)
        record_event(
            event_type="project.intake_updated",
            actor=request.user,
            request=request,
            object_instance=project,
        )
        messages.success(request, "Project intake updated and versioned in the audit trail.")
        target = "project-sources" if request.POST.get("continue") == "sources" else "project-detail"
        return redirect(target, project_id=project.pk)
    return render(
        request,
        "app/project_intake.html",
        {"form": form, "project": project, "is_create": False},
        status=400 if request.method == "POST" else 200,
    )


def _project_detail_context(project: Project) -> dict:
    latest = project.audit_runs.prefetch_related("findings", "actions", "approvals").first()
    project.profile = project.default_profile
    current_approval = None
    priority_findings = []
    if latest:
        approval = latest.approvals.filter(decision=ApprovalDecision.PENDING).first()
        if approval:
            current_approval = SimpleNamespace(
                label=approval.get_gate_display(), summary="A versioned decision is pending."
            )
        for finding in latest.findings.filter(status=Finding.Status.OPEN)[:5]:
            priority_findings.append(
                SimpleNamespace(
                    title=finding.title,
                    category=finding.category,
                    priority={Severity.CRITICAL: "P1", Severity.HIGH: "P1", Severity.MEDIUM: "P2"}.get(finding.severity, "P3"),
                    evidence_count=finding.evidence.count(),
                    confidence=finding.confidence,
                )
            )
    source_summary = [
        SimpleNamespace(
            label=item.get_provider_display(),
            status=item.availability,
            detail=item.unavailable_reason or "Connection metadata is available.",
        )
        for item in project.connections.all()
    ]
    return {
        "project": project,
        "latest_run": latest,
        "short_summary": _project_short_summary(project, latest),
        "evidence_coverage": latest.evidence_coverage if latest else None,
        "health_score": latest.health_score if latest else None,
        "ruleset_version": latest.rule_version if latest else None,
        "open_findings_count": latest.findings.filter(status=Finding.Status.OPEN).count() if latest else 0,
        "critical_high_count": latest.findings.filter(severity__in=(Severity.CRITICAL, Severity.HIGH)).count() if latest else 0,
        "approved_actions_count": latest.actions.filter(review_status=ReviewStatus.APPROVED).count() if latest else 0,
        "action_count": latest.actions.count() if latest else 0,
        "workflow_stages": _workflow_stages(latest.state) if latest else [],
        "priority_findings": priority_findings,
        "source_summary": source_summary,
        "current_approval": current_approval,
    }


def _project_short_summary(project: Project, latest: AuditRun | None) -> str:
    facts = project.brand_facts if isinstance(project.brand_facts, dict) else {}
    ai_brief = facts.get("ai_intake_brief")
    if isinstance(ai_brief, dict):
        data = ai_brief.get("data")
        if isinstance(data, dict) and str(data.get("summary") or "").strip():
            return str(data["summary"]).strip()
    if latest:
        score = (
            f"health score {latest.health_score}%"
            if latest.health_score is not None
            else "health score withheld pending sufficient evidence"
        )
        return (
            f"Latest audit: {latest.evidence_coverage}% evidence coverage, "
            f"{latest.findings.count()} findings, {latest.actions.count()} actions, and {score}."
        )
    return str(
        facts.get("business_summary") or f"SEO audit workspace for {project.primary_domain}."
    ).strip()


def _latest_downloadable_artifact(user, project: Project) -> Artifact | None:
    artifacts = (
        Artifact.objects.select_related("run__project__client")
        .filter(run__project=project)
        .order_by("-created_at")
    )
    for artifact_type in ("package", "final_package", "run_summary_html"):
        for artifact in artifacts.filter(artifact_type=artifact_type):
            if can_download_artifact(user, artifact):
                return artifact
    for artifact in artifacts:
        if can_download_artifact(user, artifact):
            return artifact
    return None


def _audit_download_available(user, project: Project, latest: AuditRun | None) -> bool:
    return bool(
        _latest_downloadable_artifact(user, project)
        or (latest and can_manage_project(user, project))
    )


@login_required
@require_GET
def project_detail_view(request, project_id):
    project = _project_for_user(request, project_id)
    context = _project_detail_context(project)
    context["audit_download_available"] = _audit_download_available(
        request.user, project, context["latest_run"]
    )
    return render(request, "app/project_detail.html", context)


class SourceConnectionForm(forms.Form):
    provider = forms.ChoiceField(choices=Connection.Provider.choices)
    label = forms.CharField(required=False, max_length=120)
    unavailable_reason = forms.CharField(max_length=2000)


class EvidenceUploadForm(forms.Form):
    SOURCE_CHOICES = (
        ("ahrefs", "Ahrefs"),
        ("screaming_frog", "Screaming Frog"),
        ("brightlocal_gbp", "BrightLocal / GBP"),
        ("mapped_csv_xlsx", "Mapped CSV or XLSX"),
        ("crawl_data_file", "CDX / CDD / XML crawl data"),
    )

    source_type = forms.ChoiceField(choices=SOURCE_CHOICES)
    as_of_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    evidence_file = forms.FileField(
        allow_empty_file=False,
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".csv,.xlsx,.cdx,.cdd,.xml,text/csv,application/xml,text/xml,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "aria-describedby": "upload-help",
            }
        ),
    )

    def clean_evidence_file(self):
        uploaded = self.cleaned_data["evidence_file"]
        if uploaded.size > ImportLimits().max_file_bytes:
            raise forms.ValidationError("Upload exceeds the configured 50 MB limit.")
        return uploaded


def _prepare_upload_errors(form: EvidenceUploadForm) -> None:
    first = True
    for field_name in form.errors:
        if field_name == forms.forms.NON_FIELD_ERRORS or field_name not in form.fields:
            continue
        widget = form.fields[field_name].widget
        error_id = f"id_{field_name}_error"
        described_by = str(widget.attrs.get("aria-describedby", "")).split()
        if error_id not in described_by:
            described_by.append(error_id)
        widget.attrs["aria-describedby"] = " ".join(described_by)
        widget.attrs["aria-invalid"] = "true"
        if first:
            widget.attrs["autofocus"] = True
            first = False


def _source_context(
    project: Project,
    *,
    selected_source=None,
    selected_import=None,
    upload_form=None,
    show_upload_form: bool = False,
) -> dict:
    live_sources = list(project.connections.all())
    for source in live_sources:
        source.status = source.availability
        source.last_captured_at = source.last_synced_at
        source.scope_summary = ", ".join(source.scopes or [])
    imports = list(project.source_imports.all())
    for item in imports:
        item.display_name = item.original_filename
        item.filename = item.original_filename
        item.captured_at = item.created_at
        mapping = item.column_mapping if isinstance(item.column_mapping, dict) else {}
        item.row_count = mapping.get("row_count")
    latest = project.audit_runs.first()
    unavailable = [
        SimpleNamespace(
            label=source.get_provider_display(),
            unavailable_reason=source.unavailable_reason,
            as_of_date=source.updated_at,
        )
        for source in live_sources
        if source.availability == AvailabilityStatus.UNAVAILABLE
    ]
    available_count = sum(
        source.availability == AvailabilityStatus.AVAILABLE for source in live_sources
    ) + sum(item.availability == AvailabilityStatus.AVAILABLE for item in imports)
    partial_count = sum(
        source.availability in {AvailabilityStatus.PENDING, AvailabilityStatus.ERROR}
        for source in live_sources
    )
    return {
        "project": project,
        "live_sources": live_sources,
        "imports": imports,
        "available_source_count": available_count,
        "partial_source_count": partial_count,
        "unavailable_source_count": len(unavailable),
        "evidence_coverage": latest.evidence_coverage if latest else None,
        "unavailable_sources": unavailable,
        "selected_source": selected_source,
        "selected_import": selected_import,
        "upload_form": upload_form,
        "show_upload_form": show_upload_form,
    }


@login_required
@require_GET
def project_sources_view(request, project_id):
    project = _project_for_user(request, project_id)
    return render(request, "app/project_sources.html", _source_context(project))


@login_required
@require_http_methods(["GET", "POST"])
def source_connect_view(request, project_id):
    project = _project_for_user(request, project_id)
    _require_manage(request.user, project)
    if request.method == "GET":
        messages.info(
            request,
            "Connections begin unavailable until encrypted credentials are supplied and verified.",
        )
        return render(request, "app/project_sources.html", _source_context(project))
    form = SourceConnectionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a provider and record why evidence is not yet available.")
        return render(
            request,
            "app/project_sources.html",
            {**_source_context(project), "connection_form": form},
            status=400,
        )
    source, created = Connection.objects.get_or_create(
        project=project,
        provider=form.cleaned_data["provider"],
        label=form.cleaned_data["label"],
        defaults={
            "availability": AvailabilityStatus.UNAVAILABLE,
            "unavailable_reason": form.cleaned_data["unavailable_reason"],
        },
    )
    if not created:
        messages.info(request, "That connection record already exists; no duplicate was created.")
    else:
        record_event(
            event_type="connection.created_unavailable",
            actor=request.user,
            request=request,
            project=project,
            payload={"provider": source.provider},
        )
        messages.success(request, "Connection record created with a truthful unavailable state.")
    return redirect("project-sources", project_id=project.pk)


@login_required
@require_http_methods(["GET", "POST"])
def source_upload_view(request, project_id):
    project = _project_for_user(request, project_id)
    _require_manage(request.user, project)
    form = EvidenceUploadForm(request.POST or None, request.FILES or None)
    if request.method == "GET":
        messages.info(
            request,
            "CSV, XLSX, CDX, CDD, and XML evidence is quarantined, scanned, and stored privately after validation.",
        )
        return render(
            request,
            "app/project_sources.html",
            _source_context(project, upload_form=form, show_upload_form=True),
        )
    if not form.is_valid():
        _prepare_upload_errors(form)
        messages.error(request, "No file was accepted. Correct the upload fields and try again.")
        return render(
            request,
            "app/project_sources.html",
            _source_context(project, upload_form=form, show_upload_form=True),
            status=400,
        )
    try:
        item, created = persist_validated_import(
            project=project,
            actor=request.user,
            source_type=form.cleaned_data["source_type"],
            uploaded=form.cleaned_data["evidence_file"],
            as_of_date=(
                form.cleaned_data["as_of_date"].isoformat()
                if form.cleaned_data.get("as_of_date")
                else None
            ),
        )
    except UploadValidationError as exc:
        form.add_error("evidence_file", exc.safe_message)
        _prepare_upload_errors(form)
        record_event(
            event_type="source_import.rejected",
            actor=request.user,
            request=request,
            project=project,
            payload={"code": exc.code, "source_type": form.cleaned_data["source_type"]},
        )
        messages.error(request, "The file failed quarantine validation and was not accepted.")
        return render(
            request,
            "app/project_sources.html",
            _source_context(project, upload_form=form, show_upload_form=True),
            status=400,
        )
    except ImportStorageError:
        form.add_error(None, "Private storage verification failed. No import was activated.")
        _prepare_upload_errors(form)
        messages.error(request, "The validated file could not be stored safely.")
        return render(
            request,
            "app/project_sources.html",
            _source_context(project, upload_form=form, show_upload_form=True),
            status=503,
        )
    record_event(
        event_type="source_import.accepted" if created else "source_import.idempotent",
        actor=request.user,
        request=request,
        project=project,
        object_instance=item,
        payload={"source_type": item.source_type, "sha256": item.sha256},
    )
    messages.success(
        request,
        "Evidence import accepted." if created else "This exact evidence import already exists.",
    )
    return redirect("source-import-detail", project_id=project.pk, import_id=item.pk)


@login_required
@require_GET
def source_detail_view(request, project_id, source_id):
    project = _project_for_user(request, project_id)
    source = get_object_or_404(Connection, pk=source_id, project=project)
    return render(
        request,
        "app/project_sources.html",
        _source_context(project, selected_source=source),
    )


@login_required
@require_GET
def source_import_detail_view(request, project_id, import_id):
    project = _project_for_user(request, project_id)
    item = get_object_or_404(SourceImport, pk=import_id, project=project)
    return render(
        request,
        "app/project_sources.html",
        _source_context(project, selected_import=item),
    )


@login_required
@require_POST
def source_refresh_view(request, project_id, source_id):
    project = _project_for_user(request, project_id)
    _require_manage(request.user, project)
    source = get_object_or_404(Connection, pk=source_id, project=project)
    record_event(
        event_type="connection.refresh_requested",
        actor=request.user,
        request=request,
        project=project,
        payload={"connection_id": str(source.pk), "provider": source.provider},
    )
    messages.info(
        request,
        "Refresh request recorded. Existing evidence remains unchanged until a verified capture completes.",
    )
    return redirect("project-sources", project_id=project.pk)


def _finding_context(project: Project, request, *, selected=None) -> dict:
    latest = project.audit_runs.first()
    queryset = latest.findings.all() if latest else Finding.objects.none()
    query = request.GET.get("q", "").strip()[:200]
    severity = request.GET.get("severity", "").strip().casefold()
    category = request.GET.get("category", "").strip()[:60]
    if query:
        queryset = queryset.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(code__icontains=query)
        )
    if severity in Severity.values:
        queryset = queryset.filter(severity=severity)
    if category:
        queryset = queryset.filter(category=category)
    findings = list(queryset.prefetch_related("evidence", "recommendations"))
    audited_count = latest.pages.count() if latest else 0
    for finding in findings:
        records = list(finding.evidence.all())
        for evidence in records:
            evidence.reference = str(evidence.pk)
            evidence.source_label = evidence.title
            evidence.get_absolute_url = reverse(
                "finding-detail", args=(project.pk, finding.pk)
            )
        recommendation = finding.recommendations.first()
        finding.priority = {
            Severity.CRITICAL: "P1",
            Severity.HIGH: "P1",
            Severity.MEDIUM: "P2",
            Severity.LOW: "P3",
        }.get(finding.severity, "P4")
        finding.audited_count = audited_count
        finding.affected_share = round(Decimal(finding.affected_share) * 100, 1)
        finding.evidence_count = len(records)
        finding.as_of_date = max((item.captured_at for item in records), default=None)
        finding.reference = finding.code
        finding.summary = finding.description
        finding.impact_statement = (
            recommendation.rationale if recommendation else "Impact awaits analyst review."
        )
        finding.recommendation = SimpleNamespace(
            summary=recommendation.implementation if recommendation else "Recommendation pending."
        )
        finding.evidence_records = records
        finding.evidence_excerpt = records[0].excerpt if records else ""
        finding.evidence_source = records[0].title if records else ""
        finding.rule_id = finding.code
    selected_finding = selected
    if selected_finding and all(item.pk != selected_finding.pk for item in findings):
        findings.append(selected_finding)
        return _finding_context(project, request, selected=None) | {"selected_finding": selected_finding}
    categories = [
        SimpleNamespace(slug=value, label=value.replace("_", " ").title())
        for value in sorted({item.category for item in findings})
    ]
    severity_counts = {
        value: (latest.findings.filter(severity=value).count() if latest else 0)
        for value in Severity.values
    }
    return {
        "project": project,
        "findings": findings,
        "selected_finding": selected_finding,
        "ruleset_version": latest.rule_version if latest else None,
        "severity_counts": severity_counts,
        "evidence_count": latest.evidence.count() if latest else 0,
        "evidence_coverage": latest.evidence_coverage if latest else None,
        "unavailable_category_count": 0,
        "filters": SimpleNamespace(q=query),
        "categories": categories,
    }


@login_required
@require_GET
def project_findings_view(request, project_id):
    project = _project_for_user(request, project_id)
    return render(request, "app/findings.html", _finding_context(project, request))


@login_required
@require_GET
def finding_detail_view(request, project_id, finding_id):
    project = _project_for_user(request, project_id)
    selected = get_object_or_404(
        Finding.objects.select_related("run").prefetch_related("evidence", "recommendations"),
        pk=finding_id,
        run__project=project,
    )
    context = _finding_context(project, request)
    selected = next((item for item in context["findings"] if item.pk == selected.pk), selected)
    context["selected_finding"] = selected
    return render(request, "app/findings.html", context)


def _action_context(project: Project, request, *, selected=None) -> dict:
    latest = project.audit_runs.first()
    queryset = latest.actions.all() if latest else ActionItem.objects.none()
    query = request.GET.get("q", "").strip()[:200]
    priority = request.GET.get("priority", "").strip().upper()
    week = request.GET.get("week", "").strip()
    if query:
        queryset = queryset.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(owner_label__icontains=query)
        )
    if priority in {"P1", "P2", "P3", "P4"}:
        queryset = queryset.filter(priority_tier=priority)
    if week.isdigit() and 1 <= int(week) <= 16:
        queryset = queryset.filter(week=int(week))
    actions = list(queryset.select_related("recommendation__finding").prefetch_related("dependencies"))
    workstreams: dict[str, list[ActionItem]] = defaultdict(list)
    for action in actions:
        action.priority = action.priority_tier
        action.start_week = action.week
        action.end_week = action.week
        action.owner_name = action.owner_label
        action.category = (
            action.recommendation.finding.category if action.recommendation_id else "General"
        )
        action.finding_count = 1 if action.recommendation_id else 0
        action.confidence = action.evidence_confidence
        action.dependency_count = action.dependencies.count()
        action.status = action.review_status
        workstreams[action.category].append(action)
    workstream_rows = []
    for label, grouped in sorted(workstreams.items()):
        workstream_rows.append(
            SimpleNamespace(
                label=label,
                cells=[
                    SimpleNamespace(
                        active=bool(active := [item for item in grouped if item.week == number]),
                        action_count=len(active),
                        approval_required=any(
                            item.risk_class in {"high", "dangerous"} for item in active
                        ),
                    )
                    for number in range(1, 17)
                ],
            )
        )
    all_actions = latest.actions.all() if latest else ActionItem.objects.none()
    priority_counts = {
        tier.casefold(): all_actions.filter(priority_tier=tier).count()
        for tier in ("P1", "P2", "P3", "P4")
    }
    return {
        "project": project,
        "plan": SimpleNamespace(version=latest.version if latest else 1),
        "actions": actions,
        "selected_action": selected,
        "can_edit": bool(latest and can_manage_project(request.user, project)),
        "action_count": all_actions.count(),
        "priority_counts": priority_counts,
        "risky_action_count": all_actions.filter(risk_class__in=("high", "dangerous")).count(),
        "finding_coverage": None,
        "weeks": list(range(1, 17)),
        "default_week_labels": [],
        "workstreams": workstream_rows,
    }


@login_required
@require_GET
def action_plan_view(request, project_id):
    project = _project_for_user(request, project_id)
    return render(request, "app/action_plan.html", _action_context(project, request))


@login_required
@require_http_methods(["GET", "POST"])
def action_create_view(request, project_id):
    project = _project_for_user(request, project_id)
    _require_manage(request.user, project)
    latest = project.audit_runs.first()
    if not latest:
        messages.error(request, "Start an audit run before creating evidence-backed actions.")
        return redirect("project-detail", project_id=project.pk)
    if request.method == "GET":
        messages.info(request, "Submit actions only after their evidence and owner are defined.")
        return render(request, "app/action_plan.html", _action_context(project, request))
    form = ActionCreateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "The action was not created; correct the bounded scoring fields.")
        return render(
            request,
            "app/action_plan.html",
            {**_action_context(project, request), "action_form": form},
            status=400,
        )
    values = form.cleaned_data
    score = (
        values["impact"] * Decimal("0.30")
        + values["evidence_confidence"] * Decimal("0.20")
        + values["reach"] * Decimal("0.15")
        + values["business_criticality"] * Decimal("0.15")
        + values["dependency_urgency"] * Decimal("0.10")
        + (Decimal("100") - values["effort"]) * Decimal("0.10")
    )
    tier = "P1" if score >= 75 else "P2" if score >= 50 else "P3" if score >= 25 else "P4"
    action = ActionItem.objects.create(
        run=latest,
        title=values["title"],
        description=values["description"],
        week=values["week"],
        owner_label=values["owner_label"],
        impact=values["impact"],
        evidence_confidence=values["evidence_confidence"],
        reach=values["reach"],
        business_criticality=values["business_criticality"],
        dependency_urgency=values["dependency_urgency"],
        effort=values["effort"],
        priority_score=score,
        priority_tier=tier,
        risk_class=values["risk_class"],
    )
    record_event(
        event_type="action.created",
        actor=request.user,
        request=request,
        run=latest,
        object_instance=latest,
        payload={"action_id": str(action.pk), "priority": tier},
    )
    messages.success(request, "Evidence-backed action added to the canonical plan.")
    return redirect("action-detail", project_id=project.pk, action_id=action.pk)


@login_required
@require_GET
def action_detail_view(request, project_id, action_id):
    project = _project_for_user(request, project_id)
    action = get_object_or_404(ActionItem, pk=action_id, run__project=project)
    return render(
        request,
        "app/action_plan.html",
        _action_context(project, request, selected=action),
    )


def _decorate_approval(approval: Approval) -> Approval:
    approval.status = approval.decision
    approval.title = f"{approval.get_gate_display()} decision"
    approval.summary = f"Review run version {approval.run.version} before recording a decision."
    approval.subject_version = approval.run.version
    approval.submitted_at = approval.requested_at
    approval.submitted_by_name = (
        approval.requested_by.get_full_name() or approval.requested_by.username
        if approval.requested_by
        else "System"
    )
    approval.note = approval.comment
    approval.decided_by_name = (
        approval.reviewed_by.get_full_name() or approval.reviewed_by.username
        if approval.reviewed_by
        else "Reviewer"
    )
    return approval


def _approval_context(project: Project, request) -> dict:
    approvals = list(
        Approval.objects.select_related(
            "run", "artifact", "requested_by", "reviewed_by"
        ).filter(run__project=project)
    )
    for approval in approvals:
        _decorate_approval(approval)
    current = next(
        (item for item in approvals if item.decision == ApprovalDecision.PENDING), None
    )
    evidence = []
    if current and current.artifact:
        evidence.append(
            SimpleNamespace(
                label=current.artifact.title,
                summary="Immutable artifact bytes identified by SHA-256.",
                version=current.run.version,
                evidence_count=current.run.evidence.count(),
                risk_class=current.artifact.risk_class,
                detail_url=reverse("export-qa", args=(project.pk,)),
            )
        )
    can_decide = bool(
        current and can_approve_gate(request.user, current.run, current.gate)
    )
    return {
        "project": project,
        "current_approval": current,
        "approval_evidence": evidence,
        "can_decide": can_decide,
        "approval_history": [
            item for item in approvals if item.decision != ApprovalDecision.PENDING
        ],
        "approval_capability": (
            "Record an immutable decision" if can_decide else "View versioned decisions"
        ),
    }


@login_required
@require_GET
def project_approvals_view(request, project_id):
    project = _project_for_user(request, project_id)
    return render(request, "app/approvals.html", _approval_context(project, request))


@login_required
@require_POST
def approval_decide_view(request, project_id, approval_id):
    project = _project_for_user(request, project_id)
    approval = get_object_or_404(
        Approval.objects.select_related("run__project"),
        pk=approval_id,
        run__project=project,
    )
    if not can_approve_gate(request.user, approval.run, approval.gate):
        raise PermissionDenied("Approval permission is required.")
    decision = request.POST.get("decision", "")
    note = request.POST.get("note", "").strip()
    if (
        decision not in {ApprovalDecision.APPROVED, ApprovalDecision.REVISION_REQUESTED}
        or not note
        or len(note) > 5000
        or request.POST.get("acknowledge_no_publish") != "yes"
    ):
        messages.error(
            request,
            "Choose a decision, add a bounded decision note, and acknowledge that nothing is published.",
        )
        return redirect("project-approvals", project_id=project.pk)
    try:
        decide_approval(
            approval=approval,
            decision=decision,
            actor=request.user,
            expected_run_version=approval.run.version,
            comment=note,
            request=request,
        )
    except (TransitionConflict, PermissionError, ValueError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Immutable approval decision recorded.")
    return redirect("project-approvals", project_id=project.pk)


def _content_context(project: Project, draft: ContentDraft, request) -> dict:
    brief = draft.brief
    claims = list(draft.claims.prefetch_related("evidence"))
    supported = 0
    for claim in claims:
        records = list(claim.evidence.all())
        claim.text = claim.claim_text
        claim.is_supported = claim.status == ClaimLedger.ClaimStatus.SUPPORTED and bool(records)
        claim.source_label = records[0].title if records else ""
        claim.unavailable_reason = "" if records else "No source attached"
        claim.as_of_date = records[0].captured_at if records else None
        if claim.is_supported:
            supported += 1
    draft.title = brief.title
    draft.target_url = brief.target_url
    draft.search_intent = brief.search_intent
    draft.primary_keyword = brief.primary_keyword
    draft.status = draft.review_status
    draft.reference = str(draft.pk)[:8]
    draft.locale = project.locale
    draft.claim_count = len(claims)
    draft.supported_claim_count = supported
    draft.word_count = len(draft.body.split())
    draft.generation = SimpleNamespace(
        model_id=draft.model_id, prompt_version=draft.prompt_version
    )
    draft.generated_at = draft.created_at
    fact_pack = brief.approved_fact_pack if isinstance(brief.approved_fact_pack, dict) else {}
    draft.audience = fact_pack.get("audience", "Not defined")
    draft.conversion_goal = fact_pack.get("conversion_goal", "Not defined")
    links = []
    for item in fact_pack.get("internal_links", []) if isinstance(fact_pack.get("internal_links", []), list) else []:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target_url", ""))[:2048]
        try:
            host = (urlsplit(target).hostname or "").casefold().rstrip(".")
        except ValueError:
            host = ""
        links.append(
            SimpleNamespace(
                target_url=target,
                anchor_text=str(item.get("anchor_text", "Internal link"))[:255],
                is_valid=host in set(project.approved_domains),
            )
        )
    draft.valid_internal_link_count = sum(item.is_valid for item in links)
    draft.invalid_internal_link_count = sum(not item.is_valid for item in links)
    all_claims_supported = all(item.is_supported for item in claims)
    return {
        "project": project,
        "content": draft,
        "claims": claims,
        "comments": [],
        "internal_links": links,
        "can_approve": bool(
            can_review_project(request.user, project)
            and all_claims_supported
            and draft.review_status != ReviewStatus.APPROVED
        ),
    }


def _content_for_project(project: Project, content_id) -> ContentDraft:
    return get_object_or_404(
        ContentDraft.objects.select_related("brief__run__project").prefetch_related(
            "claims__evidence"
        ),
        pk=content_id,
        brief__run__project=project,
    )


@login_required
@require_GET
def content_list_view(request, project_id):
    project = _project_for_user(request, project_id)
    draft = (
        ContentDraft.objects.filter(brief__run__project=project)
        .order_by("-created_at")
        .first()
    )
    if draft is None:
        messages.info(request, "No content draft is available for review.")
        return redirect("project-detail", project_id=project.pk)
    return redirect("content-detail", project_id=project.pk, content_id=draft.pk)


@login_required
@require_GET
def content_detail_view(request, project_id, content_id):
    project = _project_for_user(request, project_id)
    draft = _content_for_project(project, content_id)
    return render(request, "app/content_review.html", _content_context(project, draft, request))


@login_required
@require_POST
def content_approve_view(request, project_id, content_id):
    project = _project_for_user(request, project_id)
    if not can_review_project(request.user, project):
        raise PermissionDenied("Content review permission is required.")
    with transaction.atomic():
        draft = get_object_or_404(
            ContentDraft.objects.select_for_update().select_related("brief__run"),
            pk=content_id,
            brief__run__project=project,
        )
        blocked = draft.claims.exclude(
            status=ClaimLedger.ClaimStatus.SUPPORTED
        ).exists() or draft.claims.filter(evidence=None).exists()
        if blocked:
            messages.error(request, "Unsupported or unlinked claims block content approval.")
            return redirect("content-detail", project_id=project.pk, content_id=draft.pk)
        if draft.review_status != ReviewStatus.APPROVED:
            draft.review_status = ReviewStatus.APPROVED
            draft.save(update_fields=["review_status", "updated_at"])
            record_event(
                event_type="content.approved",
                actor=request.user,
                request=request,
                project=project,
                run=draft.brief.run,
                object_instance=draft.brief.run,
                payload={"content_id": str(draft.pk), "version": draft.version},
            )
    messages.success(request, "This exact content version is approved.")
    return redirect("content-detail", project_id=project.pk, content_id=draft.pk)


@login_required
@require_POST
def content_revision_view(request, project_id, content_id):
    project = _project_for_user(request, project_id)
    if not can_review_project(request.user, project):
        raise PermissionDenied("Content review permission is required.")
    comment = request.POST.get("comment", "").strip()
    if len(comment) < 3 or len(comment) > 5000:
        messages.error(request, "Add a review note between 3 and 5,000 characters.")
        return redirect("content-detail", project_id=project.pk, content_id=content_id)
    with transaction.atomic():
        draft = get_object_or_404(
            ContentDraft.objects.select_for_update().select_related("brief__run"),
            pk=content_id,
            brief__run__project=project,
        )
        draft.review_status = ReviewStatus.REVISION_REQUESTED
        draft.save(update_fields=["review_status", "updated_at"])
        record_event(
            event_type="content.revision_requested",
            actor=request.user,
            request=request,
            project=project,
            run=draft.brief.run,
            object_instance=draft.brief.run,
            payload={"content_id": str(draft.pk), "version": draft.version, "comment": comment},
        )
    messages.success(request, "Revision request recorded in the immutable audit trail.")
    return redirect("content-detail", project_id=project.pk, content_id=draft.pk)


def _run_context(run: AuditRun, request) -> dict:
    stages = list(run.stages.all())
    for stage in stages:
        stage.label = stage.name.replace("_", " ").title()
        stage.duration_display = None
        stage.output_count = (stage.checkpoint or {}).get("output_count", 0)
        stage.summary = "Checkpoint preserved" if stage.checkpoint else "No exceptions"
    progress = {
        state: round(index * 100 / (len(WORKFLOW_STATES) - 1))
        for index, state in enumerate(WORKFLOW_STATES)
    }.get(run.state, 0)
    run.reference = str(run.pk)[:8]
    run.status = run.state
    run.ruleset_version = run.rule_version
    run.progress_percent = progress
    run.pages_discovered = run.pages.count()
    run.pages_audited = run.pages.filter(status_code__isnull=False).count()
    run.evidence_count = run.evidence.count()
    run.source_snapshot_count = run.source_snapshots.count()
    run.findings_count = run.findings.count()
    run.critical_high_count = run.findings.filter(
        severity__in=(Severity.CRITICAL, Severity.HIGH)
    ).count()
    run.last_heartbeat_at = max(
        (stage.heartbeat_at for stage in stages if stage.heartbeat_at), default=None
    )
    run.retry_count = sum(stage.attempts for stage in stages)
    run.failure_message = run.error_summary
    run.can_cancel = bool(
        RunState.CANCELLED in ALLOWED_TRANSITIONS.get(run.state, set())
        and can_manage_project(request.user, run.project)
    )
    run.can_resume = bool(
        run.state in {RunState.FAILED, RunState.REVISION_REQUESTED}
        and can_manage_project(request.user, run.project)
    )
    events = list(run.audit_events.select_related("actor")[:12])
    for event in events:
        event.label = event.event_type.replace(".", " ").title()
        event.summary = "Recorded in the immutable run ledger."
    return {
        "run": run,
        "workflow_stages": _workflow_stages(run.state),
        "run_stages": stages,
        "recent_events": events,
        "request_id": getattr(request, "request_id", ""),
    }


@login_required
@require_GET
def run_detail_view(request, run_id):
    run = _run_for_user(request, run_id)
    return render(request, "app/run_detail.html", _run_context(run, request))


def _workflow_message(request, exc: Exception) -> None:
    messages.error(request, str(exc))


@login_required
@require_POST
def run_cancel_view(request, run_id):
    run = _run_for_user(request, run_id)
    _require_manage(request.user, run.project)
    try:
        transition_run(
            run=run,
            to_state=RunState.CANCELLED,
            actor=request.user,
            expected_version=run.version,
            reason="Cancelled from the studio UI.",
            request=request,
        )
    except (TransitionConflict, InvalidTransition, ApprovalRequired, QualityGateFailed, PermissionError, ValueError) as exc:
        _workflow_message(request, exc)
    else:
        messages.success(request, "Run cancelled; captured evidence remains preserved.")
    return redirect("run-detail", run_id=run.pk)


@login_required
@require_POST
def run_resume_view(request, run_id):
    run = _run_for_user(request, run_id)
    _require_manage(request.user, run.project)
    failed_stage = run.stages.filter(status="failed").order_by("-sequence").first()
    stage_target = {
        "collecting": RunState.COLLECTING,
        "auditing": RunState.AUDITING,
        "planning": RunState.PLANNING,
        "generating": RunState.GENERATING,
        "final_qa": RunState.FINAL_QA,
        "packaging": RunState.FINAL_QA,
    }
    candidates = ALLOWED_TRANSITIONS.get(run.state, set())
    preferred = stage_target.get(failed_stage.name if failed_stage else "", RunState.PLANNING)
    target = preferred if preferred in candidates else next(
        (value for value in (RunState.PLANNING, RunState.GENERATING, RunState.FINAL_QA, RunState.COLLECTING, RunState.AUDITING) if value in candidates),
        None,
    )
    if target is None:
        messages.error(request, "This run state cannot be resumed.")
        return redirect("run-detail", run_id=run.pk)
    try:
        updated = transition_run(
            run=run,
            to_state=target,
            actor=request.user,
            expected_version=run.version,
            reason="Resumed from the latest stored checkpoint.",
            request=request,
        )
    except (TransitionConflict, InvalidTransition, ApprovalRequired, QualityGateFailed, PermissionError, ValueError) as exc:
        _workflow_message(request, exc)
    else:
        updated.error_code = ""
        updated.error_summary = ""
        updated.save(update_fields=["error_code", "error_summary", "updated_at"])
        messages.success(request, "Run returned to its checkpointed stage for worker pickup.")
    return redirect("run-detail", run_id=run.pk)


def _export_context(project: Project, request) -> dict:
    latest_run = project.audit_runs.first()
    manifests = (
        PackageManifest.objects.select_related("run", "package_artifact")
        .filter(run__project=project)
        .order_by("-created_at")
    )
    latest_package = manifests.first()
    if latest_package:
        latest_package.is_downloadable = bool(
            latest_package.package_artifact
            and can_download_artifact(request.user, latest_package.package_artifact)
        )
        latest_package.sha256 = latest_package.package_sha256
        latest_package.run.reference = str(latest_package.run_id)[:8]
    artifacts = list(
        Artifact.objects.filter(run__project=project).order_by("artifact_type", "title")
    )
    grouped: dict[str, list[Artifact]] = defaultdict(list)
    for artifact in artifacts:
        artifact.display_name = artifact.title
        artifact.relative_path = artifact.storage_key
        artifact.version = artifact.metadata.get("version", 1)
        artifact.render_status = artifact.review_status
        grouped[artifact.artifact_type].append(artifact)
    artifact_groups = [
        SimpleNamespace(label=label.replace("_", " ").title(), artifacts=items)
        for label, items in grouped.items()
    ]
    qa_queryset = latest_run.qa_results.all() if latest_run else QAResult.objects.none()
    blocking = qa_queryset.filter(
        status=QAResult.Status.FAIL,
        severity__in=(Severity.CRITICAL, Severity.HIGH),
    ).count()
    passed = qa_queryset.filter(status=QAResult.Status.PASS).count()
    warning = qa_queryset.filter(status=QAResult.Status.WARN).count()
    total = qa_queryset.count()
    qa_results = [
        SimpleNamespace(
            status={QAResult.Status.PASS: "passed", QAResult.Status.FAIL: "failed", QAResult.Status.WARN: "warning"}.get(item.status, "skipped"),
            label=item.check_code,
            detail=item.message,
            category=item.severity,
        )
        for item in qa_queryset
    ]
    ready = bool(latest_run and not blocking and total)
    return {
        "project": project,
        "short_summary": _project_short_summary(project, latest_run),
        "audit_download_available": _audit_download_available(
            request.user, project, latest_run
        ),
        "latest_package": latest_package,
        "can_build": bool(
            ready
            and latest_run.state == RunState.FINAL_QA
            and can_manage_project(request.user, project)
        ),
        "qa_counts": {"passed": passed, "warning": warning, "blocking": blocking},
        "artifact_count": len(artifacts),
        "total_package_size": sum(item.size_bytes for item in artifacts),
        "package_ready": ready,
        "qa_pass_percent": round(passed * 100 / total) if total else 0,
        "blocking_summary": (
            f"{blocking} critical or high QA checks must be resolved." if blocking else ""
        ),
        "artifact_groups": artifact_groups,
        "qa_results": qa_results,
    }


@login_required
@require_GET
def export_qa_view(request, project_id):
    project = _project_for_user(request, project_id)
    return render(request, "app/export_qa.html", _export_context(project, request))


@login_required
@require_POST
def export_build_view(request, project_id):
    project = _project_for_user(request, project_id)
    _require_manage(request.user, project)
    run = project.audit_runs.first()
    if not run or run.state != RunState.FINAL_QA:
        messages.error(request, "A run at Final QA is required before packaging can be queued.")
        return redirect("export-qa", project_id=project.pk)
    blocking = run.qa_results.filter(
        status=QAResult.Status.FAIL,
        severity__in=(Severity.CRITICAL, Severity.HIGH),
    ).exists()
    risky = run.artifacts.filter(approval_required=True).exclude(
        review_status=ReviewStatus.APPROVED
    ).exists()
    if blocking or risky:
        messages.error(request, "Blocking QA or unapproved risky assets prevent packaging.")
        return redirect("export-qa", project_id=project.pk)
    stage, created = RunStage.objects.get_or_create(
        run=run, name="packaging", defaults={"sequence": 60, "status": "pending"}
    )
    record_event(
        event_type="export.build_requested",
        actor=request.user,
        request=request,
        run=run,
        object_instance=run,
        payload={"stage_id": str(stage.pk), "created": created},
    )
    messages.success(
        request,
        "Verified package build queued; no website or external platform was changed.",
    )
    return redirect("export-qa", project_id=project.pk)


@login_required
@require_GET
def export_download_view(request, project_id, package_id):
    project = _project_for_user(request, project_id)
    manifest = get_object_or_404(
        PackageManifest.objects.select_related("package_artifact", "run"),
        pk=package_id,
        run__project=project,
        package_artifact__isnull=False,
    )
    artifact = manifest.package_artifact
    if not can_download_artifact(request.user, artifact):
        raise PermissionDenied("This artifact is not approved for download.")
    try:
        stream = open_verified_artifact(artifact)
    except ArtifactIntegrityError:
        messages.error(request, "Artifact integrity verification failed; download was blocked.")
        return redirect("export-qa", project_id=project.pk)
    record_event(
        event_type="artifact.downloaded",
        actor=request.user,
        request=request,
        run=manifest.run,
        object_instance=manifest.run,
        payload={"artifact_id": str(artifact.pk)},
    )
    suffix = artifact.format.casefold() if artifact.format else "zip"
    filename = f"{slugify(artifact.title) or 'verified-package'}.{suffix}"
    response = FileResponse(
        stream,
        as_attachment=True,
        filename=filename,
        content_type=artifact.media_type or "application/octet-stream",
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_GET
def audit_results_download_view(request, project_id):
    """Download the best available audit result without weakening approval controls."""

    project = _project_for_user(request, project_id)
    artifact = _latest_downloadable_artifact(request.user, project)
    if artifact:
        try:
            stream = open_verified_artifact(artifact)
        except ArtifactIntegrityError:
            messages.error(request, "Artifact integrity verification failed; download was blocked.")
            return redirect("project-detail", project_id=project.pk)
        record_event(
            event_type="audit.results_downloaded",
            actor=request.user,
            request=request,
            run=artifact.run,
            object_instance=artifact,
            payload={"artifact_id": str(artifact.pk), "format": artifact.format},
        )
        suffix = artifact.format.casefold() if artifact.format else "zip"
        filename = f"{slugify(artifact.title) or project.slug + '-audit-results'}.{suffix}"
        response = FileResponse(
            stream,
            as_attachment=True,
            filename=filename,
            content_type=artifact.media_type or "application/octet-stream",
        )
        response["X-Content-Type-Options"] = "nosniff"
        return response

    run = project.audit_runs.first()
    if not run or not can_manage_project(request.user, project):
        raise PermissionDenied("No approved audit result is available for download.")
    from exporters.tasks import _render_run_summary

    payload = _render_run_summary(run)
    record_event(
        event_type="audit.results_summary_downloaded",
        actor=request.user,
        request=request,
        run=run,
        object_instance=run,
        payload={"format": "html", "fallback": True},
    )
    response = FileResponse(
        BytesIO(payload),
        as_attachment=True,
        filename=f"{project.slug}-audit-results.html",
        content_type="text/html; charset=utf-8",
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response
