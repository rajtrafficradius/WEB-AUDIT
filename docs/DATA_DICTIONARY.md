# Canonical data dictionary

Document owner: Traffic Radius data and engineering  
Canonical implementation: `app/domain/models.py`  
Last reviewed: 2026-07-15

## Reading this dictionary

This document explains meaning and lineage. Django migrations remain the executable schema authority. If this document and a migration differ, stop the release, reconcile both, and add a regression test rather than silently choosing one.

Unless noted otherwise, domain entities use a UUID primary key plus `created_at` and `updated_at` timestamps. Decimal confidence values range from `0` to `1`; percentage/score values range from `0` to `100`.

## Shared evidence fields

### Availability

Entities using the availability contract store:

| Field | Meaning |
|---|---|
| `availability` | `pending`, `available`, `unavailable`, or `error` |
| `unavailable_reason` | Human-safe reason; required when status is `unavailable` |

Absence is data. Do not encode unavailable as zero, an empty string, a fabricated estimate or a dropped row.

### Evidence metadata

Evidence-bearing snapshots/observations store:

| Field | Meaning |
|---|---|
| `captured_at` | When this source fact was observed, timezone-aware |
| `locale` | Locale such as `en-AU`, blank only when not applicable |
| `device` | Device dimension such as mobile/desktop, blank when not applicable |
| `scope` | Human/machine-readable scope, property, date range or segment |
| `rule_version` | Version of collection, transformation or deterministic rule |
| `confidence` | Evidence confidence from 0 through 1 |

`created_at` is database creation time and is not a substitute for `captured_at` or a provider's measurement period.

### Integrity and lineage

- `sha256` identifies exact bytes or canonical content where supplied.
- `storage_key` is a private object locator, never a public URL or permission.
- `source_snapshot` links an observation to one immutable capture.
- `source_import` or `connection` identifies how the snapshot was obtained.
- `page` identifies the exact normalized page snapshot when applicable.
- Many-to-many `evidence` fields identify all supporting evidence records.

## Identity and tenancy

### `User`

Custom Django user used for authentication and authorization.

| Field | Contract |
|---|---|
| `username` | Unique login ID inherited from `AbstractUser` |
| `role` | `agency_admin`, `analyst`, or `client_reviewer` |
| `must_change_password` | Blocks application access until password replacement |
| `temporary_password_expires_at` | Expiry for administrator-issued recovery credential |
| `password_changed_at` | Audit-supporting timestamp for latest application change |
| `last_activity_at` | Optional activity marker |
| `is_active`, `is_staff`, `is_superuser` | Django account/operator controls |

Passwords use Django hash fields; no plaintext password field exists.

### `Client`

One client organization within the Traffic Radius agency workspace.

| Field | Contract |
|---|---|
| `name`, `slug` | Display name and globally unique client slug |
| `brand_name` | Optional co-brand label |
| `logo_storage_key` | Private object key for client logo |
| `primary_colour`, `accent_colour` | Co-brand presentation values |
| `retention_days` | Client data retention period, default 365 |
| `archived_at` | Soft-archive time |

### `Project`

The authorization, approved-domain and audit configuration boundary.

| Field | Contract |
|---|---|
| `client` | Owning client; deletion protected through run relationships where relevant |
| `name`, `slug` | Project display and client-scoped unique slug |
| `primary_domain` | Canonical root domain without scheme/path |
| `approved_domains` | JSON list of exact allowed roots; must include `primary_domain` |
| `locale`, `country_code` | Default market context |
| `business_type` | `service`, `saas`, `local`, `ecommerce`, or `hybrid` |
| `default_profile` | `quick`, `standard`, or `enterprise` |
| `status` | `active`, `paused`, or `archived` |
| `conversion_goals` | Versioned business goal descriptors |
| `brand_facts` | Approved structured facts; not a free-form evidence substitute |
| `prohibited_claims` | Claims generation must not make |
| `cms_platform` | Advisory deployment platform label |

