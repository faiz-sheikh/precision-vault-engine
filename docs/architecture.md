# Architecture and Release Classification

The controller is the sole operational writer. SQLite/WAL stores runs, idempotent commands, jobs, fencing leases, append-only provider events, reservations, sources, candidates, artifacts, dependencies, approvals, failures, remediation, and reconciliation records. Large raw/parsed/published outputs live in confined filesystem paths keyed by permanent module identity and operation/artifact identity.

Legacy component classification:

| Component | Classification |
|---|---|
| curriculum mapper, ingestion researcher | MIGRATE behind candidate/source controller APIs |
| synthesis, visual, audio, assessment, viva | LEGACY / compatibility; paid main entry points blocked |
| compiler, renderer, Windows bridge | KEEP + HARDEN as local workers |
| manifest repair | RETIRE as direct authority writer; retain as regression/migration evidence |
| JSONL serializer | KEEP as offline diagnostic only |
| dedup engine | MIGRATE to content-addressed source processing |
| Anthropic paths/tests | LEGACY COMPATIBILITY; no default route or credential |

Runtime curriculum prerequisites remain manifest data. Artifact dependencies remain operational data. These graphs are intentionally separate.
