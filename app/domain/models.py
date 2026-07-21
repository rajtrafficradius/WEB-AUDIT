# ruff: noqa: DJ008, DJ012
"""Canonical, evidence-first relational model for enterprise SEO work."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .constants import (
    ApprovalDecision,
    ApprovalGate,
    AvailabilityStatus,
    ReviewStatus,
    RiskClass,
    RunProfile,
    RunState,
    Severity,
    StageStatus,
    UserRole,
)
from .managers import UserManager

PERCENTAGE_VALIDATORS = [MinValueValidator(0), MaxValueValidator(100)]
CONFIDENCE_VALIDATORS = [MinValueValidator(0), MaxValueValidator(1)]


class UUIDTimeStampedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AvailabilityMixin(models.Model):
    availability = models.CharField(
        max_length=20,
        choices=AvailabilityStatus.choices,
        default=AvailabilityStatus.PENDING,
        db_index=True,
    )
    unavailable_reason = models.TextField(blank=True)

    class Meta:
        abstract = True
        constraints = [
            models.CheckConstraint(
                condition=~Q(availability=AvailabilityStatus.UNAVAILABLE)
                | ~Q(unavailable_reason=""),
                name="%(app_label)s_%(class)s_unavailable_reason",
            )
        ]


class EvidenceMetadataMixin(models.Model):
    captured_at = models.DateTimeField(default=timezone.now, db_index=True)
    locale = models.CharField(max_length=35, blank=True)
    device = models.CharField(max_length=20, blank=True)
    scope = models.CharField(max_length=255, blank=True)
    rule_version = models.CharField(max_length=80, blank=True)
    confidence = models.DecimalField(
        max_digits=5,
        decimal_places=4,
        default=0,
        validators=CONFIDENCE_VALIDATORS,
    )

    class Meta:
        abstract = True


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.CharField(
        max_length=24, choices=UserRole.choices, default=UserRole.CLIENT_REVIEWER, db_index=True
    )
    must_change_password = models.BooleanField(default=True)
    temporary_password_expires_at = models.DateTimeField(null=True, blank=True)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    class Meta:
        ordering = ("username",)

    @property
    def is_agency_admin(self) -> bool:
        return self.is_superuser or self.role == UserRole.AGENCY_ADMIN


class Client(UUIDTimeStampedModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=120, unique=True)
    brand_name = models.CharField(max_length=255, blank=True)
    logo_storage_key = models.CharField(max_length=1024, blank=True)
    primary_colour = models.CharField(max_length=16, default="#17324D")
    accent_colour = models.CharField(max_length=16, default="#D97A32")
    retention_days = models.PositiveIntegerField(default=365)
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class Project(UUIDTimeStampedModel):
    class BusinessType(models.TextChoices):
        SERVICE = "service", "Service"
        SAAS = "saas", "SaaS"
        LOCAL = "local", "Local"
        ECOMMERCE = "ecommerce", "Ecommerce"
        HYBRID = "hybrid", "Hybrid"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        ARCHIVED = "archived", "Archived"

    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="projects")
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=120)
    primary_domain = models.CharField(max_length=253)
    approved_domains = models.JSONField(default=list)
    locale = models.CharField(max_length=35, default="en-AU")
    country_code = models.CharField(max_length=2, default="AU")
    business_type = models.CharField(max_length=20, choices=BusinessType.choices)
    default_profile = models.CharField(
        max_length=20, choices=RunProfile.choices, default=RunProfile.STANDARD
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    conversion_goals = models.JSONField(default=list, blank=True)
    brand_facts = models.JSONField(default=dict, blank=True)
    prohibited_claims = models.JSONField(default=list, blank=True)
    cms_platform = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ("client__name", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("client", "slug"), name="domain_project_client_slug_unique"
            )
        ]
        indexes = [models.Index(fields=("client", "status"))]

    def clean(self):
        super().clean()
        primary = self.primary_domain.strip().lower().rstrip(".")
        approved = {
            str(value).strip().lower().rstrip(".") for value in (self.approved_domains or [])
        }
        if primary and primary not in approved:
            raise ValidationError(
                {"approved_domains": "The primary domain must be included in approved domains."}
            )

    def __str__(self) -> str:
        return f"{self.client}: {self.name}"


class Membership(UUIDTimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="memberships")
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.CASCADE, related_name="memberships"
    )
    access_role = models.CharField(max_length=24, choices=UserRole.choices)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("user", "client"),
                condition=Q(project__isnull=True),
                name="domain_membership_client_scope_unique",
            ),
            models.UniqueConstraint(
                fields=("user", "project"),
                condition=Q(project__isnull=False),
                name="domain_membership_project_scope_unique",
            ),
        ]
        indexes = [
            models.Index(fields=("user", "is_active")),
            models.Index(fields=("client", "project")),
        ]

    def clean(self):
        super().clean()
        if self.project_id and self.project.client_id != self.client_id:
            raise ValidationError({"project": "The project must belong to the selected client."})


class Connection(UUIDTimeStampedModel, AvailabilityMixin):
    class Provider(models.TextChoices):
        GSC = "gsc", "Google Search Console"
        GA4 = "ga4", "Google Analytics 4"
        SEMRUSH = "semrush", "SEMrush"
        PAGESPEED = "pagespeed", "PageSpeed Insights"
        S3 = "s3", "S3-compatible storage"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="connections")
    provider = models.CharField(max_length=40, choices=Provider.choices)
    label = models.CharField(max_length=120, blank=True)
    encrypted_credentials = models.TextField(blank=True)
    encryption_key_id = models.CharField(max_length=80, blank=True)
    external_account_id = models.CharField(max_length=255, blank=True)
    scopes = models.JSONField(default=list, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("project", "provider", "label"), name="domain_connection_label_unique"
            ),
            models.CheckConstraint(
                condition=~Q(availability=AvailabilityStatus.UNAVAILABLE)
                | ~Q(unavailable_reason=""),
                name="domain_connection_unavailable_reason",
            ),
        ]
        indexes = [models.Index(fields=("project", "provider", "availability"))]


class ManagedCredential(UUIDTimeStampedModel):
    """An organisation-wide API credential applied to every project by default.

    One row per provider. The secret is encrypted at rest with the same
    envelope as per-project Connections; only a short masked hint and the
    encryption key id are ever readable. This is the deployment-wide default,
    overridden by a per-project Connection when one carries its own key.
    """

    provider = models.CharField(max_length=40, choices=Connection.Provider.choices, unique=True)
    encrypted_credentials = models.TextField(blank=True)
    encryption_key_id = models.CharField(max_length=80, blank=True)
    credential_hint = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    updated_by = models.ForeignKey(
        "domain.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="managed_credentials",
    )

    class Meta:
        ordering = ("provider",)

    def __str__(self) -> str:  # pragma: no cover - admin display only
        return f"{self.get_provider_display()} organisation credential"


class SourceImport(UUIDTimeStampedModel, AvailabilityMixin):
    class Status(models.TextChoices):
        QUARANTINED = "quarantined", "Quarantined"
        VALIDATING = "validating", "Validating"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="source_imports")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="source_imports",
    )
    source_type = models.CharField(max_length=60)
    original_filename = models.CharField(max_length=255)
    media_type = models.CharField(max_length=100)
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, db_index=True)
    storage_key = models.CharField(max_length=1024)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.QUARANTINED, db_index=True
    )
    schema_version = models.CharField(max_length=80, blank=True)
    column_mapping = models.JSONField(default=dict, blank=True)
    validation_issues = models.JSONField(default=list, blank=True)

    class Meta:
        indexes = [models.Index(fields=("project", "source_type", "status"))]
        constraints = [
            models.UniqueConstraint(
                fields=("project", "source_type", "sha256", "schema_version"),
                name="domain_sourceimport_digest_version_unique",
            ),
            models.CheckConstraint(
                condition=~Q(availability=AvailabilityStatus.UNAVAILABLE)
                | ~Q(unavailable_reason=""),
                name="domain_sourceimport_unavailable_reason",
            )
        ]


class AuditRun(UUIDTimeStampedModel):
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="audit_runs")
    profile = models.CharField(max_length=20, choices=RunProfile.choices)
    state = models.CharField(
        max_length=30, choices=RunState.choices, default=RunState.DRAFT, db_index=True
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="audit_runs"
    )
    idempotency_key = models.CharField(max_length=128)
    version = models.PositiveIntegerField(default=1)
    rule_version = models.CharField(max_length=80)
    source_cutoff_at = models.DateTimeField(null=True, blank=True)
    evidence_coverage = models.DecimalField(
        max_digits=5, decimal_places=2, default=0, validators=PERCENTAGE_VALIDATORS
    )
    confidence = models.DecimalField(
        max_digits=5, decimal_places=4, default=0, validators=CONFIDENCE_VALIDATORS
    )
    health_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, validators=PERCENTAGE_VALIDATORS
    )
    error_code = models.CharField(max_length=80, blank=True)
    error_summary = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("project", "idempotency_key"), name="domain_run_idempotency_unique"
            ),
            models.CheckConstraint(
                condition=Q(health_score__isnull=True) | Q(evidence_coverage__gte=70),
                name="domain_run_score_requires_coverage",
            ),
        ]
        indexes = [models.Index(fields=("project", "state", "-created_at"))]


class RunStage(UUIDTimeStampedModel):
    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="stages")
    name = models.CharField(max_length=80)
    status = models.CharField(
        max_length=20, choices=StageStatus.choices, default=StageStatus.PENDING, db_index=True
    )
    sequence = models.PositiveSmallIntegerField(default=0)
    attempts = models.PositiveSmallIntegerField(default=0)
    checkpoint = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    error_summary = models.TextField(blank=True)

    class Meta:
        ordering = ("sequence", "created_at")
        constraints = [
            models.UniqueConstraint(fields=("run", "name"), name="domain_stage_name_unique")
        ]


class SourceSnapshot(UUIDTimeStampedModel, AvailabilityMixin, EvidenceMetadataMixin):
    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="source_snapshots")
    source_type = models.CharField(max_length=60)
    source_import = models.ForeignKey(
        SourceImport, null=True, blank=True, on_delete=models.SET_NULL, related_name="snapshots"
    )
    connection = models.ForeignKey(
        Connection, null=True, blank=True, on_delete=models.SET_NULL, related_name="snapshots"
    )
    storage_key = models.CharField(max_length=1024, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    record_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=("run", "source_type", "availability"))]
        constraints = [
            models.CheckConstraint(
                condition=~Q(availability=AvailabilityStatus.UNAVAILABLE)
                | ~Q(unavailable_reason=""),
                name="domain_sourcesnapshot_unavailable_reason",
            )
        ]


class PageSnapshot(UUIDTimeStampedModel, EvidenceMetadataMixin):
    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="pages")
    source_snapshot = models.ForeignKey(
        SourceSnapshot, null=True, blank=True, on_delete=models.SET_NULL, related_name="pages"
    )
    original_url = models.URLField(max_length=2048)
    normalized_url = models.URLField(max_length=2048)
    domain = models.CharField(max_length=253, db_index=True)
    approved_domain = models.BooleanField(default=False, db_index=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    content_type = models.CharField(max_length=120, blank=True)
    canonical_url = models.URLField(max_length=2048, blank=True)
    redirect_target_url = models.URLField(max_length=2048, blank=True)
    robots_indexable = models.BooleanField(null=True, blank=True)
    title = models.TextField(blank=True)
    meta_description = models.TextField(blank=True)
    h1 = models.TextField(blank=True)
    content_sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    response_ms = models.PositiveIntegerField(null=True, blank=True)
    facts = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("run", "normalized_url"), name="domain_page_normalized_unique"
            )
        ]
        indexes = [models.Index(fields=("run", "approved_domain", "status_code"))]


class MetricObservation(UUIDTimeStampedModel, AvailabilityMixin, EvidenceMetadataMixin):
    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="metrics")
    source_snapshot = models.ForeignKey(
        SourceSnapshot, null=True, blank=True, on_delete=models.SET_NULL, related_name="metrics"
    )
    page = models.ForeignKey(
        PageSnapshot, null=True, blank=True, on_delete=models.CASCADE, related_name="metrics"
    )
    metric_key = models.CharField(max_length=120)
    numeric_value = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    text_value = models.TextField(blank=True)
    json_value = models.JSONField(null=True, blank=True)
    unit = models.CharField(max_length=40, blank=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=("run", "metric_key", "captured_at"))]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(availability=AvailabilityStatus.UNAVAILABLE)
                    | Q(numeric_value__isnull=False)
                    | ~Q(text_value="")
                    | Q(json_value__isnull=False)
                ),
                name="domain_metric_value_or_unavailable",
            ),
            models.CheckConstraint(
                condition=~Q(availability=AvailabilityStatus.UNAVAILABLE)
                | ~Q(unavailable_reason=""),
                name="domain_metric_unavailable_reason",
            ),
        ]


class Evidence(UUIDTimeStampedModel, AvailabilityMixin, EvidenceMetadataMixin):
    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="evidence")
    source_snapshot = models.ForeignKey(
        SourceSnapshot, null=True, blank=True, on_delete=models.SET_NULL, related_name="evidence"
    )
    page = models.ForeignKey(
        PageSnapshot, null=True, blank=True, on_delete=models.CASCADE, related_name="evidence"
    )
    evidence_type = models.CharField(max_length=80, db_index=True)
    title = models.CharField(max_length=255)
    excerpt = models.TextField(blank=True)
    locator = models.CharField(max_length=1024, blank=True)
    storage_key = models.CharField(max_length=1024, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-captured_at",)
        indexes = [models.Index(fields=("run", "evidence_type", "availability"))]
        constraints = [
            models.CheckConstraint(
                condition=~Q(availability=AvailabilityStatus.UNAVAILABLE)
                | ~Q(unavailable_reason=""),
                name="domain_evidence_unavailable_reason",
            )
        ]


class Finding(UUIDTimeStampedModel):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ACCEPTED = "accepted", "Accepted"
        RESOLVED = "resolved", "Resolved"
        DISMISSED = "dismissed", "Dismissed"

    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="findings")
    page = models.ForeignKey(
        PageSnapshot, null=True, blank=True, on_delete=models.CASCADE, related_name="findings"
    )
    category = models.CharField(max_length=60, db_index=True)
    code = models.CharField(max_length=120)
    title = models.CharField(max_length=255)
    description = models.TextField()
    severity = models.CharField(max_length=20, choices=Severity.choices, db_index=True)
    affected_count = models.PositiveIntegerField(default=0)
    affected_share = models.DecimalField(
        max_digits=5, decimal_places=4, default=0, validators=CONFIDENCE_VALIDATORS
    )
    score_penalty = models.DecimalField(
        max_digits=7, decimal_places=3, default=0, validators=[MinValueValidator(0)]
    )
    confidence = models.DecimalField(
        max_digits=5, decimal_places=4, default=0, validators=CONFIDENCE_VALIDATORS
    )
    rule_version = models.CharField(max_length=80)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True
    )
    evidence = models.ManyToManyField(Evidence, related_name="findings", blank=True)

    class Meta:
        indexes = [models.Index(fields=("run", "category", "severity", "status"))]


class Recommendation(UUIDTimeStampedModel):
    finding = models.ForeignKey(Finding, on_delete=models.CASCADE, related_name="recommendations")
    title = models.CharField(max_length=255)
    rationale = models.TextField()
    implementation = models.TextField()
    impact = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    effort = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    risk_class = models.CharField(max_length=20, choices=RiskClass.choices, default=RiskClass.LOW)
    review_status = models.CharField(
        max_length=24, choices=ReviewStatus.choices, default=ReviewStatus.DRAFT
    )


class Keyword(UUIDTimeStampedModel, AvailabilityMixin, EvidenceMetadataMixin):
    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="keywords")
    source_snapshot = models.ForeignKey(
        SourceSnapshot, null=True, blank=True, on_delete=models.SET_NULL, related_name="keywords"
    )
    phrase = models.CharField(max_length=500)
    normalized_phrase = models.CharField(max_length=500)
    country_code = models.CharField(max_length=2, blank=True)
    intent = models.CharField(max_length=40, blank=True)
    search_volume = models.PositiveIntegerField(null=True, blank=True)
    difficulty = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, validators=PERCENTAGE_VALIDATORS
    )
    cpc = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    position = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("run", "normalized_phrase", "country_code", "locale"),
                name="domain_keyword_scope_unique",
            ),
            models.CheckConstraint(
                condition=~Q(availability=AvailabilityStatus.UNAVAILABLE)
                | ~Q(unavailable_reason=""),
                name="domain_keyword_unavailable_reason",
            ),
        ]
        indexes = [models.Index(fields=("run", "intent", "search_volume"))]


class KeywordCluster(UUIDTimeStampedModel):
    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="keyword_clusters")
    name = models.CharField(max_length=255)
    intent = models.CharField(max_length=40, blank=True)
    rationale = models.TextField(blank=True)
    pillar_keyword = models.ForeignKey(
        Keyword,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pillar_for_clusters",
    )
    keywords = models.ManyToManyField(Keyword, related_name="clusters", blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("run", "name"), name="domain_cluster_name_unique")
        ]


class URLTarget(UUIDTimeStampedModel):
    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="url_targets")
    cluster = models.ForeignKey(
        KeywordCluster, null=True, blank=True, on_delete=models.SET_NULL, related_name="url_targets"
    )
    original_url = models.URLField(max_length=2048, blank=True)
    normalized_url = models.URLField(max_length=2048)
    target_type = models.CharField(max_length=40)
    proposed_action = models.CharField(max_length=60, blank=True)
    intent = models.CharField(max_length=40, blank=True)
    rationale = models.TextField(blank=True)
    risk_class = models.CharField(max_length=20, choices=RiskClass.choices, default=RiskClass.LOW)
    review_status = models.CharField(
        max_length=24, choices=ReviewStatus.choices, default=ReviewStatus.DRAFT
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("run", "normalized_url"), name="domain_urltarget_unique"
            )
        ]


class Backlink(UUIDTimeStampedModel, AvailabilityMixin, EvidenceMetadataMixin):
    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="backlinks")
    source_snapshot = models.ForeignKey(
        SourceSnapshot, null=True, blank=True, on_delete=models.SET_NULL, related_name="backlinks"
    )
    source_url = models.URLField(max_length=2048)
    target_url = models.URLField(max_length=2048)
    referring_domain = models.CharField(max_length=253, db_index=True)
    anchor_text = models.TextField(blank=True)
    authority_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, validators=PERCENTAGE_VALIDATORS
    )
    toxicity_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, validators=PERCENTAGE_VALIDATORS
    )
    link_type = models.CharField(max_length=40, blank=True)
    first_seen = models.DateField(null=True, blank=True)
    last_seen = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("run", "source_url", "target_url"), name="domain_backlink_unique"
            ),
            models.CheckConstraint(
                condition=~Q(availability=AvailabilityStatus.UNAVAILABLE)
                | ~Q(unavailable_reason=""),
                name="domain_backlink_unavailable_reason",
            ),
        ]
        indexes = [models.Index(fields=("run", "referring_domain"))]


class ContentBrief(UUIDTimeStampedModel):
    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="content_briefs")
    cluster = models.ForeignKey(
        KeywordCluster,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="content_briefs",
    )
    title = models.CharField(max_length=500)
    slug = models.SlugField(max_length=255)
    target_url = models.URLField(max_length=2048)
    primary_keyword = models.CharField(max_length=500)
    search_intent = models.CharField(max_length=40)
    outline = models.JSONField(default=list)
    approved_fact_pack = models.JSONField(default=dict)
    source_evidence = models.ManyToManyField(Evidence, related_name="content_briefs", blank=True)
    review_status = models.CharField(
        max_length=24, choices=ReviewStatus.choices, default=ReviewStatus.DRAFT, db_index=True
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("run", "slug"), name="domain_contentbrief_slug_unique"),
            models.UniqueConstraint(
                fields=("run", "target_url"), name="domain_contentbrief_target_unique"
            ),
        ]


class ContentDraft(UUIDTimeStampedModel):
    brief = models.ForeignKey(ContentBrief, on_delete=models.CASCADE, related_name="drafts")
    version = models.PositiveIntegerField(default=1)
    format = models.CharField(max_length=20, default="markdown")
    body = models.TextField()
    model_id = models.CharField(max_length=120, blank=True)
    prompt_version = models.CharField(max_length=80, blank=True)
    request_sha256 = models.CharField(max_length=64, blank=True)
    response_sha256 = models.CharField(max_length=64, blank=True)
    review_status = models.CharField(
        max_length=24, choices=ReviewStatus.choices, default=ReviewStatus.DRAFT, db_index=True
    )

    class Meta:
        ordering = ("brief", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=("brief", "version"), name="domain_contentdraft_version_unique"
            )
        ]


class ClaimLedger(UUIDTimeStampedModel):
    class ClaimStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SUPPORTED = "supported", "Supported"
        UNSUPPORTED = "unsupported", "Unsupported"
        REMOVED = "removed", "Removed"

    draft = models.ForeignKey(ContentDraft, on_delete=models.CASCADE, related_name="claims")
    claim_text = models.TextField()
    status = models.CharField(
        max_length=20, choices=ClaimStatus.choices, default=ClaimStatus.PENDING, db_index=True
    )
    evidence = models.ManyToManyField(Evidence, related_name="claims", blank=True)
    reviewer_notes = models.TextField(blank=True)


class ActionItem(UUIDTimeStampedModel):
    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="actions")
    recommendation = models.ForeignKey(
        Recommendation, null=True, blank=True, on_delete=models.SET_NULL, related_name="actions"
    )
    title = models.CharField(max_length=500)
    description = models.TextField()
    week = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(16)]
    )
    owner_label = models.CharField(max_length=120, blank=True)
    impact = models.DecimalField(max_digits=5, decimal_places=2, validators=PERCENTAGE_VALIDATORS)
    evidence_confidence = models.DecimalField(
        max_digits=5, decimal_places=2, validators=PERCENTAGE_VALIDATORS
    )
    reach = models.DecimalField(max_digits=5, decimal_places=2, validators=PERCENTAGE_VALIDATORS)
    business_criticality = models.DecimalField(
        max_digits=5, decimal_places=2, validators=PERCENTAGE_VALIDATORS
    )
    dependency_urgency = models.DecimalField(
        max_digits=5, decimal_places=2, validators=PERCENTAGE_VALIDATORS
    )
    effort = models.DecimalField(max_digits=5, decimal_places=2, validators=PERCENTAGE_VALIDATORS)
    priority_score = models.DecimalField(
        max_digits=5, decimal_places=2, validators=PERCENTAGE_VALIDATORS
    )
    priority_tier = models.CharField(
        max_length=2, choices=(("P1", "P1"), ("P2", "P2"), ("P3", "P3"), ("P4", "P4"))
    )
    risk_class = models.CharField(max_length=20, choices=RiskClass.choices, default=RiskClass.LOW)
    review_status = models.CharField(
        max_length=24, choices=ReviewStatus.choices, default=ReviewStatus.DRAFT
    )
    dependencies = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="dependants"
    )

    class Meta:
        ordering = ("week", "-priority_score", "title")
        indexes = [models.Index(fields=("run", "priority_tier", "week"))]


class Artifact(UUIDTimeStampedModel):
    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="artifacts")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="artifacts"
    )
    artifact_type = models.CharField(max_length=80, db_index=True)
    title = models.CharField(max_length=500)
    format = models.CharField(max_length=20)
    storage_key = models.CharField(max_length=1024)
    sha256 = models.CharField(max_length=64, db_index=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    media_type = models.CharField(max_length=120)
    risk_class = models.CharField(max_length=20, choices=RiskClass.choices, default=RiskClass.LOW)
    approval_required = models.BooleanField(default=False, db_index=True)
    review_status = models.CharField(
        max_length=24, choices=ReviewStatus.choices, default=ReviewStatus.DRAFT, db_index=True
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("run", "storage_key"), name="domain_artifact_storage_unique"
            )
        ]
        indexes = [models.Index(fields=("run", "review_status", "approval_required"))]


class Approval(UUIDTimeStampedModel):
    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="approvals")
    artifact = models.ForeignKey(
        Artifact, null=True, blank=True, on_delete=models.CASCADE, related_name="approvals"
    )
    gate = models.CharField(max_length=20, choices=ApprovalGate.choices, db_index=True)
    target_type = models.CharField(max_length=80, blank=True)
    target_id = models.UUIDField(null=True, blank=True)
    decision = models.CharField(
        max_length=24,
        choices=ApprovalDecision.choices,
        default=ApprovalDecision.PENDING,
        db_index=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="approval_requests",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approval_reviews",
    )
    requested_at = models.DateTimeField(default=timezone.now)
    decided_at = models.DateTimeField(null=True, blank=True)
    comment = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=("run", "gate", "decision"))]
        constraints = [
            models.UniqueConstraint(
                fields=("run", "gate", "target_type", "target_id"),
                condition=Q(decision=ApprovalDecision.PENDING),
                name="domain_approval_pending_target_unique",
            )
        ]


class QAResult(UUIDTimeStampedModel):
    class Status(models.TextChoices):
        PASS = "pass", "Pass"
        FAIL = "fail", "Fail"
        WARN = "warn", "Warn"
        SKIP = "skip", "Skip"

    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="qa_results")
    artifact = models.ForeignKey(
        Artifact, null=True, blank=True, on_delete=models.CASCADE, related_name="qa_results"
    )
    check_code = models.CharField(max_length=120)
    check_version = models.CharField(max_length=80)
    severity = models.CharField(max_length=20, choices=Severity.choices)
    status = models.CharField(max_length=10, choices=Status.choices, db_index=True)
    message = models.TextField()
    details = models.JSONField(default=dict, blank=True)
    evidence = models.ManyToManyField(Evidence, related_name="qa_results", blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("run", "artifact", "check_code", "check_version"),
                name="domain_qa_check_unique",
            )
        ]
        indexes = [models.Index(fields=("run", "status", "severity"))]


class PackageManifest(UUIDTimeStampedModel):
    run = models.ForeignKey(AuditRun, on_delete=models.CASCADE, related_name="package_manifests")
    package_artifact = models.OneToOneField(
        Artifact, null=True, blank=True, on_delete=models.SET_NULL, related_name="package_manifest"
    )
    version = models.PositiveIntegerField(default=1)
    manifest = models.JSONField(default=dict)
    manifest_sha256 = models.CharField(max_length=64)
    package_sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        max_length=24, choices=ReviewStatus.choices, default=ReviewStatus.DRAFT
    )
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name="package_manifests",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("run", "version"), name="domain_manifest_version_unique"
            )
        ]
        ordering = ("run", "-version")


class AuditEvent(models.Model):
    """Append-only audit trail. Application code cannot update or delete rows."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    client = models.ForeignKey(
        Client, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events"
    )
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events"
    )
    run = models.ForeignKey(
        AuditRun, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events"
    )
    event_type = models.CharField(max_length=120, db_index=True)
    object_type = models.CharField(max_length=120, blank=True)
    object_id = models.CharField(max_length=80, blank=True)
    request_id = models.CharField(max_length=100, blank=True, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("project", "event_type", "-created_at"))]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Audit events are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit events cannot be deleted.")
