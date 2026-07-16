from django.db import models


class UserRole(models.TextChoices):
    AGENCY_ADMIN = "agency_admin", "Agency administrator"
    ANALYST = "analyst", "Analyst"
    CLIENT_REVIEWER = "client_reviewer", "Client reviewer"


class RunProfile(models.TextChoices):
    QUICK = "quick", "Quick"
    STANDARD = "standard", "Standard"
    ENTERPRISE = "enterprise", "Enterprise"


class RunState(models.TextChoices):
    DRAFT = "draft", "Draft"
    COLLECTING = "collecting", "Collecting"
    AUDITING = "auditing", "Auditing"
    GATE_1_REVIEW = "gate_1_review", "Gate 1 review"
    PLANNING = "planning", "Planning"
    GENERATING = "generating", "Generating"
    GATE_2_REVIEW = "gate_2_review", "Gate 2 review"
    FINAL_QA = "final_qa", "Final QA"
    PACKAGED = "packaged", "Packaged"
    APPROVED = "approved", "Approved"
    REVISION_REQUESTED = "revision_requested", "Revision requested"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class StageStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    SKIPPED = "skipped", "Skipped"
    CANCELLED = "cancelled", "Cancelled"


class Severity(models.TextChoices):
    INFO = "info", "Info"
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    CRITICAL = "critical", "Critical"


class ApprovalDecision(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REVISION_REQUESTED = "revision_requested", "Revision requested"
    REJECTED = "rejected", "Rejected"


class ApprovalGate(models.TextChoices):
    GATE_1 = "gate_1", "Gate 1"
    GATE_2 = "gate_2", "Gate 2"
    HIGH_RISK = "high_risk", "High-risk asset"
    PACKAGE = "package", "Final package"


class AvailabilityStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    AVAILABLE = "available", "Available"
    UNAVAILABLE = "unavailable", "Unavailable"
    ERROR = "error", "Error"


class ReviewStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    IN_REVIEW = "in_review", "In review"
    APPROVED = "approved", "Approved"
    REVISION_REQUESTED = "revision_requested", "Revision requested"
    REJECTED = "rejected", "Rejected"


class RiskClass(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    DANGEROUS = "dangerous", "Dangerous"
