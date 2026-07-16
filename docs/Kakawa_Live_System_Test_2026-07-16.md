# Traffic Radius Enterprise SEO Studio — Live System Test

**Test date:** 16 July 2026  
**Test website:** Kakawa Chocolates Australia  
**Approved domain:** `https://kakawachocolates.com.au`  
**Local project:** `Kakawa Chocolates Enterprise SEO Live Test`  
**Project ID:** `821b236c-7492-4c5f-8a66-4220c7b33f14`

## Purpose

Confirm that the locally running application is usable for authenticated enterprise SEO project intake, preserves approved-domain boundaries, records the project correctly, reports missing evidence truthfully, and exposes the expected audit workflow. Check the OpenAI integration and run a small automated regression test.

## Data entered

- Client: Kakawa Chocolates
- Project: Kakawa Chocolates Enterprise SEO Live Test
- Profile: Standard
- Locale: English (Australia)
- Business type: Ecommerce
- CMS: Shopify
- Market: Australia, with a Sydney focus
- Goals: online purchases, corporate and wholesale enquiries, email/rewards registrations
- Priority offerings: gift boxes, pralines and bonbons, corporate, wholesale, and seasonal chocolates
- Competitors: intentionally blank; no unverified competitor evidence was invented
- Prohibited claims: awards, ratings, review counts, health/ingredient claims, delivery guarantees, sustainability claims, rankings, traffic, or revenue without evidence

## Expected workflow

`Project intake → source connections/imports → crawl and evidence collection → audits → Gate 1 → strategy/content generation → Gate 2 → final QA → package → approval`

## Results

| Check | Result | Evidence |
|---|---|---|
| Local application | PASS | Authenticated UI accepted and saved the project. |
| Domain boundary | PASS | Canonical domain stored as `kakawachocolates.com.au`. |
| Project persistence | PASS | Project record exists in the local database. |
| Honest unavailable state | PASS | No source, run, import, score, or finding was fabricated when credentials/evidence were absent. |
| Automated regression | PASS | 19 targeted UI, worker, workflow, and render-worker tests passed. |
| Existing Kakawa audit/export benchmark | PASS | v19 package and ZIP are present; unpacked package contains 56 files. |
| Fresh end-to-end run from this UI project | NOT STARTED | The project currently has 0 runs, 0 source connections, and 0 imports. |
| Source connection UI | GAP | Backend POST handling exists, but the GET page does not render the connection form. |
| Run launch UI | GAP | No visible control currently creates/queues an audit run from the project page. |

## Existing Kakawa benchmark output

The previously generated v19 Kakawa acceptance package demonstrates the deterministic crawl, audit, QA, and export engines: 366 discovered URLs, 357 unique normalized pages, 5 findings, 16 actions, 6 evidence-supported content assets, 35% evidence coverage, and an intentionally withheld overall health score. GSC, GA4, SEMrush, PageSpeed, and OpenAI are recorded as unavailable where credentials/data were not supplied.

## OpenAI status

The OpenAI Responses API boundary is integrated in source code. It includes strict structured output validation, prompt-injection isolation, evidence-only fact packs, request/response hashes, usage ledgers, retries, and safe unavailable states.

**Runtime status:** not active. `OPENAI_API_KEY` is absent from both the process environment and a local `.env` file. Add a valid key before AI generation can run. The configured model names are `gpt-5.6-sol` and `gpt-5.6-luna`; confirm that these exact model IDs are enabled in the intended OpenAI account, or replace them with account-supported model IDs using `OPENAI_STRATEGY_MODEL` and `OPENAI_EXTRACTION_MODEL`.

Never store the real API key in source control. Copy `.env.example` to `.env`, set `OPENAI_API_KEY`, and keep `.env` private.

## Verdict

The application is running and the project-intake/authentication/domain-control path is usable. Core deterministic engines and professional package generation are covered by passing tests and the existing Kakawa v19 package. The newly created project cannot yet execute the whole workflow solely through the UI because source-connection form rendering and audit-run launch controls remain unwired, and external credentials/workers are not configured. Therefore, this is a successful live intake smoke test, not a completed fresh enterprise audit run.

## Local link

`http://127.0.0.1:8000/projects/821b236c-7492-4c5f-8a66-4220c7b33f14/`
