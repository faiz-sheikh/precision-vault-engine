# PVE Startup MVP Engineering Report

## Maturity and baseline

The original `4132b54` repository was a functional prototype, not a safe unattended system. It contained two divergent legacy manifests (142 and 274 modules), direct provider SDK use, acceptance-before-state persistence for Anthropic batches, existence-based completion, collision-prone artifact paths, silent corrupt-state reset, broad retries/fallbacks, and four import-time dependency failures. The hardened skeleton checkpoint is `e9fcbfe`.

Both manifests remain immutable historical evidence:

- `master_manifest.json`: `69869db09a3076b71161f6a220ea5b48a62418321fdcd2941b1a0bfe82e47fa8`
- `config/master_manifest.json`: `181e56c469b43b1962ee23ca5ba950b179298190a79a02d9ec5d0412e640ea81`

No authority decision or promotion was made.

## Historical regression disposition

| # | Issue | Disposition |
|---:|---|---|
| 1–2 | truncated IDs; `&`/`and` duplicate | FIXED with full SHA-256 identity and regression tests |
| 3–6 | Biology/CS Minor/domain taxonomy | FIXED in deterministic legacy mapping; regression covered |
| 7–11 | administrative noise, queries, subject/chapter/visual drift | FIXED or CONTAINED by deterministic mapping and governed target schema |
| 12–13 | greedy PYQ parsing; path traversal | FIXED/covered; shared artifact confinement added |
| 14–17 | weak cache, provider config/limits, truncated completion | FIXED in controller fingerprints/checkpoints; legacy execution contained |
| 18–23 | ordering, artifact collision, corrupt state, zombies, unsafe dedup, tier routing | FIXED or CONTAINED through DAG, permanent IDs, fail-closed state, capability routing |
| 24–27 | lost response, duplicate batch, polling, fallback | FIXED in controller path; legacy Anthropic classified compatibility-only and blocked |

## Implemented architecture

- SQLite/WAL authority with migrations, foreign keys, busy timeout, permissions, integrity checks, verified backup/restore, append-only provider events, commands, jobs, leases/fences, reservations, sources, candidates, approvals, artifacts, failures, remediation, outbox, and reconciliation records.
- Provider-neutral capability routes for Gemini, Nemotron Super/Ultra, and OpenAI; Anthropic is legacy compatibility only. Concrete HTTP transports are live-disabled by two exact switches and tested without network.
- Canonical request/config/upstream SHA-256 fingerprints, durable intent before calls, ambiguous acceptance blocking, raw checkpoint before parsing, bounded transient attempts, conservative unknown pricing, and command idempotency.
- Governed source and candidate-manifest flow with permanent opaque module IDs, semantic duplicate detection, checksum validation, and approval/input-hash-bound atomic promotion support. Promotion was not executed.
- Content-addressed artifacts, dependency fingerprints, selective invalidation, serial controller DAG, singleton worker leases/fencing, prompt versions, deterministic quality validation, grounding maps, technical-risk routing, and remediation recommendations.
- Controller-backed CLI, read-only dashboard projections covering all required areas, demo manifest, package metadata, operator/recovery/deployment guides, UAT, export, and live-canary plan.

## Verification summary

Offline tests cover kill switches, adapter construction, retries, ambiguity, crash boundaries, parser recovery, SQLite corruption, backup/restore, concurrency, fencing, command idempotency, path/symlink escapes, manifest governance, prompt/quality/grounding, CLI, dashboard refresh, full 12-phase mocked E2E, selective invalidation, and stress/property workloads. The unchanged E2E call sequence is `7 → 0 → 0`. No live external call was made.

Residual risks: live provider request/response compatibility and current pricing require controlled canaries; Streamlit itself was not installed in the core-only clean-install environment; legacy artifacts remain collision-prone until explicitly migrated; the two historical manifests remain unresolved pending source authority review; distributed operation is intentionally unsupported.
