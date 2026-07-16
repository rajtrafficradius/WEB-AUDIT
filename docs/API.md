# HTTP API contract

API family: Traffic Radius Enterprise SEO Studio  
Current major version: `v1`  
Authentication: Django cookie session plus CSRF  
Last reviewed: 2026-07-15

## Contract scope

The implemented API exposes project and run coordination under `/api/v1/`, with authentication endpoints outside the version prefix. It is a same-origin, browser-oriented API. It does not expose provider credentials, raw private-object keys, live publishing, disavow submission or a public registration flow.

The API description in this document is normative for the implemented routes. A future OpenAPI document must reconcile to these semantics and remain versioned with the code.

## Versioning policy

- The major version appears in the path: `/api/v1/`.
- Backward-compatible additions may be released within `v1`: new endpoints, optional request fields, new response fields and new enumerated values where clients are required to tolerate unknown values.
- Removing or renaming a field, changing authorization meaning, changing a field type, making an optional field required, or changing workflow semantics requires a new major version.
- Security fixes may intentionally narrow previously accepted input without a major version.
- Clients must ignore unknown response fields and must not derive permission from the presence of a button or field.
- Deprecation is announced in response documentation and release notes with a migration window. Both versions must use the same canonical authorization rules.

## Base behavior

| Property | Contract |
|---|---|
| Content type | Requests with a body use `application/json`; form data is accepted only by auth endpoints |
| Response type | JSON for API and auth endpoints |
| Identifier | UUID string |
| Date/time | ISO 8601 with timezone |
| Locale default | `en-AU` |
| Authentication | Cookie session; unauthenticated API request is denied |
| CSRF | Required on every unsafe same-origin request, including login |
| Correlation | Client may send valid `X-Request-ID`; response always includes the accepted/generated value |
| Pagination | Current APIView list endpoints return JSON arrays directly; do not assume DRF pagination metadata |
| Optimistic concurrency | Run transitions and approval decisions require the current run version |
| Idempotency | Run creation requires `Idempotency-Key`, unique within a project |

The server accepts a caller's `X-Request-ID` only when it contains 1–100 letters, digits, `.`, `_` or `-`. Otherwise it generates a UUID. Request IDs are diagnostic correlation values, not idempotency keys and not secrets.

## Authentication flow

### Obtain CSRF token

`GET /auth/csrf/`

Returns HTTP 200 and establishes the session-backed CSRF cookie/token behavior.

```json
{
  "csrf": "set"
}
```

Browser JavaScript sends the token in `X-CSRFToken` on unsafe requests. Cookie handling depends on the same-origin browser session; do not copy tokens between users or environments.

### Sign in

`POST /auth/login/`

```json
{
  "username": "analyst.example",
  "password": "user-supplied-secret"
}
```

Success, HTTP 200:

```json
{
  "user": {
    "id": "4bc9d679-ae2c-4b30-bb39-154a149b2b52",
    "username": "analyst.example",
    "role": "analyst"
  },
  "must_change_password": false
}
```

The server cycles the session key. Invalid, inactive, expired-temporary-password and unknown accounts all receive the same `invalid_credentials` response. Eight cached failures for the address/username key yield HTTP 429 for 15 minutes.

### Current user

`GET /auth/me/`

Success, HTTP 200:

```json
{
  "user": {
    "id": "4bc9d679-ae2c-4b30-bb39-154a149b2b52",
    "username": "analyst.example",
    "display_name": "Example Analyst",
    "role": "analyst",
    "must_change_password": false
  }
}
```

### Change password

`POST /auth/change-password/`

```json
{
  "current_password": "current-secret",
  "new_password": "new-user-supplied-secret"
}
```

Success, HTTP 200:

```json
{
  "status": "password_changed"
}
```

The current session is rotated and retained; other sessions fail their authentication-hash check. A temporary password is cleared and application access is unblocked. Password-policy failures use `field_errors.new_password`.

### Sign out

`POST /auth/logout/`

Success, HTTP 200:

```json
{
  "status": "signed_out"
}
```

### Forced password-change behavior

An authenticated user with `must_change_password=true` receives HTTP 403 `password_change_required` on application and API paths. Only change-password, logout, liveness/readiness and static paths remain available.

## Error envelope

All deliberate API/auth errors use:

```json
{
  "error": {
    "code": "validation_error",
    "message": "The request could not be processed.",
    "request_id": "06a8e434-56bd-4d61-a3d8-1ee54a547c47",
    "retryable": false,
    "field_errors": {
      "approved_domains": ["Include the primary domain."]
    }
  }
}
```

`field_errors` is omitted when not applicable. Provider exception strings, credentials and stack traces are never part of the envelope.

