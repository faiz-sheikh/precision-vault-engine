# Precision Vault Engine Implementation Authorization

## Record metadata

- Recorded at: `2026-07-29T11:13:13+05:30`
- Authorization effective date: `2026-07-29`
- Implementation plan: `Precision Vault Engine — Reconciled Startup MVP Architecture and Implementation Plan`
- Implementation-plan version: `unversioned supplied revision`
- Implementation-plan SHA-256: `d2ae27afe5bfc26ffb9862ac0d161d242ebd360e6a08b016227e42ce93b931cc`
- Git commit: `unavailable; supplied workspace is not a Git repository`
- Repository checksum: `fa7054ba2077a76057457acc7ab3932d56f6b32e4bbe7741d249c03be4154d0d`
- Repository-checksum method: SHA-256 of the sorted per-file SHA-256 inventory, excluding `venv/` and this authorization record.
- Authorization source: direct user response in the implementation session.

This is a non-secret authorization record. It intentionally contains no credentials, API keys, tokens, or secret values.

## Permanent permissions

Grant type: **permanent for Releases 0–7 unless revoked by the user**.

| Permission | Scope | Restrictions |
|---|---|---|
| A1 — Repository inspection | Supplied source, configuration structure, manifests, state/registry files, tests, documentation, tree structure, metadata/permissions, hashes, search, static analysis, and available Git state/history. | Do not expose secrets or send repository content externally. Missing review-copy files are not production defects without evidence. |
| A2 — Local source-code modification | Create, modify, and reorganize local source; controller-backed legacy wrappers; comments, typing, docs, templates, safe-mode, and feature gates; removal only with migration/rollback preservation. | Preserve unrelated behavior and legacy data; follow approved architecture; no provider calls from tests or imports. |
| A3 — Local test execution | Offline tooling, unit/integration/crash tests, mocks, network blocking, repeated tests, typing, linting, formatting, and temporary fixtures. | Mock all providers; no live hosts, paid quota, or real credentials. |
| A4 — Project-local filesystem operations | Approved directories, temporary files, backups, checksums, snapshots, quarantine, inventories, logs, local databases, atomic generated-file replacement, and permissions on project-owned non-secret files. | Stay in approved project/backup/temp roots; no arbitrary system changes; no unbacked legacy deletion; no cleanup of raw paid responses or financial history. |
| A5 — SQLite creation and migration | Operational SQLite database, approved WAL/transactions, migrations, indexes, constraints, append-only financial events, operational tables, backups, integrity/restore testing, and verified legacy-state migration. | Never silently recreate missing/corrupt production data; fail closed; preserve backups and immutable history; no unsupported network storage for WAL. |
| A6 — Manifest candidate generation and validation | Immutable legacy candidates, candidate generation, schemas/checksums/comparison reports, ID mappings, authority proposals, quarantine, and promotion previews. | No automatic promotion or authoritative overwrite; no silent semantic merge or count-based authority assumption; no paid mapping call without fresh approval. |
| A7 — Local artifact migration preparation | Inventory and hash artifacts, inspect metadata, detect collisions, propose mappings, stage verifiable copies, quarantine ambiguity, and report. | No multi-assignment by guesswork, overwrite, external regeneration, original deletion, or collision-evidence deletion. |
| A8 — Documentation and configuration updates | Implementation/operator/recovery/setup documentation, architecture corrections, examples, ignore/secret rules, locked dependencies, provider disclosures, and financial controls. | No credentials, guaranteed-price claims, or premature release-completion claims. |
| A9 — Local Git operations | Inspect, branch, stage, locally commit/tag after acceptance, diff, and revert Codex-created changes. | No remote push, shared-history rewrite, remote-branch deletion, or secret/private-data commits. |
| A10 — Dashboard and controller implementation | Controller, worker, CLI, Streamlit dashboard/pages, local interfaces, localhost testing, idempotent commands, and read-only refresh testing. | Rendering cannot call providers; localhost only by default; no public exposure, multi-user system, or SaaS deployment. |

## One-time initial permissions

Grant type: **one-time; expires when the named initial action completes**.

