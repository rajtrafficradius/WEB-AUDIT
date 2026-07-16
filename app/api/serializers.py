from __future__ import annotations

try:
    from rest_framework import serializers
except ImportError:  # pragma: no cover
    serializers = None

from app.domain.models import Approval, Artifact, AuditRun, Client, Project

if serializers is not None:

    class ClientSerializer(serializers.ModelSerializer):
        class Meta:
            model = Client
            fields = ("id", "name", "slug", "brand_name", "primary_colour", "accent_colour")

    class ProjectSerializer(serializers.ModelSerializer):
        client_name = serializers.CharField(source="client.name", read_only=True)

        class Meta:
            model = Project
            fields = (
                "id",
                "client",
                "client_name",
                "name",
                "slug",
                "primary_domain",
                "approved_domains",
                "locale",
                "country_code",
                "business_type",
                "default_profile",
                "status",
                "conversion_goals",
                "brand_facts",
                "prohibited_claims",
                "cms_platform",
                "created_at",
                "updated_at",
            )
            read_only_fields = ("created_at", "updated_at")

        def validate(self, attrs):
            primary = (
                attrs.get("primary_domain", getattr(self.instance, "primary_domain", ""))
                .strip()
                .lower()
                .rstrip(".")
            )
            approved = attrs.get("approved_domains", getattr(self.instance, "approved_domains", []))
            normalized = sorted(
                {str(item).strip().lower().rstrip(".") for item in approved if str(item).strip()}
            )
            if primary not in normalized:
                raise serializers.ValidationError(
                    {"approved_domains": "Include the primary domain."}
                )
            attrs["primary_domain"] = primary
            attrs["approved_domains"] = normalized
            return attrs

    class AuditRunSerializer(serializers.ModelSerializer):
        project_name = serializers.CharField(source="project.name", read_only=True)

        class Meta:
            model = AuditRun
            fields = (
                "id",
                "project",
                "project_name",
                "profile",
                "state",
                "version",
                "rule_version",
                "source_cutoff_at",
                "evidence_coverage",
                "confidence",
                "health_score",
                "error_code",
                "error_summary",
                "created_at",
                "updated_at",
                "completed_at",
            )
            read_only_fields = fields

    class ApprovalSerializer(serializers.ModelSerializer):
        class Meta:
            model = Approval
            fields = (
                "id",
                "run",
                "artifact",
                "gate",
                "target_type",
                "target_id",
                "decision",
                "requested_by",
                "reviewed_by",
                "requested_at",
                "decided_at",
                "comment",
                "created_at",
                "updated_at",
            )
            read_only_fields = fields

    class ArtifactSerializer(serializers.ModelSerializer):
        class Meta:
            model = Artifact
            fields = (
                "id",
                "run",
                "artifact_type",
                "title",
                "format",
                "sha256",
                "size_bytes",
                "media_type",
                "risk_class",
                "approval_required",
                "review_status",
                "approved_at",
                "metadata",
                "created_at",
            )
            read_only_fields = fields
else:
    ClientSerializer = ProjectSerializer = AuditRunSerializer = ApprovalSerializer = (
        ArtifactSerializer
    ) = None