| HTTP | Typical code | Meaning and client behavior |
|---:|---|---|
| 400 | `validation_error`, `password_validation_failed` | Correct request; do not retry unchanged |
| 401 | `invalid_credentials`, authentication failure | Authenticate again; do not enumerate account state |
| 403 | `forbidden`, `password_change_required` | User lacks permission or must change password |
| 404 | `not_found` | Object is absent or outside the user's scope |
| 409 | `transition_conflict` | Refresh run/approval and retry with current version |
| 422 | `invalid_transition`, `approval_required`, `quality_gate_failed` | Satisfy workflow condition before retrying |
| 429 | `authentication_throttled` | Wait; response is retryable |
| 501 | `drf_required` | Development-only fallback lacks a feature; production installs DRF |
| 502/503/504 | adapter/service-specific safe code | Retry only when `retryable=true` and with bounded backoff |

## Authorization semantics

- Agency administrators can access all projects in the agency workspace.
- Analysts and client reviewers see only projects with an active client- or project-level membership.
- Only agency administrators create projects through the API.
- Agency administrators and assigned analysts manage project settings and run transitions.
- Assigned client reviewers may review/request revision and approve Gate 1, Gate 2 and final package.
- High-risk approval is agency-administrator only.
- Client reviewers see only approved artifacts.
- Out-of-scope project/run requests return 404 where implemented, not a permission oracle.

The server evaluates these rules. A client must never hide/show UI controls as its sole protection.

## Projects

### List accessible projects

`GET /api/v1/projects/`

Returns HTTP 200 and an array ordered by client name and project name.

```json
[
  {
    "id": "ee50e20b-52fe-4aab-bf03-b50665082681",
    "client": "afc02ef1-9bc1-45ca-9f8a-b0ad4450c2c5",
    "client_name": "Example Client",
    "name": "Example Enterprise SEO",
    "slug": "example-enterprise-seo",
    "primary_domain": "example.com.au",
    "approved_domains": ["example.com.au"],
    "locale": "en-AU",
    "country_code": "AU",
    "business_type": "ecommerce",
    "default_profile": "enterprise",
    "status": "active",
    "conversion_goals": [],
    "brand_facts": {},
    "prohibited_claims": [],
    "cms_platform": "",
    "created_at": "2026-07-15T03:00:00Z",
    "updated_at": "2026-07-15T03:00:00Z"
  }
]
```

### Create project

`POST /api/v1/projects/`

Permission: agency administrator.

```json
{
  "client": "afc02ef1-9bc1-45ca-9f8a-b0ad4450c2c5",
  "name": "Example Enterprise SEO",
  "slug": "example-enterprise-seo",
  "primary_domain": "example.com.au",
  "approved_domains": ["example.com.au", "shop.example.com.au"],
  "locale": "en-AU",
  "country_code": "AU",
  "business_type": "ecommerce",
  "default_profile": "enterprise",
  "conversion_goals": ["completed_checkout"],
  "brand_facts": {},
  "prohibited_claims": [],
  "cms_platform": ""
}
```

The server lowercases/trims domains and requires the primary domain in `approved_domains`. Creation returns HTTP 201 and the project representation.

### Read project

`GET /api/v1/projects/{project_id}/`

Permission: accessible project. Returns HTTP 200 or scoped 404.

### Update project

`PATCH /api/v1/projects/{project_id}/`

Permission: agency administrator or assigned analyst. Partial body uses the create fields. Domain validation is rerun. Returns HTTP 200.

Changing approved domains while a run is active is an operationally significant action. API permission alone is not sufficient process approval; cancel/restart or formally revision the run so its capture boundary remains auditable.

## Audit runs

### List project runs

`GET /api/v1/projects/{project_id}/runs/`

Permission: accessible project. Returns an array of runs ordered newest first.

### Create run idempotently

`POST /api/v1/projects/{project_id}/runs/`

Permission: agency administrator or assigned analyst. Required header:

```http
Idempotency-Key: client-generated-stable-key-within-128-characters
```

Body:

```json
{
  "profile": "enterprise",
  "rule_version": "2026.07.1"
}
```

`profile` is `quick`, `standard` or `enterprise`. The server uses the project default when omitted. The idempotency key must be nonblank and at most 128 characters.

- First successful request: HTTP 201.
- Replay with the same project/key: HTTP 200 and the original run, even if the body differs. Clients must therefore bind each key to one intended request.

Example representation:

```json
{
  "id": "7a8a7ee8-9323-4629-8296-908f33e0129e",
  "project": "ee50e20b-52fe-4aab-bf03-b50665082681",
  "project_name": "Example Enterprise SEO",
  "profile": "enterprise",
  "state": "draft",
  "version": 1,
  "rule_version": "2026.07.1",
  "source_cutoff_at": null,
  "evidence_coverage": "0.00",
  "confidence": "0.0000",
  "health_score": null,
  "error_code": "",
  "error_summary": "",
  "created_at": "2026-07-15T03:00:00Z",
  "updated_at": "2026-07-15T03:00:00Z",
  "completed_at": null
}
```

### Read run

`GET /api/v1/runs/{run_id}/`

Permission: accessible project. Returns HTTP 200 or scoped 404.

### Transition run