Project domain changes do not rewrite historical runs. A run's snapshots and source cutoff preserve what was analyzed.

### `Membership`

Associates a user with a client and optionally one project.

| Field | Contract |
|---|---|
| `user`, `client` | Subject and client scope |
| `project` | Null for client-wide membership; UUID for project-specific membership |
| `access_role` | Role recorded for the membership context |
| `is_active` | Revocable access flag |

Only one active/logical membership row per user/client-wide scope or user/project scope is allowed by unique constraints. A project's client must equal the membership client.

## Source configuration and capture

### `Connection`

Encrypted configuration for a live evidence source.

| Field | Contract |
|---|---|
| `project` | Project scope |
| `provider` | `gsc`, `ga4`, `semrush`, `pagespeed`, or `s3` |
| `label` | Distinguishes accounts/properties for the same provider |
| `encrypted_credentials`, `encryption_key_id` | Authenticated ciphertext and key-ring identifier |
| `external_account_id` | Provider property/account identifier, not a secret |
| `scopes` | Granted OAuth/API scopes |
| `expires_at`, `last_synced_at` | Credential expiry and collection state |
| availability fields | Current connection availability and reason |

Unique key: `(project, provider, label)`.

### `SourceImport`

Quarantined and validated upload record.

| Field | Contract |
|---|---|
| `project`, `created_by` | Tenant and actor |
| `source_type` | Mapped source such as Ahrefs, Screaming Frog or a declared custom schema |
| `original_filename` | Display/audit metadata only; never an object path |
| `media_type`, `size_bytes`, `sha256` | Validated file identity |
| `storage_key` | Private quarantine/accepted object locator |
| `status` | `quarantined`, `validating`, `accepted`, or `rejected` |
| `schema_version` | Versioned accepted import contract |
| `column_mapping` | Source-to-canonical mapping |
| `validation_issues` | Structured safe validation outcomes |
| availability fields | Whether the source can contribute evidence and why not |

### `SourceSnapshot`

Immutable logical capture used by one audit run.

| Field | Contract |
|---|---|
| `run`, `source_type` | Owning run and source kind |
| `source_import`, `connection` | Exactly applicable origin references; both may be null for crawler/replay capture |
| `storage_key`, `sha256` | Raw/normalized capture object and integrity |
| `record_count` | Canonical count produced by this snapshot |
| `metadata` | Provider/request schema, sampling and reconciliation metadata |
| availability/evidence metadata | Source state, as-of and scope |

Do not update a snapshot when a provider is recollected. Create a new run/snapshot or an explicit newer capture according to workflow versioning.

## Runs and checkpoints

### `AuditRun`

Versioned audit/strategy execution for one project.

| Field | Contract |
|---|---|
| `project`, `created_by` | Scope and actor |
| `profile` | `quick`, `standard`, or `enterprise` |
| `state` | Workflow state defined below |
| `idempotency_key` | Project-scoped unique run-create key |
| `version` | Optimistic concurrency counter |
| `rule_version` | Deterministic audit/scoring ruleset |
| `source_cutoff_at` | Latest evidence inclusion/as-of boundary |
| `evidence_coverage` | Weighted 0–100 coverage |
| `confidence` | Aggregate bounded confidence |
| `health_score` | Optional 0–100; database requires coverage at least 70 |
| `error_code`, `error_summary` | Safe terminal/transient error metadata |
| `cancelled_at`, `completed_at` | Terminal timestamps |

Unique key: `(project, idempotency_key)`.

Workflow states: `draft`, `collecting`, `auditing`, `gate_1_review`, `planning`, `generating`, `gate_2_review`, `final_qa`, `packaged`, `approved`, `revision_requested`, `failed`, `cancelled`.

### `RunStage`

Durable sub-operation/checkpoint within a run.