| Permission | Scope | Restrictions |
|---|---|---|
| B1 — Initial immutable backup | One timestamped, read-only migration snapshot of supplied source, both manifests, state/registries, artifact metadata, templates, docs, and repository identity, with verified checksums. | Do not place secret values in a distributable backup; protect live credential files separately. |
| B2 — Secret and permission remediation | Identify credential-bearing files without content display, restrict permissions, remove them from distributable/tracked locations, add ignores/templates, and warn about rotation. | No secret revelation/upload, unsupported rotation, or deletion of the only usable credential copy without fresh approval. |
| B3 — Legacy safe-mode activation | Reversible safeguard blocking direct paid legacy execution through flags, controller redirection, or refusal until Release 1 gates pass while allowing offline/read-only work. | Must have documented rollback. |
| B4 — Initial SQLite database creation | First operational database/schema after backup, plan/permission validation, and successful test database. | Uncertain financial/batch state must remain `manual_review` or `ambiguous_acceptance`, never confirmed truth. |
| B5 — Legacy artifact staging | Copy uniquely attributable artifacts into migration staging. | No deletion, promotion, regeneration, overwrite, or guessed collision resolution. |
| B6 — Approved dependency installation | Install pinned local runtime/test dependencies after a package, purpose, constraint, role, and system-requirement disclosure. | No unrelated packages or global upgrades; the disclosure is required before installation. |

## Actions always requiring fresh approval

C1–C9 are acknowledged and are not covered by either grant above:

1. Any live external-provider call, with the required scope, cost, pricing, disclosure, storage, fingerprint, retry/fallback, budget, and offline-test evidence.
2. Manifest promotion, with versions/checksums, source checksum, full change/conflict/warning report, invalidation/cost impact, and rollback.
3. Superseding or repeating an ambiguously accepted provider request, with reconciliation and duplicate-cost evidence.
4. Broad regeneration, force execution, ambiguity-driven regeneration, excess automatic repair, or complete-tier/manifest reruns.
5. Credential rotation, revocation, authentication replacement, token invalidation, or deletion of the only credential file.
6. Destructive filesystem action affecting legacy artifacts, backups, raw responses, financial history, source, quarantine, state, retired modules, or Git history.
7. Remote Git actions, releases, deployment, non-local binding, public domains, or external beta access.
8. New providers or major infrastructure including PostgreSQL, Redis, Celery, queues, workflow engines, vector databases, public APIs, multi-user authentication, containers, or unapproved cloud infrastructure.
9. Significant deviation from JSON plus SQLite, controller ownership, single-worker operation, Streamlit, permanent module identity, append-only finance, fail-closed ambiguous calls, or the seven-release roadmap.

## Explicitly prohibited actions

The following remain prohibited:

- Live provider calls without scope-specific canary approval.
- Exposing, printing, logging, committing, or externally uploading secrets or repository content without approval.
- Silent authoritative-manifest overwrite or ambiguous curriculum merge.
- Automatic resubmission of ambiguously accepted provider requests.
- Silent reset/recreation of corrupt or missing financial state/history.
- Normal-cache deletion of raw paid responses or modification of immutable financial events.
- Title-only or truncated artifact identities.
- Paid work during dashboard rendering.
- Multiple uncontrolled workers or stale-worker commits.
- Treating module count alone as manifest authority.
- Automatic regeneration of ambiguous collision artifacts.
- Treating synthetic fallback as a retrieved source.
- Bypassing controller, budget, ledger, or preview controls.
- Remote push, public deployment, or major unapproved infrastructure.
- External-service calls from automated tests.

## Additional restrictions in the recommended grant

- No live external provider calls during Releases 0–1 unless separately approved as a limited canary after all Release 1 acceptance tests pass.
- Do not promote either manifest without a separate promotion report and approval.
- Do not delete legacy files, artifacts, raw responses, backups, or financial history.
- Do not push to remote Git or deploy publicly.
- Do not display secret values.
- Keep Release 1 unified.

## User-provided authorization text

> I authorize the recommended grant exactly as written.

The incorporated authorization statement is:

> I authorize Codex to begin Release 0 and continue implementation under the permissions selected above. This authorization does not permit live provider calls, manifest promotion, destructive deletion, remote Git actions, public deployment, ambiguous-call resubmission, or other actions listed under C without fresh approval.