`POST /api/v1/runs/{run_id}/transition/`

```json
{
  "to_state": "collecting",
  "expected_version": 1,
  "reason": ""
}
```

`expected_version` is required. On success, the server increments `version` and returns the updated run. A stale value returns HTTP 409. A revision request requires a nonblank reason.

Happy-path states:

```text
draft -> collecting -> auditing -> gate_1_review -> planning -> generating
      -> gate_2_review -> final_qa -> packaged -> approved
```

Additional states are `revision_requested`, `failed` and `cancelled`. Only transitions defined by the server state machine are accepted.

Workflow guards:

- Gate 1 approval is required for `gate_1_review -> planning`.
- Gate 2 approval is required for `gate_2_review -> final_qa`.
- `final_qa -> packaged` requires no failed Critical/High QA and no unapproved approval-required artifact.
- Package approval is required for `packaged -> approved`.

## Approvals

### Decide approval

`POST /api/v1/approvals/{approval_id}/decision/`

```json
{
  "decision": "approved",
  "expected_run_version": 8,
  "comment": "Evidence and scope reviewed."
}
```

Final decisions are `approved`, `revision_requested` or `rejected`. A non-approval requires a nonblank comment. Pending approvals are single-decision records; deciding an already decided approval returns a conflict. The run version increments on success.

Gate authorization:

- `gate_1`, `gate_2`, `package`: agency administrator or assigned client reviewer;
- `high_risk`: agency administrator only.

When the approval targets an artifact, an approval sets the artifact review status to approved; a non-approval sets it to revision requested. Changed artifact bytes require a new artifact/hash and new approval.

## Artifacts

### List run artifacts

`GET /api/v1/runs/{run_id}/artifacts/`

Permission: accessible project. Analysts/administrators receive all run artifacts; client reviewers receive only approved artifacts.

```json
[
  {
    "id": "a419de06-7a92-447e-8034-e2076898c1cb",
    "run": "7a8a7ee8-9323-4629-8296-908f33e0129e",
    "artifact_type": "executive_report",
    "title": "Enterprise SEO Executive Report",
    "format": "pdf",
    "sha256": "64-lowercase-hex-characters",
    "size_bytes": 148200,
    "media_type": "application/pdf",
    "risk_class": "low",
    "approval_required": false,
    "review_status": "approved",
    "approved_at": "2026-07-15T05:00:00Z",
    "metadata": {},
    "created_at": "2026-07-15T04:00:00Z"
  }
]
```

`storage_key` is intentionally not serialized.

The current v1 route set lists artifact metadata but does not expose a binary download endpoint. A future download route must re-authorize the artifact at request time, filter client reviewers to approved status, record an audit event and stream privately or issue a short-lived single-object URL. It must not expose the storage key as a public path.

## Health endpoints

Health routes are intentionally unversioned and do not expose secrets.

### Liveness

`GET /healthz/`

```json
{
  "status": "ok",
  "service": "traffic-radius-seo-studio"
}
```

HTTP 200 proves process liveness only.

### Readiness

`GET /readyz/`

Success, HTTP 200:

```json
{
  "status": "ready",
  "checks": {
    "database": "ok",
    "cache": "ok",
    "secret_key": "ok"
  }
}
```

Failure returns HTTP 503 with `status=not_ready` and one or more values set to `unavailable`. It does not return connection strings or exception details.

## Enumerations

### Roles

`agency_admin`, `analyst`, `client_reviewer`

### Run profiles

`quick`, `standard`, `enterprise`

### Run states

`draft`, `collecting`, `auditing`, `gate_1_review`, `planning`, `generating`, `gate_2_review`, `final_qa`, `packaged`, `approved`, `revision_requested`, `failed`, `cancelled`

### Approval gates and decisions

- Gates: `gate_1`, `gate_2`, `high_risk`, `package`
- Decisions: `pending`, `approved`, `revision_requested`, `rejected`

### Review status

`draft`, `in_review`, `approved`, `revision_requested`, `rejected`

### Risk class

`low`, `medium`, `high`, `dangerous`

Clients should render an unknown value safely and retain it; they must not map unknown risk or approval values to an approved/low-risk default.

## Example safe transition exchange

```http
POST /api/v1/runs/7a8a7ee8-9323-4629-8296-908f33e0129e/transition/ HTTP/1.1
Content-Type: application/json
X-CSRFToken: <session-token>
X-Request-ID: ui-20260715-00192

{"to_state":"collecting","expected_version":1,"reason":""}
```

Success:

```http
HTTP/1.1 200 OK
X-Request-ID: ui-20260715-00192
Content-Type: application/json
```

Stale editor:

```json
{
  "error": {
    "code": "transition_conflict",
    "message": "The run changed; refresh before trying again",
    "request_id": "ui-20260715-00192",
    "retryable": false
  }
}
```

The client refreshes the run, shows the newer state/decision to the user and asks them to retry intentionally. It must not silently replay a stale approval.