| Field | Contract |
|---|---|
| `run`, `name`, `sequence` | Run, stable stage identifier and order |
| `status` | `pending`, `running`, `succeeded`, `failed`, `skipped`, or `cancelled` |
| `attempts` | Number of executions |
| `checkpoint` | JSON resume cursor/count/hash state; no credentials |
| `started_at`, `heartbeat_at`, `finished_at` | Execution timing |
| `error_code`, `error_summary` | Safe failure information |

Unique key: `(run, name)`.

## Page and metric observations

### `PageSnapshot`

One normalized page identity within one run.

| Field | Contract |
|---|---|
| `run`, `source_snapshot` | Run and capture origin |
| `original_url` | First/as-captured source URL retained for audit |
| `normalized_url` | Stable deduplication identity |
| `domain`, `approved_domain` | Extracted host and boundary result |
| `status_code`, `content_type`, `response_ms` | HTTP observation |
| `canonical_url`, `redirect_target_url` | Graph edges as observed |
| `robots_indexable` | True/false/unknown indexability observation |
| `title`, `meta_description`, `h1` | Extracted on-page values |
| `content_sha256` | Exact normalized/raw content identity used by the collector |
| `facts` | Additional validated structured page facts |
| evidence metadata | As-of, locale, device, scope, rule and confidence |

Unique key: `(run, normalized_url)`. Duplicate original URLs normalize to one page; the collection/reconciliation report retains duplicate-source counts.

### `MetricObservation`

A typed measured value.

| Field | Contract |
|---|---|
| `run`, `source_snapshot`, `page` | Run, source and optional page scope |
| `metric_key` | Versioned canonical metric name |
| `numeric_value`, `text_value`, `json_value` | Exactly applicable typed representation |
| `unit` | Unit such as clicks, milliseconds or percent |
| `period_start`, `period_end` | Provider measurement period |
| availability/evidence metadata | Source/as-of/scope/confidence |

Constraint: at least one value must be present unless availability is `unavailable`. A numeric zero is a measured value and differs from null/unavailable.

### `Evidence`

Human- and machine-addressable support for a finding, claim or QA result.

| Field | Contract |
|---|---|
| `run`, `source_snapshot`, `page` | Scope and origin |
| `evidence_type` | Versioned evidence category |
| `title`, `excerpt` | Safe summary; excerpt is bounded by renderer/export policy |
| `locator` | Row/cell/JSON path/CSS-like locator or provider key |
| `storage_key`, `sha256` | Optional private binary evidence identity |
| `details` | Validated structured details |
| availability/evidence metadata | Complete provenance and absence reason |

An Evidence row with `available` status must not refer to a missing private object when that object is required to verify it.

## Findings, recommendations and action plan

### `Finding`

Deterministic or human-validated issue discovered in a run.

| Field | Contract |
|---|---|
| `run`, `page` | Run and optional page scope |
| `category`, `code` | Audit module and stable rule code |
| `title`, `description` | Human explanation |
| `severity` | `info`, `low`, `medium`, `high`, or `critical` |
| `affected_count`, `affected_share` | Reach count and share |
| `score_penalty` | Transparent deterministic category penalty |
| `confidence`, `rule_version` | Evidence confidence and rule revision |
| `status` | `open`, `accepted`, `resolved`, or `dismissed` |
| `evidence` | All supporting Evidence rows |

### `Recommendation`

Proposed response to one finding.

| Field | Contract |
|---|---|
| `finding` | Causal finding |
| `title`, `rationale`, `implementation` | What, why and how |
| `impact`, `effort` | Ordinal 1–5 inputs |
| `risk_class` | `low`, `medium`, `high`, or `dangerous` |
| `review_status` | Draft/review/approval lifecycle |

Implementation risk is independent from priority and severity.

### `ActionItem`

Authoritative 16-week plan row.

