# ruff: noqa
import app.domain.managers
import django.contrib.auth.validators
import django.core.validators
import django.db.models.deletion
import django.utils.timezone
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="Client",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("slug", models.SlugField(max_length=120, unique=True)),
                ("brand_name", models.CharField(blank=True, max_length=255)),
                ("logo_storage_key", models.CharField(blank=True, max_length=1024)),
                ("primary_colour", models.CharField(default="#17324D", max_length=16)),
                ("accent_colour", models.CharField(default="#D97A32", max_length=16)),
                ("retention_days", models.PositiveIntegerField(default=365)),
                ("archived_at", models.DateTimeField(blank=True, db_index=True, null=True)),
            ],
            options={
                "ordering": ("name",),
            },
        ),
        migrations.CreateModel(
            name="User",
            fields=[
                ("password", models.CharField(max_length=128, verbose_name="password")),
                (
                    "last_login",
                    models.DateTimeField(blank=True, null=True, verbose_name="last login"),
                ),
                (
                    "is_superuser",
                    models.BooleanField(
                        default=False,
                        help_text="Designates that this user has all permissions without explicitly assigning them.",
                        verbose_name="superuser status",
                    ),
                ),
                (
                    "username",
                    models.CharField(
                        error_messages={"unique": "A user with that username already exists."},
                        help_text="Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.",
                        max_length=150,
                        unique=True,
                        validators=[django.contrib.auth.validators.UnicodeUsernameValidator()],
                        verbose_name="username",
                    ),
                ),
                (
                    "first_name",
                    models.CharField(blank=True, max_length=150, verbose_name="first name"),
                ),
                (
                    "last_name",
                    models.CharField(blank=True, max_length=150, verbose_name="last name"),
                ),
                (
                    "email",
                    models.EmailField(blank=True, max_length=254, verbose_name="email address"),
                ),
                (
                    "is_staff",
                    models.BooleanField(
                        default=False,
                        help_text="Designates whether the user can log into this admin site.",
                        verbose_name="staff status",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(
                        default=True,
                        help_text="Designates whether this user should be treated as active. Unselect this instead of deleting accounts.",
                        verbose_name="active",
                    ),
                ),
                (
                    "date_joined",
                    models.DateTimeField(
                        default=django.utils.timezone.now, verbose_name="date joined"
                    ),
                ),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("agency_admin", "Agency administrator"),
                            ("analyst", "Analyst"),
                            ("client_reviewer", "Client reviewer"),
                        ],
                        db_index=True,
                        default="client_reviewer",
                        max_length=24,
                    ),
                ),
                ("must_change_password", models.BooleanField(default=True)),
                ("temporary_password_expires_at", models.DateTimeField(blank=True, null=True)),
                ("password_changed_at", models.DateTimeField(blank=True, null=True)),
                ("last_activity_at", models.DateTimeField(blank=True, null=True)),
                (
                    "groups",
                    models.ManyToManyField(
                        blank=True,
                        help_text="The groups this user belongs to. A user will get all permissions granted to each of their groups.",
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.group",
                        verbose_name="groups",
                    ),
                ),
                (
                    "user_permissions",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Specific permissions for this user.",
                        related_name="user_set",
                        related_query_name="user",
                        to="auth.permission",
                        verbose_name="user permissions",
                    ),
                ),
            ],
            options={
                "ordering": ("username",),
            },
            managers=[
                ("objects", app.domain.managers.UserManager()),
            ],
        ),
        migrations.CreateModel(
            name="AuditRun",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "profile",
                    models.CharField(
                        choices=[
                            ("quick", "Quick"),
                            ("standard", "Standard"),
                            ("enterprise", "Enterprise"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("collecting", "Collecting"),
                            ("auditing", "Auditing"),
                            ("gate_1_review", "Gate 1 review"),
                            ("planning", "Planning"),
                            ("generating", "Generating"),
                            ("gate_2_review", "Gate 2 review"),
                            ("final_qa", "Final QA"),
                            ("packaged", "Packaged"),
                            ("approved", "Approved"),
                            ("revision_requested", "Revision requested"),
                            ("failed", "Failed"),
                            ("cancelled", "Cancelled"),
                        ],
                        db_index=True,
                        default="draft",
                        max_length=30,
                    ),
                ),
                ("idempotency_key", models.CharField(max_length=128)),
                ("version", models.PositiveIntegerField(default=1)),
                ("rule_version", models.CharField(max_length=80)),
                ("source_cutoff_at", models.DateTimeField(blank=True, null=True)),
                (
                    "evidence_coverage",
                    models.DecimalField(
                        decimal_places=2,
                        default=0,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(100),
                        ],
                    ),
                ),
                (
                    "confidence",
                    models.DecimalField(
                        decimal_places=4,
                        default=0,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(1),
                        ],
                    ),
                ),
                (
                    "health_score",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=5,
                        null=True,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(100),
                        ],
                    ),
                ),
                ("error_code", models.CharField(blank=True, max_length=80)),
                ("error_summary", models.TextField(blank=True)),
                ("cancelled_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="Artifact",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("artifact_type", models.CharField(db_index=True, max_length=80)),
                ("title", models.CharField(max_length=500)),
                ("format", models.CharField(max_length=20)),
                ("storage_key", models.CharField(max_length=1024)),
                ("sha256", models.CharField(db_index=True, max_length=64)),
                ("size_bytes", models.PositiveBigIntegerField(default=0)),
                ("media_type", models.CharField(max_length=120)),
                (
                    "risk_class",
                    models.CharField(
                        choices=[
                            ("low", "Low"),
                            ("medium", "Medium"),
                            ("high", "High"),
                            ("dangerous", "Dangerous"),
                        ],
                        default="low",
                        max_length=20,
                    ),
                ),
                ("approval_required", models.BooleanField(db_index=True, default=False)),
                (
                    "review_status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("in_review", "In review"),
                            ("approved", "Approved"),
                            ("revision_requested", "Revision requested"),
                            ("rejected", "Rejected"),
                        ],
                        db_index=True,
                        default="draft",
                        max_length=24,
                    ),
                ),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="artifacts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="artifacts",
                        to="domain.auditrun",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Approval",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "gate",
                    models.CharField(
                        choices=[
                            ("gate_1", "Gate 1"),
                            ("gate_2", "Gate 2"),
                            ("high_risk", "High-risk asset"),
                            ("package", "Final package"),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                ("target_type", models.CharField(blank=True, max_length=80)),
                ("target_id", models.UUIDField(blank=True, null=True)),
                (
                    "decision",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("approved", "Approved"),
                            ("revision_requested", "Revision requested"),
                            ("rejected", "Rejected"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=24,
                    ),
                ),
                ("requested_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("comment", models.TextField(blank=True)),
                (
                    "requested_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approval_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="approval_reviews",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "artifact",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="approvals",
                        to="domain.artifact",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="approvals",
                        to="domain.auditrun",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ContentBrief",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=500)),
                ("slug", models.SlugField(max_length=255)),
                ("target_url", models.URLField(max_length=2048)),
                ("primary_keyword", models.CharField(max_length=500)),
                ("search_intent", models.CharField(max_length=40)),
                ("outline", models.JSONField(default=list)),
                ("approved_fact_pack", models.JSONField(default=dict)),
                (
                    "review_status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("in_review", "In review"),
                            ("approved", "Approved"),
                            ("revision_requested", "Revision requested"),
                            ("rejected", "Rejected"),
                        ],
                        db_index=True,
                        default="draft",
                        max_length=24,
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="content_briefs",
                        to="domain.auditrun",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ContentDraft",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("format", models.CharField(default="markdown", max_length=20)),
                ("body", models.TextField()),
                ("model_id", models.CharField(blank=True, max_length=120)),
                ("prompt_version", models.CharField(blank=True, max_length=80)),
                ("request_sha256", models.CharField(blank=True, max_length=64)),
                ("response_sha256", models.CharField(blank=True, max_length=64)),
                (
                    "review_status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("in_review", "In review"),
                            ("approved", "Approved"),
                            ("revision_requested", "Revision requested"),
                            ("rejected", "Rejected"),
                        ],
                        db_index=True,
                        default="draft",
                        max_length=24,
                    ),
                ),
                (
                    "brief",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="drafts",
                        to="domain.contentbrief",
                    ),
                ),
            ],
            options={
                "ordering": ("brief", "-version"),
            },
        ),
        migrations.CreateModel(
            name="Evidence",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "availability",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("available", "Available"),
                            ("unavailable", "Unavailable"),
                            ("error", "Error"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("unavailable_reason", models.TextField(blank=True)),
                (
                    "captured_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                ("locale", models.CharField(blank=True, max_length=35)),
                ("device", models.CharField(blank=True, max_length=20)),
                ("scope", models.CharField(blank=True, max_length=255)),
                ("rule_version", models.CharField(blank=True, max_length=80)),
                (
                    "confidence",
                    models.DecimalField(
                        decimal_places=4,
                        default=0,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(1),
                        ],
                    ),
                ),
                ("evidence_type", models.CharField(db_index=True, max_length=80)),
                ("title", models.CharField(max_length=255)),
                ("excerpt", models.TextField(blank=True)),
                ("locator", models.CharField(blank=True, max_length=1024)),
                ("storage_key", models.CharField(blank=True, max_length=1024)),
                ("sha256", models.CharField(blank=True, max_length=64)),
                ("details", models.JSONField(blank=True, default=dict)),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="evidence",
                        to="domain.auditrun",
                    ),
                ),
            ],
            options={
                "ordering": ("-captured_at",),
            },
        ),
        migrations.AddField(
            model_name="contentbrief",
            name="source_evidence",
            field=models.ManyToManyField(
                blank=True, related_name="content_briefs", to="domain.evidence"
            ),
        ),
        migrations.CreateModel(
            name="ClaimLedger",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("claim_text", models.TextField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("supported", "Supported"),
                            ("unsupported", "Unsupported"),
                            ("removed", "Removed"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("reviewer_notes", models.TextField(blank=True)),
                (
                    "draft",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="claims",
                        to="domain.contentdraft",
                    ),
                ),
                (
                    "evidence",
                    models.ManyToManyField(blank=True, related_name="claims", to="domain.evidence"),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="Keyword",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "availability",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("available", "Available"),
                            ("unavailable", "Unavailable"),
                            ("error", "Error"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("unavailable_reason", models.TextField(blank=True)),
                (
                    "captured_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                ("locale", models.CharField(blank=True, max_length=35)),
                ("device", models.CharField(blank=True, max_length=20)),
                ("scope", models.CharField(blank=True, max_length=255)),
                ("rule_version", models.CharField(blank=True, max_length=80)),
                (
                    "confidence",
                    models.DecimalField(
                        decimal_places=4,
                        default=0,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(1),
                        ],
                    ),
                ),
                ("phrase", models.CharField(max_length=500)),
                ("normalized_phrase", models.CharField(max_length=500)),
                ("country_code", models.CharField(blank=True, max_length=2)),
                ("intent", models.CharField(blank=True, max_length=40)),
                ("search_volume", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "difficulty",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=5,
                        null=True,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(100),
                        ],
                    ),
                ),
                (
                    "cpc",
                    models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True),
                ),
                (
                    "position",
                    models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="keywords",
                        to="domain.auditrun",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="KeywordCluster",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("intent", models.CharField(blank=True, max_length=40)),
                ("rationale", models.TextField(blank=True)),
                (
                    "keywords",
                    models.ManyToManyField(
                        blank=True, related_name="clusters", to="domain.keyword"
                    ),
                ),
                (
                    "pillar_keyword",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="pillar_for_clusters",
                        to="domain.keyword",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="keyword_clusters",
                        to="domain.auditrun",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="contentbrief",
            name="cluster",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="content_briefs",
                to="domain.keywordcluster",
            ),
        ),
        migrations.CreateModel(
            name="PackageManifest",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version", models.PositiveIntegerField(default=1)),
                ("manifest", models.JSONField(default=dict)),
                ("manifest_sha256", models.CharField(max_length=64)),
                ("package_sha256", models.CharField(blank=True, max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("in_review", "In review"),
                            ("approved", "Approved"),
                            ("revision_requested", "Revision requested"),
                            ("rejected", "Rejected"),
                        ],
                        default="draft",
                        max_length=24,
                    ),
                ),
                (
                    "generated_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="package_manifests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "package_artifact",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="package_manifest",
                        to="domain.artifact",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="package_manifests",
                        to="domain.auditrun",
                    ),
                ),
            ],
            options={
                "ordering": ("run", "-version"),
            },
        ),
        migrations.CreateModel(
            name="PageSnapshot",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "captured_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                ("locale", models.CharField(blank=True, max_length=35)),
                ("device", models.CharField(blank=True, max_length=20)),
                ("scope", models.CharField(blank=True, max_length=255)),
                ("rule_version", models.CharField(blank=True, max_length=80)),
                (
                    "confidence",
                    models.DecimalField(
                        decimal_places=4,
                        default=0,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(1),
                        ],
                    ),
                ),
                ("original_url", models.URLField(max_length=2048)),
                ("normalized_url", models.URLField(max_length=2048)),
                ("domain", models.CharField(db_index=True, max_length=253)),
                ("approved_domain", models.BooleanField(db_index=True, default=False)),
                ("status_code", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("content_type", models.CharField(blank=True, max_length=120)),
                ("canonical_url", models.URLField(blank=True, max_length=2048)),
                ("redirect_target_url", models.URLField(blank=True, max_length=2048)),
                ("robots_indexable", models.BooleanField(blank=True, null=True)),
                ("title", models.TextField(blank=True)),
                ("meta_description", models.TextField(blank=True)),
                ("h1", models.TextField(blank=True)),
                ("content_sha256", models.CharField(blank=True, db_index=True, max_length=64)),
                ("response_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("facts", models.JSONField(blank=True, default=dict)),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pages",
                        to="domain.auditrun",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Finding",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("category", models.CharField(db_index=True, max_length=60)),
                ("code", models.CharField(max_length=120)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField()),
                (
                    "severity",
                    models.CharField(
                        choices=[
                            ("info", "Info"),
                            ("low", "Low"),
                            ("medium", "Medium"),
                            ("high", "High"),
                            ("critical", "Critical"),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                ("affected_count", models.PositiveIntegerField(default=0)),
                (
                    "affected_share",
                    models.DecimalField(
                        decimal_places=4,
                        default=0,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(1),
                        ],
                    ),
                ),
                (
                    "score_penalty",
                    models.DecimalField(
                        decimal_places=3,
                        default=0,
                        max_digits=7,
                        validators=[django.core.validators.MinValueValidator(0)],
                    ),
                ),
                (
                    "confidence",
                    models.DecimalField(
                        decimal_places=4,
                        default=0,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(1),
                        ],
                    ),
                ),
                ("rule_version", models.CharField(max_length=80)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "Open"),
                            ("accepted", "Accepted"),
                            ("resolved", "Resolved"),
                            ("dismissed", "Dismissed"),
                        ],
                        db_index=True,
                        default="open",
                        max_length=20,
                    ),
                ),
                (
                    "evidence",
                    models.ManyToManyField(
                        blank=True, related_name="findings", to="domain.evidence"
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="findings",
                        to="domain.auditrun",
                    ),
                ),
                (
                    "page",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="findings",
                        to="domain.pagesnapshot",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="evidence",
            name="page",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="evidence",
                to="domain.pagesnapshot",
            ),
        ),
        migrations.CreateModel(
            name="Project",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=255)),
                ("slug", models.SlugField(max_length=120)),
                ("primary_domain", models.CharField(max_length=253)),
                ("approved_domains", models.JSONField(default=list)),
                ("locale", models.CharField(default="en-AU", max_length=35)),
                ("country_code", models.CharField(default="AU", max_length=2)),
                (
                    "business_type",
                    models.CharField(
                        choices=[
                            ("service", "Service"),
                            ("saas", "SaaS"),
                            ("local", "Local"),
                            ("ecommerce", "Ecommerce"),
                            ("hybrid", "Hybrid"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "default_profile",
                    models.CharField(
                        choices=[
                            ("quick", "Quick"),
                            ("standard", "Standard"),
                            ("enterprise", "Enterprise"),
                        ],
                        default="standard",
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("paused", "Paused"),
                            ("archived", "Archived"),
                        ],
                        db_index=True,
                        default="active",
                        max_length=20,
                    ),
                ),
                ("conversion_goals", models.JSONField(blank=True, default=list)),
                ("brand_facts", models.JSONField(blank=True, default=dict)),
                ("prohibited_claims", models.JSONField(blank=True, default=list)),
                ("cms_platform", models.CharField(blank=True, max_length=80)),
                (
                    "client",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="projects",
                        to="domain.client",
                    ),
                ),
            ],
            options={
                "ordering": ("client__name", "name"),
            },
        ),
        migrations.CreateModel(
            name="Membership",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "access_role",
                    models.CharField(
                        choices=[
                            ("agency_admin", "Agency administrator"),
                            ("analyst", "Analyst"),
                            ("client_reviewer", "Client reviewer"),
                        ],
                        max_length=24,
                    ),
                ),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                (
                    "client",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to="domain.client",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to="domain.project",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Connection",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "availability",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("available", "Available"),
                            ("unavailable", "Unavailable"),
                            ("error", "Error"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("unavailable_reason", models.TextField(blank=True)),
                (
                    "provider",
                    models.CharField(
                        choices=[
                            ("gsc", "Google Search Console"),
                            ("ga4", "Google Analytics 4"),
                            ("semrush", "SEMrush"),
                            ("pagespeed", "PageSpeed Insights"),
                            ("s3", "S3-compatible storage"),
                        ],
                        max_length=40,
                    ),
                ),
                ("label", models.CharField(blank=True, max_length=120)),
                ("encrypted_credentials", models.TextField(blank=True)),
                ("encryption_key_id", models.CharField(blank=True, max_length=80)),
                ("external_account_id", models.CharField(blank=True, max_length=255)),
                ("scopes", models.JSONField(blank=True, default=list)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="connections",
                        to="domain.project",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="auditrun",
            name="project",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="audit_runs",
                to="domain.project",
            ),
        ),
        migrations.CreateModel(
            name="AuditEvent",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("event_type", models.CharField(db_index=True, max_length=120)),
                ("object_type", models.CharField(blank=True, max_length=120)),
                ("object_id", models.CharField(blank=True, max_length=80)),
                ("request_id", models.CharField(blank=True, db_index=True, max_length=100)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("payload", models.JSONField(blank=True, default=dict)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="domain.auditrun",
                    ),
                ),
                (
                    "client",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="domain.client",
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_events",
                        to="domain.project",
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="QAResult",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("check_code", models.CharField(max_length=120)),
                ("check_version", models.CharField(max_length=80)),
                (
                    "severity",
                    models.CharField(
                        choices=[
                            ("info", "Info"),
                            ("low", "Low"),
                            ("medium", "Medium"),
                            ("high", "High"),
                            ("critical", "Critical"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pass", "Pass"),
                            ("fail", "Fail"),
                            ("warn", "Warn"),
                            ("skip", "Skip"),
                        ],
                        db_index=True,
                        max_length=10,
                    ),
                ),
                ("message", models.TextField()),
                ("details", models.JSONField(blank=True, default=dict)),
                (
                    "artifact",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="qa_results",
                        to="domain.artifact",
                    ),
                ),
                (
                    "evidence",
                    models.ManyToManyField(
                        blank=True, related_name="qa_results", to="domain.evidence"
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="qa_results",
                        to="domain.auditrun",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Recommendation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=255)),
                ("rationale", models.TextField()),
                ("implementation", models.TextField()),
                (
                    "impact",
                    models.PositiveSmallIntegerField(
                        default=1,
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(5),
                        ],
                    ),
                ),
                (
                    "effort",
                    models.PositiveSmallIntegerField(
                        default=1,
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(5),
                        ],
                    ),
                ),
                (
                    "risk_class",
                    models.CharField(
                        choices=[
                            ("low", "Low"),
                            ("medium", "Medium"),
                            ("high", "High"),
                            ("dangerous", "Dangerous"),
                        ],
                        default="low",
                        max_length=20,
                    ),
                ),
                (
                    "review_status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("in_review", "In review"),
                            ("approved", "Approved"),
                            ("revision_requested", "Revision requested"),
                            ("rejected", "Rejected"),
                        ],
                        default="draft",
                        max_length=24,
                    ),
                ),
                (
                    "finding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recommendations",
                        to="domain.finding",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="ActionItem",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("title", models.CharField(max_length=500)),
                ("description", models.TextField()),
                (
                    "week",
                    models.PositiveSmallIntegerField(
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(16),
                        ]
                    ),
                ),
                ("owner_label", models.CharField(blank=True, max_length=120)),
                (
                    "impact",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(100),
                        ],
                    ),
                ),
                (
                    "evidence_confidence",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(100),
                        ],
                    ),
                ),
                (
                    "reach",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(100),
                        ],
                    ),
                ),
                (
                    "business_criticality",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(100),
                        ],
                    ),
                ),
                (
                    "dependency_urgency",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(100),
                        ],
                    ),
                ),
                (
                    "effort",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(100),
                        ],
                    ),
                ),
                (
                    "priority_score",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(100),
                        ],
                    ),
                ),
                (
                    "priority_tier",
                    models.CharField(
                        choices=[("P1", "P1"), ("P2", "P2"), ("P3", "P3"), ("P4", "P4")],
                        max_length=2,
                    ),
                ),
                (
                    "risk_class",
                    models.CharField(
                        choices=[
                            ("low", "Low"),
                            ("medium", "Medium"),
                            ("high", "High"),
                            ("dangerous", "Dangerous"),
                        ],
                        default="low",
                        max_length=20,
                    ),
                ),
                (
                    "review_status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("in_review", "In review"),
                            ("approved", "Approved"),
                            ("revision_requested", "Revision requested"),
                            ("rejected", "Rejected"),
                        ],
                        default="draft",
                        max_length=24,
                    ),
                ),
                (
                    "dependencies",
                    models.ManyToManyField(
                        blank=True, related_name="dependants", to="domain.actionitem"
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="actions",
                        to="domain.auditrun",
                    ),
                ),
                (
                    "recommendation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="actions",
                        to="domain.recommendation",
                    ),
                ),
            ],
            options={
                "ordering": ("week", "-priority_score", "title"),
            },
        ),
        migrations.CreateModel(
            name="RunStage",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=80)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("running", "Running"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                            ("skipped", "Skipped"),
                            ("cancelled", "Cancelled"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("sequence", models.PositiveSmallIntegerField(default=0)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("checkpoint", models.JSONField(blank=True, default=dict)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("heartbeat_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("error_code", models.CharField(blank=True, max_length=80)),
                ("error_summary", models.TextField(blank=True)),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="stages",
                        to="domain.auditrun",
                    ),
                ),
            ],
            options={
                "ordering": ("sequence", "created_at"),
            },
        ),
        migrations.CreateModel(
            name="SourceImport",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "availability",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("available", "Available"),
                            ("unavailable", "Unavailable"),
                            ("error", "Error"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("unavailable_reason", models.TextField(blank=True)),
                ("source_type", models.CharField(max_length=60)),
                ("original_filename", models.CharField(max_length=255)),
                ("media_type", models.CharField(max_length=100)),
                ("size_bytes", models.PositiveBigIntegerField(default=0)),
                ("sha256", models.CharField(db_index=True, max_length=64)),
                ("storage_key", models.CharField(max_length=1024)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("quarantined", "Quarantined"),
                            ("validating", "Validating"),
                            ("accepted", "Accepted"),
                            ("rejected", "Rejected"),
                        ],
                        db_index=True,
                        default="quarantined",
                        max_length=20,
                    ),
                ),
                ("schema_version", models.CharField(blank=True, max_length=80)),
                ("column_mapping", models.JSONField(blank=True, default=dict)),
                ("validation_issues", models.JSONField(blank=True, default=list)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="source_imports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="source_imports",
                        to="domain.project",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="SourceSnapshot",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "availability",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("available", "Available"),
                            ("unavailable", "Unavailable"),
                            ("error", "Error"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("unavailable_reason", models.TextField(blank=True)),
                (
                    "captured_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                ("locale", models.CharField(blank=True, max_length=35)),
                ("device", models.CharField(blank=True, max_length=20)),
                ("scope", models.CharField(blank=True, max_length=255)),
                ("rule_version", models.CharField(blank=True, max_length=80)),
                (
                    "confidence",
                    models.DecimalField(
                        decimal_places=4,
                        default=0,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(1),
                        ],
                    ),
                ),
                ("source_type", models.CharField(max_length=60)),
                ("storage_key", models.CharField(blank=True, max_length=1024)),
                ("sha256", models.CharField(blank=True, max_length=64)),
                ("record_count", models.PositiveIntegerField(default=0)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "connection",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="snapshots",
                        to="domain.connection",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="source_snapshots",
                        to="domain.auditrun",
                    ),
                ),
                (
                    "source_import",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="snapshots",
                        to="domain.sourceimport",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="pagesnapshot",
            name="source_snapshot",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pages",
                to="domain.sourcesnapshot",
            ),
        ),
        migrations.CreateModel(
            name="MetricObservation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "availability",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("available", "Available"),
                            ("unavailable", "Unavailable"),
                            ("error", "Error"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("unavailable_reason", models.TextField(blank=True)),
                (
                    "captured_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                ("locale", models.CharField(blank=True, max_length=35)),
                ("device", models.CharField(blank=True, max_length=20)),
                ("scope", models.CharField(blank=True, max_length=255)),
                ("rule_version", models.CharField(blank=True, max_length=80)),
                (
                    "confidence",
                    models.DecimalField(
                        decimal_places=4,
                        default=0,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(1),
                        ],
                    ),
                ),
                ("metric_key", models.CharField(max_length=120)),
                (
                    "numeric_value",
                    models.DecimalField(blank=True, decimal_places=6, max_digits=20, null=True),
                ),
                ("text_value", models.TextField(blank=True)),
                ("json_value", models.JSONField(blank=True, null=True)),
                ("unit", models.CharField(blank=True, max_length=40)),
                ("period_start", models.DateField(blank=True, null=True)),
                ("period_end", models.DateField(blank=True, null=True)),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="metrics",
                        to="domain.auditrun",
                    ),
                ),
                (
                    "page",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="metrics",
                        to="domain.pagesnapshot",
                    ),
                ),
                (
                    "source_snapshot",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="metrics",
                        to="domain.sourcesnapshot",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="keyword",
            name="source_snapshot",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="keywords",
                to="domain.sourcesnapshot",
            ),
        ),
        migrations.AddField(
            model_name="evidence",
            name="source_snapshot",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="evidence",
                to="domain.sourcesnapshot",
            ),
        ),
        migrations.CreateModel(
            name="Backlink",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "availability",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("available", "Available"),
                            ("unavailable", "Unavailable"),
                            ("error", "Error"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("unavailable_reason", models.TextField(blank=True)),
                (
                    "captured_at",
                    models.DateTimeField(db_index=True, default=django.utils.timezone.now),
                ),
                ("locale", models.CharField(blank=True, max_length=35)),
                ("device", models.CharField(blank=True, max_length=20)),
                ("scope", models.CharField(blank=True, max_length=255)),
                ("rule_version", models.CharField(blank=True, max_length=80)),
                (
                    "confidence",
                    models.DecimalField(
                        decimal_places=4,
                        default=0,
                        max_digits=5,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(1),
                        ],
                    ),
                ),
                ("source_url", models.URLField(max_length=2048)),
                ("target_url", models.URLField(max_length=2048)),
                ("referring_domain", models.CharField(db_index=True, max_length=253)),
                ("anchor_text", models.TextField(blank=True)),
                (
                    "authority_score",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=5,
                        null=True,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(100),
                        ],
                    ),
                ),
                (
                    "toxicity_score",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=5,
                        null=True,
                        validators=[
                            django.core.validators.MinValueValidator(0),
                            django.core.validators.MaxValueValidator(100),
                        ],
                    ),
                ),
                ("link_type", models.CharField(blank=True, max_length=40)),
                ("first_seen", models.DateField(blank=True, null=True)),
                ("last_seen", models.DateField(blank=True, null=True)),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="backlinks",
                        to="domain.auditrun",
                    ),
                ),
                (
                    "source_snapshot",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="backlinks",
                        to="domain.sourcesnapshot",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="URLTarget",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("original_url", models.URLField(blank=True, max_length=2048)),
                ("normalized_url", models.URLField(max_length=2048)),
                ("target_type", models.CharField(max_length=40)),
                ("proposed_action", models.CharField(blank=True, max_length=60)),
                ("intent", models.CharField(blank=True, max_length=40)),
                ("rationale", models.TextField(blank=True)),
                (
                    "risk_class",
                    models.CharField(
                        choices=[
                            ("low", "Low"),
                            ("medium", "Medium"),
                            ("high", "High"),
                            ("dangerous", "Dangerous"),
                        ],
                        default="low",
                        max_length=20,
                    ),
                ),
                (
                    "review_status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("in_review", "In review"),
                            ("approved", "Approved"),
                            ("revision_requested", "Revision requested"),
                            ("rejected", "Rejected"),
                        ],
                        default="draft",
                        max_length=24,
                    ),
                ),
                (
                    "cluster",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="url_targets",
                        to="domain.keywordcluster",
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="url_targets",
                        to="domain.auditrun",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="artifact",
            index=models.Index(
                fields=["run", "review_status", "approval_required"],
                name="domain_arti_run_id_776e3f_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="artifact",
            constraint=models.UniqueConstraint(
                fields=("run", "storage_key"), name="domain_artifact_storage_unique"
            ),
        ),
        migrations.AddIndex(
            model_name="approval",
            index=models.Index(
                fields=["run", "gate", "decision"], name="domain_appr_run_id_a2d978_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="approval",
            constraint=models.UniqueConstraint(
                condition=models.Q(("decision", "pending")),
                fields=("run", "gate", "target_type", "target_id"),
                name="domain_approval_pending_target_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="contentdraft",
            constraint=models.UniqueConstraint(
                fields=("brief", "version"), name="domain_contentdraft_version_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="keywordcluster",
            constraint=models.UniqueConstraint(
                fields=("run", "name"), name="domain_cluster_name_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="contentbrief",
            constraint=models.UniqueConstraint(
                fields=("run", "slug"), name="domain_contentbrief_slug_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="contentbrief",
            constraint=models.UniqueConstraint(
                fields=("run", "target_url"), name="domain_contentbrief_target_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="packagemanifest",
            constraint=models.UniqueConstraint(
                fields=("run", "version"), name="domain_manifest_version_unique"
            ),
        ),
        migrations.AddIndex(
            model_name="finding",
            index=models.Index(
                fields=["run", "category", "severity", "status"],
                name="domain_find_run_id_b5fec0_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="project",
            index=models.Index(fields=["client", "status"], name="domain_proj_client__55a80b_idx"),
        ),
        migrations.AddConstraint(
            model_name="project",
            constraint=models.UniqueConstraint(
                fields=("client", "slug"), name="domain_project_client_slug_unique"
            ),
        ),
        migrations.AddIndex(
            model_name="membership",
            index=models.Index(fields=["user", "is_active"], name="domain_memb_user_id_87baae_idx"),
        ),
        migrations.AddIndex(
            model_name="membership",
            index=models.Index(fields=["client", "project"], name="domain_memb_client__12ee7a_idx"),
        ),
        migrations.AddConstraint(
            model_name="membership",
            constraint=models.UniqueConstraint(
                condition=models.Q(("project__isnull", True)),
                fields=("user", "client"),
                name="domain_membership_client_scope_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="membership",
            constraint=models.UniqueConstraint(
                condition=models.Q(("project__isnull", False)),
                fields=("user", "project"),
                name="domain_membership_project_scope_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="connection",
            index=models.Index(
                fields=["project", "provider", "availability"],
                name="domain_conn_project_d1108e_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="connection",
            constraint=models.UniqueConstraint(
                fields=("project", "provider", "label"), name="domain_connection_label_unique"
            ),
        ),
        migrations.AddIndex(
            model_name="auditrun",
            index=models.Index(
                fields=["project", "state", "-created_at"], name="domain_audi_project_433b59_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="auditrun",
            constraint=models.UniqueConstraint(
                fields=("project", "idempotency_key"), name="domain_run_idempotency_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="auditrun",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("health_score__isnull", True), ("evidence_coverage__gte", 70), _connector="OR"
                ),
                name="domain_run_score_requires_coverage",
            ),
        ),
        migrations.AddIndex(
            model_name="auditevent",
            index=models.Index(
                fields=["project", "event_type", "-created_at"],
                name="domain_audi_project_126171_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="qaresult",
            index=models.Index(
                fields=["run", "status", "severity"], name="domain_qare_run_id_bd53dd_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="qaresult",
            constraint=models.UniqueConstraint(
                fields=("run", "artifact", "check_code", "check_version"),
                name="domain_qa_check_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="actionitem",
            index=models.Index(
                fields=["run", "priority_tier", "week"], name="domain_acti_run_id_b21f76_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="runstage",
            constraint=models.UniqueConstraint(
                fields=("run", "name"), name="domain_stage_name_unique"
            ),
        ),
        migrations.AddIndex(
            model_name="sourceimport",
            index=models.Index(
                fields=["project", "source_type", "status"], name="domain_sour_project_6a9b17_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="sourcesnapshot",
            index=models.Index(
                fields=["run", "source_type", "availability"], name="domain_sour_run_id_192db6_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="pagesnapshot",
            index=models.Index(
                fields=["run", "approved_domain", "status_code"],
                name="domain_page_run_id_d6e300_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="pagesnapshot",
            constraint=models.UniqueConstraint(
                fields=("run", "normalized_url"), name="domain_page_normalized_unique"
            ),
        ),
        migrations.AddIndex(
            model_name="metricobservation",
            index=models.Index(
                fields=["run", "metric_key", "captured_at"], name="domain_metr_run_id_dc0648_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="metricobservation",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("availability", "unavailable"),
                    ("numeric_value__isnull", False),
                    models.Q(("text_value", ""), _negated=True),
                    ("json_value__isnull", False),
                    _connector="OR",
                ),
                name="domain_metric_value_or_unavailable",
            ),
        ),
        migrations.AddIndex(
            model_name="keyword",
            index=models.Index(
                fields=["run", "intent", "search_volume"], name="domain_keyw_run_id_f0c907_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="keyword",
            constraint=models.UniqueConstraint(
                fields=("run", "normalized_phrase", "country_code", "locale"),
                name="domain_keyword_scope_unique",
            ),
        ),
        migrations.AddIndex(
            model_name="evidence",
            index=models.Index(
                fields=["run", "evidence_type", "availability"],
                name="domain_evid_run_id_715960_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="backlink",
            index=models.Index(
                fields=["run", "referring_domain"], name="domain_back_run_id_9fb19f_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="backlink",
            constraint=models.UniqueConstraint(
                fields=("run", "source_url", "target_url"), name="domain_backlink_unique"
            ),
        ),
        migrations.AddConstraint(
            model_name="urltarget",
            constraint=models.UniqueConstraint(
                fields=("run", "normalized_url"), name="domain_urltarget_unique"
            ),
        ),
    ]
