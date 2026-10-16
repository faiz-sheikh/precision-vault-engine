# Precision Vault Engine (PVE)

A local control layer that sits between an application and external LLM providers. Its goal is **API-bleed protection**: no paid provider call should happen unless it is identified, authorized, budgeted, and recoverable. PVE is used by a study-material generation pipeline whose full study-pack workflow produces grounded research, detailed notes, a revision sheet, a quiz, viva practice, and flashcards.

> **Status: work in progress.** The controller path (`pve/`) is tested offline with mocked transports. Live provider execution is not demonstrated in this repository. See [Known limitations](#known-limitations) before relying on any guarantee below.

## How a call flows

```
App / user
   ↓
Compute deterministic operation fingerprint (provider, model, request, prompt, params, schema, config, upstream hashes)
   ↓
Check approval  →  check budget and reserve it  →  check retry / concurrency state
   ↓
Anything uncertain or ambiguous?  →  STOP (no automatic resubmission)
   ↓
Call provider (explicit global + per-provider switches; no silent fallback)
   ↓
Write raw response atomically + SHA-256
   ↓
Validate output  →  store durable result
   ↓
Same operation again?  →  reuse the validated checkpoint, make no provider call
```

## What works today

| Capability | Status | Notes |
|---|---|---|
| Deterministic operation fingerprints (SHA-256) | Working | `pve/identity.py`; `tests/test_identity_regressions.py::IdentityRegressionTests::test_fingerprint_is_order_stable` |
| Budget check and reservation before transport | Working (controller path) | `pve/controller.py`; `tests/test_crash_corruption_security.py::CrashCorruptionSecurityTests::test_unknown_pricing_blocked_under_cost_cap` |
| Retry classification, bounded automatic retries | Working (controller path) | `pve/providers.py`, `pve/controller.py`; `tests/test_crash_corruption_security.py::CrashCorruptionSecurityTests::test_transient_retries_are_bounded_across_restarts` |
| Raw response checkpoint + hash verification | Working (controller path) | `pve/artifacts.py`, `pve/controller.py`; `tests/test_safety_critical_branches.py::SafetyCriticalBranchTests::test_raw_checkpoint_checksum_mismatch_fails_closed` |
| Idempotent reuse of completed operations | Working in tested paths | `pve/pipeline.py`; `tests/test_full_pipeline.py::FullPipelineTests::test_all_phases_and_three_run_zero_call_proof` shows 7 → 0 → 0 mocked provider calls |
| Append-only provider events and reconciliation | Working | `pve/store.py`; `tests/test_store_controller.py::StoreControllerTests::test_events_are_append_only` and `tests/test_release1_release3.py::ReleaseOneThreeTests::test_startup_reconciliation_reports_ambiguous_without_mutation` |
| Immutable one-shot approvals | Partial | `pve/controller.py`; `tests/test_manual_provider_retry.py::ManualProviderRetryTests::test_approval_and_consumption_are_immutable`, `tests/test_benchmark_execution_planner.py::BenchmarkExecutionPlannerTests::test_approval_exact_binding_duplicate_expiry_drift_and_one_shot`, and `tests/test_reconciliation_supersession.py::ReconciliationSupersessionTests::test_concurrent_child_execution_consumes_one_shot_once`; general manifest/source approvals are not one-shot |
| Provider isolation, no silent fallback | Partial | `pve/providers.py`; `tests/test_live_transport_offline.py::ConcreteProviderOfflineTests::test_every_live_adapter_kill_switch_prevents_urlopen`; legacy scripts can still build clients directly |
| Job leases and fencing tokens | Partial | `pve/worker.py`, `pve/store.py`; `tests/test_store_controller.py::StoreControllerTests::test_fencing_rejects_stale_worker`; no lease renewal is implemented |
| Structured output validation | Partial | `pve/providers.py`; `tests/test_structured_provider_validation.py::StructuredProviderValidationTests::test_schema_invalid_inner_json_is_rejected`; the pipeline does not yet enforce per-phase schemas |
| Fail-closed on corrupt state | Partial | `pve/state.py`, `pve/store.py`; `tests/test_crash_corruption_security.py::CrashCorruptionSecurityTests::test_database_corruption_fails_closed` and `tests/test_safety_critical_branches.py::SafetyCriticalBranchTests::test_state_missing_default_and_non_object_corruption` |

## Evidence

- Offline test run: 157 passed and 1 skipped; the skipped test requires preserved local provider checkpoints. Live-provider switches were disabled for the run.
- Adapters exist for Google (Gemini), an NVIDIA-compatible endpoint and OpenAI; Anthropic exists only as a legacy compatibility adapter.
- State is stored in SQLite (WAL, schema version 4) with integrity checks and backup/restore verification.

## Quick start

```bash
# install the package in editable mode
python -m pip install -e .

# inspect local health and configured providers
pve doctor
pve providers

# preview a run against a known manifest checksum
pve preview --manifest-checksum <sha256>

# launch the dashboard, or create the bundled study pack offline
streamlit run pve/dashboard.py
pve demo

# run the test suite with live providers disabled
PYTHONPATH=. PVE_ALLOW_LIVE_PROVIDERS=0 pytest -q
```

## Repository layout

- `pve/` hardened package: identity, providers, safety, store, artifacts, controller, worker, manifest, pipeline, quality, dashboard, cli
- `tests/` 24 test modules
- `docs/` design and deployment notes
- Legacy scripts at the repository root (curriculum mapping, synthesis, assessment, audio, etc.) predate the controller and are being migrated

## Curriculum source documents

The university's programme scheme and syllabus files are not redistributed in this repository. Download them from the Ramdeobaba University website (B.Tech Robotics & AI programme scheme and syllabus) and place them in `config/curriculum/` when they are needed locally.

## Known limitations

These are open issues found in a code audit, listed so the project is not oversold:

1. Legacy Claude batch submission is not crash-safe: a crash after the provider accepts a batch but before the local ID is saved can cause a duplicate submission.
2. Actual provider cost is kept in event payloads but not written to accounting columns, so the cost command reports an estimate.
3. `max_regeneration_attempts` is configured but not enforced.
4. General manifest/source approvals are reusable (not consumed or expiring).
5. Artifact reuse trusts the database and does not re-verify the file's existence or hash.
6. Worker leases have no heartbeat; duplicate execution during a long provider call is not tested.
7. Manifest promotion writes a file and updates SQLite in separate steps, with no crash recovery.
8. Legacy scripts still make direct provider calls outside the controller ledger.
9. Registry files under `config/` were generated on the author's machine and contain local absolute paths.

## Roadmap

Move the legacy scripts behind the controller, persist actual cost and usage, consume approvals, verify artifact hashes on reuse, and add lease renewal, in that order.

## Development history

The rewritten history contains 20+ commits, first commit 2026-08-21. This repository was published on 2026-10-16 with the existing history preserved.

## How this was built

Designed and specified by me; implemented with AI coding assistants (Codex and Claude); I review the code, run the tests and direct the roadmap.