| Field | Contract |
|---|---|
| `run`, `recommendation` | Run and optional causal recommendation |
| `title`, `description` | Action definition |
| `week` | Integer 1–16 |
| `owner_label` | Responsible role/team label, not necessarily a user account |
| `impact`, `evidence_confidence`, `reach`, `business_criticality`, `dependency_urgency`, `effort` | 0–100 priority inputs |
| `priority_score`, `priority_tier` | Deterministic score and P1–P4 band |
| `risk_class`, `review_status` | Separate implementation risk and approval state |
| `dependencies` | Directed self-relations to prerequisite actions |

Action ordering is week, descending priority score, then title. The calculation version belongs to the run/ruleset and exported ledger.

## Keywords, architecture and links

### `Keyword`

| Field | Contract |
|---|---|
| `run`, `source_snapshot` | Run and metric source |
| `phrase`, `normalized_phrase` | Original and deduplicated keyword |
| `country_code`, `locale`, `device`, `scope` | Market dimensions |
| `intent` | Validated/classified intent |
| `search_volume`, `difficulty`, `cpc`, `position` | Nullable source metrics, never fabricated |
| availability/evidence metadata | Capture and evidence status |

Unique key: `(run, normalized_phrase, country_code, locale)`.

### `KeywordCluster`

Named topical/intent cluster for one run with optional pillar keyword, rationale and many-to-many keywords. Cluster name is unique within a run.

### `URLTarget`

| Field | Contract |
|---|---|
| `run`, `cluster` | Run and optional topic cluster |
| `original_url`, `normalized_url` | Preserved input and canonical target identity |
| `target_type`, `proposed_action`, `intent`, `rationale` | Strategy decision |
| `risk_class`, `review_status` | Deployment safety and review lifecycle |

Unique key: `(run, normalized_url)`.

### `Backlink`

| Field | Contract |
|---|---|
| `run`, `source_snapshot` | Run and evidence source |
| `source_url`, `target_url`, `referring_domain` | Link edge |
| `anchor_text`, `link_type` | Observed attributes |
| `authority_score`, `toxicity_score` | Nullable provider metrics |
| `first_seen`, `last_seen` | Provider observation dates |
| availability/evidence metadata | Provenance and source status |

Unique key: `(run, source_url, target_url)`. Toxicity does not itself authorize disavow; manual-action risk, removal attempts and administrator approval are separate required evidence.

## Content and claims

### `ContentBrief`

Evidence-approved plan for one potential content asset.

| Field | Contract |
|---|---|
| `run`, `cluster` | Run and topic cluster |
| `title`, `slug`, `target_url` | Unique asset identity in the run |
| `primary_keyword`, `search_intent`, `outline` | Strategy fields |
| `approved_fact_pack` | Strict versioned facts supplied to generation |
| `source_evidence` | Evidence rows backing the fact pack |
| `review_status` | Human review lifecycle |

Unique keys: `(run, slug)` and `(run, target_url)`.

### `ContentDraft`

| Field | Contract |
|---|---|
| `brief`, `version` | Brief and monotonically increasing draft version |
| `format`, `body` | Markdown/other controlled source format and content |
| `model_id`, `prompt_version` | Returned/configured generation identity as applicable |
| `request_sha256`, `response_sha256` | Generation integrity ledger |
| `review_status` | Human review state |

Unique key: `(brief, version)`. Additional token, cost and returned-model details belong in the generation ledger/artifact metadata; blank fields mean not generated by a model, not an inferred model.

### `ClaimLedger`

| Field | Contract |
|---|---|
| `draft`, `claim_text` | Exact claim and draft version |
| `status` | `pending`, `supported`, `unsupported`, or `removed` |
| `evidence` | Evidence supporting the exact claim |
| `reviewer_notes` | Human disposition notes |

A client-approved draft must contain no `pending` or `unsupported` factual claim.

## Artifacts, approvals and QA

### `Artifact`

| Field | Contract |
|---|---|
| `run`, `created_by` | Owning run and actor/process identity |
| `artifact_type`, `title`, `format`, `media_type` | Semantic and file type |
| `storage_key`, `sha256`, `size_bytes` | Private immutable byte identity |
| `risk_class`, `approval_required` | Safety classification and hard approval flag |
| `review_status`, `approved_at` | Review lifecycle |
| `metadata` | Template version, derivation, source hashes and renderer details |

Unique key: `(run, storage_key)`. A change in bytes creates a new hash/object and requires re-QA/reapproval.

### `Approval`

| Field | Contract |
|---|---|
| `run`, `artifact` | Run and optional targeted artifact |
| `gate` | `gate_1`, `gate_2`, `high_risk`, or `package` |
| `target_type`, `target_id` | Optional typed non-artifact target |
| `decision` | `pending`, `approved`, `revision_requested`, or `rejected` |
| `requested_by`, `reviewed_by` | Actors |
| `requested_at`, `decided_at`, `comment` | Review evidence |

Only one pending approval per `(run, gate, target_type, target_id)` is allowed. A rejection/revision requires a comment at service/API level.

### `QAResult`

| Field | Contract |
|---|---|
| `run`, `artifact` | Run and optional file scope |
| `check_code`, `check_version` | Stable test identity and version |
| `severity` | Info through Critical |
| `status` | `pass`, `fail`, `warn`, or `skip` |
| `message`, `details` | Human summary and machine details |
| `evidence` | Evidence proving the outcome |

Unique key: `(run, artifact, check_code, check_version)`. A skipped check requires a reason in details/message and cannot be treated as pass.

### `PackageManifest`

| Field | Contract |
|---|---|
| `run`, `package_artifact` | Run and final ZIP artifact |
| `version` | Manifest version unique within the run |
| `manifest`, `manifest_sha256` | Canonical inventory and its hash |
| `package_sha256` | Final ZIP transfer hash |
| `status`, `generated_by` | Review state and actor/process |

The manifest lists each file once, its relative POSIX path, media/format, byte size, SHA-256, semantic type, review/risk state and derivation relationship. Absolute machine paths are prohibited.

### `AuditEvent`

Append-only application audit trail.

| Field | Contract |
|---|---|
| `created_at`, `actor` | Time and authenticated actor when present |
| `client`, `project`, `run` | Resolved scope |
| `event_type`, `object_type`, `object_id` | Stable action and subject |
| `request_id`, `ip_address` | Correlation and normalized remote address |
| `payload` | Narrow non-secret structured details |

Application updates/deletes raise validation errors. Database privilege and external audit controls are still required to protect against administrator-level tampering.

## Deterministic engine contracts

`audit_engine.models` defines immutable, framework-independent records used by collectors/rules before or alongside ORM persistence:

- `Provenance`
- `SourceSnapshot`
- `EvidenceRecord`
- `MetricObservation`
- `PageSnapshot`
- `Finding`
- `Recommendation`
- `ActionCandidate`
- `VerifiedFact`

These contracts validate UUIDs, timezone awareness, JSON safety, evidence references, confidence and nonempty values. Mapping them to ORM entities must preserve source IDs, timestamps, availability and version fields.

## Reconciliation invariants

Every run/package verifier must assert:

1. Page count equals unique `(run, normalized_url)` canonical pages; duplicate source rows are reported separately.
2. All page domains are within the project's approved boundary or explicitly classified as external competitor evidence outside crawler page identity.
3. Snapshot `record_count` reconciles to its normalized import/collection record set, with documented exclusions.
4. Every finding/recommendation/action/claim that depends on evidence has valid evidence references in the same run.
5. Health score is null below 70 percent weighted evidence coverage.
6. Unavailable sources have nonempty reasons and contribute no fabricated metric values.
7. All approved artifacts exist, match canonical byte size/SHA-256, and have no unresolved Critical/High QA.
8. Client-downloadable artifacts are approved.
9. Package inventory, cross-format reports and UI counts reconcile.
10. Manifest hash, ZIP hash and adjacent checksum validate.
