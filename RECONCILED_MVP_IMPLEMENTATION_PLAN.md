# Precision Vault Engine — Reconciled Startup MVP Architecture and Implementation Plan

**Plan date:** 2026-07-29  
**Target:** Credible single-user startup MVP with safe full end-to-end execution  
**Status:** Final reconciled architecture and implementation plan — reviewed, no implementation authorized

## Binding final architecture decisions

This section reconciles the independent audit, the supplied historical review, and the final concern summary. These are release gates, not optional refinements. If a later section appears less strict, these decisions take precedence.

### Identity, governance, and authority

1. **Permanent module identity is immutable and independent of mutable metadata.** A module receives an opaque stable `module_id` at promotion. Titles, subjects, domains, tracks, semesters, source filenames, and source labels may change without changing that identity. Candidate matching may propose continuity, but an ambiguous match requires review. Content-derived hashes identify revisions; they do not replace the permanent identity.
2. **The 142-module manifest is not accepted because it contains 142 entries.** Release 0 must prove its authority from the approved source registry, effective dates, supersession decisions, provenance coverage, validation results, and candidate-to-authoritative promotion record. A count match is only a diagnostic.
3. **Source-authority decisions are reviewable governance records.** Selection, rejection, supersession, equivalence, and conflict resolution require actor, reason, timestamp, evidence, and source hashes. They are visible in CLI and dashboard history and are not silently overwritten.
4. **Artifact collisions that cannot be mapped unambiguously are quarantined.** They are never guessed, silently selected, or marked current. Recovery requires provenance or content evidence; otherwise regeneration is explicitly previewed and approved.

### Financial transaction boundary

5. **Only the controller may authorize state changes, and only controller-authorized workers may invoke provider adapters.** Dashboard pages, import-time code, artifact readers, validators, and phase helpers cannot call providers directly. Architecture tests enforce the dependency boundary.
6. **Every external attempt is represented by an append-only financial event.** Corrections create new events; they never edit or delete historical attempts, provider identifiers, token usage, cost, or reconciliation decisions. Mutable projections may exist for queries, but they must be rebuildable from immutable events.
7. **Ambiguous provider acceptance always fails closed.** If acceptance may have occurred but no provider ID was durably captured, the request enters `ambiguous_acceptance`. It cannot be resubmitted automatically. Reconciliation or an explicit, audited human resolution is required.
8. **Quality-driven regeneration is hard-limited.** Each artifact policy defines a maximum paid regeneration count and cost ceiling. Deterministic validation runs first. A quality judge cannot authorize regeneration or publication by itself; the controller applies policy and escalates exhausted or uncertain cases to manual review.
9. **Run configuration is immutable and versioned.** A run snapshots the canonical configuration hash, prompt versions and hashes, models, parameters, schema/phase versions, source and manifest checksums, pricing version, and feature flags. Changing settings creates a new run or invalidates the preview; it never mutates an active run's meaning.

### SQLite, filesystem, and worker correctness

10. **SQLite protection begins in Release 1, when SQLite first becomes operationally authoritative.** Before any paid canary it must have migrations, integrity checks, WAL/transaction configuration, restrictive permissions, bounded backups, restore verification, corruption fail-closed behavior, and documented recovery. Release 0 inventories legacy state and creates protected backups without prematurely making an unvalidated database authoritative.
11. **JSON, SQLite, and artifact files are reconciled explicitly because they cannot share one atomic transaction.** The controller uses staged writes plus checksums and a durable outbox/projection record: write and fsync the artifact or candidate file, commit its intended metadata/event, atomically publish the file, then finalize the projection. Startup reconciliation detects every interrupted boundary. Authoritative JSON is never inferred from a partially committed SQLite projection, or vice versa.
12. **The singleton worker uses transactional leases plus fencing tokens.** Lease acquisition and renewal are atomic. Every state-changing commit includes the current fencing token; an expired or superseded worker cannot commit even if it resumes. Provider jobs found during takeover enter reconciliation before any retry.
13. **SQLite backups are consistent, validated, and restorable.** Use SQLite's backup mechanism or a transactionally consistent snapshot—not a raw copy of a live WAL database. Each backup records checksum, schema version, creation result, and restore-test status. Financial history and active provider records receive the strictest retention.

### UI and execution safety

14. **A preview is cryptographically bound to execution.** Confirmation records the scope, manifest checksum, configuration hash, dependency fingerprints, pricing snapshot, estimated call range, budget, and an expiring preview token. Execution refuses the command if any bound input has drifted. This closes the preview-to-execution time-of-check/time-of-use gap.
15. **Streamlit refresh, rerun, back navigation, and double-click are read-only with respect to paid work.** UI events submit an idempotent command once to the controller; page rendering only reads projections. Database uniqueness constraints and controller idempotency—not session-state alone—prevent duplicate jobs.
16. **Provider health displayed on page load is derived from recent ledger activity and local configuration.** A live provider canary is a separately scoped, priced, confirmed command.

### Additional independent-review safeguards

17. **Pricing is versioned evidence.** Previews and ledger events store currency, unit prices, pricing-source/version, and timestamp. Unknown or stale pricing is conservative and may block execution under safe mode; historical estimates are never silently recomputed with new prices.
18. **Source documents are untrusted data.** Provider prompts delimit source content from instructions, restrict arbitrary file inclusion, validate outbound queries, and redact secrets. Source ingestion records sensitivity/provider-disclosure policy so prompt injection or embedded instructions cannot expand tool or call scope.
19. **Disk capacity and retention are financial controls.** Before a paid request, preflight storage for the raw response and safety margin. If durable checkpointing is not possible, do not call. Raw paid responses, active batch metadata, manifests, and financial events cannot be garbage-collected by ordinary cache cleanup; retention and deletion are explicit audited operations.
20. **Manual approvals and overrides are dependency-bearing records.** Promotion, source authority, collision recovery, force execution, quality approval, and ambiguous-call resolution record actor, reason, evidence, and input hashes. A changed dependency makes the approval stale.
21. **A safe usable pipeline precedes product polish.** Releases 0–5 must deliver a controller/CLI path that is financially safe, resumable, and fully testable. Dashboard work consumes the same command/query interfaces and cannot delay the limited canary or safe end-to-end pipeline milestones.

### Final non-negotiable proof obligations

Before a limited provider canary, tests must demonstrate:

- zero provider calls from invalid manifests, corrupt financial state, UI refreshes, duplicate command submission, and stale/changed previews;
- no automatic resubmission after ambiguous acceptance or worker takeover;
- rejection of commits from stale fencing tokens;
- recovery at every JSON–SQLite–artifact commit boundary;
- durable raw-response recovery after every post-provider local failure;
- permanent errors receive zero retries and quality regeneration cannot exceed its hard call/cost cap;
- every paid attempt is present in immutable history and every current projection can be reconciled from it.
**Primary invariant:** An unchanged end-to-end rerun performs zero unnecessary model calls.

---

# Section 1 — Reconciliation summary

## 1.1 Consolidated conclusion

The independent audit and the later historical/architecture review substantially agree. They describe five shared root causes:

1. **Financial transactions are not modeled as durable transactions.**
2. **Identity is inconsistent across manifests, modules, requests, and artifacts.**
3. **Source authority and manifest promotion are missing.**
4. **Completion is inferred from existence instead of dependency fingerprints and validated state.**
5. **Phase scripts own overlapping state independently rather than operating through one controller.**

The startup-MVP scope is therefore not “add a dashboard to the scripts.” The correct scope is:

```text
financial/identity hardening
    → manifest and source governance
    → dependency-aware artifacts
    → quality and grounding gates
    → durable controller
    → safe dashboard
```

## 1.2 Finding reconciliation

| Finding | Independent audit | Historical/additional review | Final classification | Reconciled decision |
|---|---|---|---|---|
| Claude acceptance-before-state crash window | Independently confirmed | Repeated as P0 | Confirmed and unresolved | Durable intent, `submitting/ambiguous` state, no automatic ambiguous resubmit, provider reconciliation |
| Manual duplicate batch submission | Independently confirmed | Repeated | Confirmed and unresolved | Unique in-flight reservation over request fingerprint/module set |
| Title/truncated artifact collisions | Quantified: 8 groups/16 note-audio modules; 31 groups/103 assessment-viva modules | Repeated | Confirmed and unresolved | Full stable identity in every path plus metadata verification |
| Two master manifests | Quantified 274 vs 142 | 142 expected authoritative | Confirmed; target selection conditional | Release 0 verifies and promotes one 142-module candidate |
| Existence-based completion | Confirmed across phases | Repeated | Confirmed and unresolved | Artifact/dependency registry |
| No source authority | Confirmed overlapping curricula | Repeated | Confirmed and unresolved | Source discovery/reconciliation before mapping |
| Hard ID truncation | Current mapper has hash suffix | Historical original bug | Fixed incompletely; superseded by broader identity problem | Keep regression test; replace identity scheme and remove all downstream truncation |
| Semantic `&`/`and` duplicates | Normalizer exists; first-entry merge remains | Historical bug | Fixed incompletely | Auto-merge exact-safe equivalence only; uncertain matches become conflicts |
| Biology → AI/ML | Current mapper corrections and domains exist | Historical bug | Core code fixed, migration incomplete | Regression test; validate promoted manifest |
| CS minor → Defence | Context-aware corrections exist but LLM classification remains influential | Historical bug | Fixed incompletely | Deterministic classifier/teachable gate plus regression test |
| Missing Natural Sciences/Mechanical domains | Present in mapper/config/current manifests | Historical bug | Confirmed fixed in core; not yet guaranteed everywhere | Cross-phase enum test and dashboard coverage |
| Administrative text as modules | Current prompt filters it, supplied manifest still includes evaluation/administrative-like entries | Historical bug | Confirmed and unresolved | `module_type`, `teachable`, explicit rejection/defer report |
| Weak web queries | Deterministic anchored queries added | Historical bug | Fixed incompletely | Structured query builder plus validation before grounding |
| Subject drift | Current mapper separates `track`, configured manifest predates it | Historical bug | Fixed incompletely | Versioned schema and migration |
| `suggested_chapters` ambiguity | Audit found educational/media/API meanings mixed | Historical bug | Confirmed and unresolved | Split into four explicit planning fields |
| Visual-engine misassignment | Current deterministic module resolver improves cases | Historical bug | Fixed incompletely | Per-visual routing; module field only a default |
| Greedy PYQ extraction | Lazy matching is present | Historical bug | Confirmed fixed correctly | Permanent one-line multi-question regression test |
| Path traversal | `_safe_path` now enforces containment and null-byte rejection | Historical bug | Fixed correctly for enrichment, incomplete globally | Shared authorized-root resolver; symlink/null/absolute-path tests |
| No per-file mapper cache | Hash cache exists but supplied state absent and key incomplete | Historical bug | Fixed incompletely | Versioned source-processing fingerprint |
| Missing Gemini thinking config | Most calls set budget zero/tool mode; handwriting call does not | Historical bug | Fixed incompletely | Provider adapter enforces per-operation policy; config-shape tests |
| Wrong Claude output ceiling | Active constant changed; stale comments/docs and cost ceiling remain | Historical bug | Fixed incompletely | Capability registry, cost-aware limits, no stale constants/comments |
| Manifest repair | Exists as one-time direct mutator | Later governance design supersedes it | Obsolete as runtime tool | Convert to explicit migration/import utility; never run against authority directly |
| “Gemini free” cost assumptions | Guide claims unconditional behavior | Audit found account-dependent | False as a guaranteed invariant | Ledger/pricing config treats all external calls as financial/quota transactions |
| Need PostgreSQL/distributed queue | Neither audit found a requirement | Explicitly discouraged | False/overengineered for MVP | SQLite plus one local worker |

## 1.3 Final accepted P0 findings

- Paid call intent, attempts, provider IDs, raw results, and ambiguity are not durably modeled.
- The current Claude Batch flow can duplicate paid work after crashes or repeated commands.
- Missing/corrupt state can be treated as empty and trigger broad regeneration.
- Active manifest selection is ambiguous.
- Artifact identifiers collide.
- Invalid/unapproved manifest entries can reach paid phases.
- Broad force and automatic fallback lack a financial approval boundary.
- Successful paid results can be lost after local failures.
- Permanent provider errors are inconsistently retried.
- No test proves an unchanged rerun makes zero calls.

---

# Section 2 — Final target architecture

## 2.1 Component diagram

```text
┌──────────────────────────────── Dashboard (Streamlit) ────────────────────────────────┐
│ Read-only views                     Confirmed commands                                │
│ overview/manifests/sources/...      scope + estimate + budget + reason               │
└───────────────────────┬──────────────────────────────┬─────────────────────────────────┘
                        │ queries                      │ idempotent command
                        v                              v
┌──────────────────────────── Pipeline Controller / Application Service ────────────────┐
│ planner │ validator │ state machine │ command dedup │ policy gates │ recovery         │
└───────┬────────────┬────────────┬───────────────┬──────────────┬───────────────────────┘
        │            │            │               │              │
        v            v            v               v              v
 Manifest repo   Source repo   Job/API store   Artifact repo   Provider adapters
 versioned JSON  governed JSON SQLite/WAL      files+metadata  Gemini / Claude
        │            │            │               │              │
        └────────────┴────────────┴───────────────┴──────────────┘
                                      │
                                      v
                       Single local process-safe worker
                                      │
                                      v
        source → candidate → promotion → research → synthesis → Q/G gates
                                              → approved dependent artifacts
```

## 2.2 Design rules

- The controller is the only supported writer to authoritative manifest, operational state, API ledger, and artifact metadata.
- Existing phase scripts become thin CLI adapters to controller commands.
- Dashboard page rendering is always read-only.
- Streamlit session state is never authoritative.
- One local worker claims jobs transactionally; no Redis/Celery is introduced.
- Top-level phases remain serial by dependencies.
- Provider adapters centralize request construction, errors, retry, raw checkpoints, usage, pricing, and mocks.
- Large artifacts stay in the filesystem.
- The promoted JSON manifest remains human-reviewable, versioned, checksummed, and portable.
- SQLite is local-disk only; WAL is not placed on a network filesystem.

## 2.3 Data flow

```text
discovered source
  → immutable hash and metadata
  → duplicate/version/authority group
  → approved source set
  → cached extraction
  → candidate manifest
  → schema + semantic + identity + cost validation
  → diff/conflict report
  → promotion command
  → authoritative manifest version
  → research pack checkpoints
  → synthesis raw checkpoint
  → parsed draft
  → quality result + grounding result
  → approved published note
  → visual/audio/assessment/viva/compile/video
```

## 2.4 State flow

Every command creates a durable `run` and one or more `jobs`. A job is prepared before work begins. External calls create ledger rows before network I/O. Published artifact metadata is projected into the manifest only after validation.

```text
command accepted
  → planned
  → budget reserved
  → queued
  → worker claim
  → prepared/check cache
  → local work or external transaction
  → raw checkpoint
  → parse/validate
  → artifact publish
  → dependency projection
  → complete/review/failed
```

## 2.5 API-call flow

```text
canonical request inputs
  → SHA-256 request fingerprint
  → ledger lookup
      ├─ succeeded + raw result valid → cache hit, no call
      ├─ submitted/running → attach/recover, no call
      ├─ calling with expired process lease → ambiguous, block
      ├─ permanent failure unchanged → fail, no retry
      └─ absent/retryable under budget → reserve budget + durable intent
  → mark calling/submitting and commit
  → provider call
  → immediately checkpoint response/provider ID
  → record usage/cost
  → downstream local processing
```

## 2.6 Dashboard flow

```text
page refresh → SELECT queries only
user fills command form
  → preview endpoint/method
  → exact scope + call range + cost range + invalid blockers
  → explicit confirmation with generated command ID
  → controller inserts command once
  → worker executes
dashboard polls durable job/events
```

## 2.7 Manifest flow

```text
authoritative vN (read-only file)
       + approved source set
       ↓
candidate vN+1
       ↓ validation
diff: add/change/remove/retire/conflict
       ↓ explicit promotion
backup vN + atomic publish vN+1 + checksum
       ↓ artifact staleness reconciliation
```

## 2.8 Artifact flow

Artifact layout:

```text
artifacts/<module_id>/<phase>/<artifact_id>/
    metadata.json
    raw/
    parsed/
    published/
```

Human-readable title slugs may appear after a digest but never determine uniqueness:

```text
<module_id>__<title_slug>__<artifact_digest>.md
```

## 2.9 Failure and recovery flow

```text
failure
  → classify local/provider and transient/permanent/ambiguous
  → preserve last checkpoint
  → update failure record and recommended action
  → release or retain budget reservation
  → retry only if policy permits

restart
  → recover claimed jobs with expired leases
  → calling/submitting external rows become ambiguous, never automatically repeated
  → submitted provider jobs resume polling/retrieval
  → raw-received jobs resume local parse/validation
  → local-render failures resume from upstream checkpoints
```

---

# Section 3 — Storage decision

## 3.1 Options

| Option | Strengths | Weaknesses | Verdict |
|---|---|---|---|
| JSON-only | Minimal dependencies; human-readable; easy backup | Poor querying/event history; awkward locks/transactions; whole-file contention; difficult dashboard/job/API-ledger semantics | Insufficient for credible dashboard/recovery MVP |
| JSON + SQLite | Keeps portable versioned manifest; ACID jobs/ledger/events/budgets/locks; easy dashboard queries; one-file backup | Requires migrations and cross-store projection/reconciliation | **Recommended** |
| SQLite-only | Simplest transactional truth | Loses reviewable/promotable manifest artifact; weak Git/diff/export workflow | Reject for this product target |
| PostgreSQL | Strong concurrency/multi-user future | Operational overhead without current need | Defer |

SQLite serializes writers and supports concurrent consistent readers; that matches one controller/worker plus dashboard readers. WAL must remain on the same local machine/filesystem.

## 3.2 Recommended ownership

### Authoritative versioned JSON manifest

Contains stable or published truth:

- schema and manifest versions;
- checksum and promotion metadata;
- module identity/type/teachable state;
- curriculum/domain/subject/track/course fields;
- source provenance and authority references;
- prerequisite identities;
- research/prompt/model policy references;
- published quality/grounding summaries;
- current published artifact references and dependency fingerprints;
- stable status such as active/retired/manual-review/approved.

It does **not** contain every polling attempt, log line, or transient worker lease.

### SQLite operational store

Contains:

- runs, jobs, commands, idempotency keys, leases, pause/cancel requests;
- API transactions and attempts;
- provider request/batch IDs;
- budget reservations and realized cost;
- events and metrics;
- failures and recovery actions;
- raw checkpoint registry;
- artifact generations and stale graph;
- candidate validation/diff/promotion records;
- live phase state.

### Source registry

Canonical JSON export plus SQLite query projection:

- immutable source ID/hash;
- path and media type;
- extracted-content hash;
- authority/effective year/status/supersession;
- source groups/conflicts;
- extraction/mapper fingerprints.

### Filesystem artifacts

- immutable raw provider responses;
- extracted source text;
- research components/packs;
- draft/published notes;
- quality/grounding reports;
- media and assessments;
- logs exported per run.

### Logs

- Structured events in SQLite for UI/querying.
- Rotating redacted text/JSON logs for diagnosis.
- Never authoritative for financial state.

## 3.3 Cross-store consistency

SQLite records a pending manifest projection before atomic JSON publication. On restart, the controller compares the expected checksum with the file:

- checksum matches: mark projection complete;
- old checksum remains: replay atomic publish;
- unexpected checksum: block and require recovery.

---

# Section 4 — Final issue matrix

| ID | Issue | Root cause | Severity | Financial risk | Data risk | User impact | Files | Required fix | Release |
|---|---|---|---|---|---|---|---|---|---|
| FIN-01 | Duplicate Claude batch after crash | Provider acceptance before durable ID | Critical | Full duplicate batch | Conflicting results | Unexpected spend | synthesis | Intent, ambiguity, reconciliation | R1 |
| FIN-02 | Duplicate manual/in-flight submit | No module-set reservation | Critical | Full duplicate batch | Duplicate outputs | Unsafe controls | synthesis/controller | Unique reservation | R1 |
| FIN-03 | State corruption triggers regeneration | Fail-open state loaders | Critical | Full-phase repeats | Lost history | Unsafe recovery | synthesis/render/mapper | Fail closed/backups | R1 |
| FIN-04 | Retry permanent errors | Scattered ad hoc handlers | Very High | Multiplied useless calls | None | Long failures | all provider phases | Adapter error taxonomy | R1 |
| FIN-05 | Nested research/assessment amplification | Nested logical and transport retries | Very High | Up to dozens/module | Partial packs | Unpredictable runs | ingestion/assessment/viva | Central retry budget/checkpoints | R1/R3 |
| FIN-06 | Automatic paid fallback | No fallback approval policy | Very High | Second-provider/extra calls | Synthetic data treated as source | Hidden behavior | ingestion/viva | Preview + explicit policy | R1 |
| FIN-07 | Paid result lost locally | Raw response not checkpointed | Very High | Repeat generation | Lost evidence | Fragile resume | research/synthesis/audio/assessment/viva | Raw-first checkpoint | R1/R3 |
| FIN-08 | Force is broad/unbudgeted | Boolean flags bypass cache | Very High | Full regeneration | Multiple generations | Accidental spend | mapper/research/visual | Scoped force command/reason | R1 |
| FIN-09 | No cost/call preview | Scripts execute directly | Very High | Unbounded user surprise | None | Low trust | controller/dashboard | Planner/budget guard | R1/R5/R6 |
| ID-01 | Two master manifests | Ambiguous SSOT paths/versions | Critical | Different workload | Curriculum divergence | Wrong product | config/mapper/docs | Freeze/promote one manifest | R0/R2 |
| ID-02 | Note/audio path collision | Title-based paths | Critical | Repeat/incorrect calls | Silent overwrite | Wrong modules | synthesis/audio | Stable-ID paths | R1 |
| ID-03 | Assessment/viva collision | `module_id[:30]` | Critical | Incorrect cache decisions | Cross-module reuse | Wrong content | assessment/viva | Full ID/digest | R1 |
| ID-04 | ID changes with title/domain | Mutable metadata in ID | High | Orphaned cache repeats | Broken references | Migration pain | mapper/repair | Stable identity + aliases | R2 |
| MAN-01 | Silent first-entry semantic merge | Dedup used as conflict resolution | Critical | Wrong modules proceed | Legitimate deletion | Missing curriculum | mapper/repair | Safe equivalence/conflicts | R2 |
| MAN-02 | Stale source entries carried | No source ownership/retirement | Very High | Stale paid outputs | Zombie modules | Wrong syllabus | mapper | Source-owned lifecycle | R2 |
| MAN-03 | No source authority/versioning | Equal scan of overlapping files | Very High | Duplicate parsing/generation | Version mixing | Wrong curriculum | mapper/new source registry | Governance stage | R2 |
| MAN-04 | Administrative items reach generation | No formal module type/gate | Very High | Paid invalid outputs | Manifest pollution | Low-quality product | mapper/all phases | `module_type`/`teachable` | R2 |
| MAN-05 | Subject/track drift | Overloaded fields | High | Cache/path fragmentation | Inconsistent schema | Bad filters | mapper/manifest | Separate fields | R2 |
| MAN-06 | Grade/tier trusted from LLM | Incomplete deterministic validation | Very High | School item can route to Claude | Misplacement | Wrong level | mapper | Normalization/rules/review | R2 |
| MAN-07 | Domain regressions | Incomplete historic taxonomy | High | Wrong routing/cost | Misclassification | Wrong content | config/mapper/all | Central enums/regressions | R2 |
| CACHE-01 | Existence means completion | No dependency graph | Very High | Stale or broad force | Stale artifacts | Inconsistent product | all phases | Fingerprints/registry | R3 |
| CACHE-02 | Mapper cache incomplete | Missing prompt/model/extractor/schema keys | Very High | Reparse or stale parse | Stale manifest | Slow/costly | mapper | Full processing fingerprint | R2/R3 |
| CACHE-03 | Embedding cache keyed by path | No content/model key | High | Repeat/stale embeddings | Wrong dedup | Wrong source set | dedup | Content-aware key | R3 |
| CACHE-04 | Note mutation does not invalidate dependents | No graph | Very High | Incorrect cache hit | Stale media/assessment | Mismatched outputs | visual/artifacts | Dependency DAG | R3 |
| QUAL-01 | No quality judge | Generation treated as publication | Very High | Bad downstream spend | Low-quality notes | Uncredible product | new quality/controller | Separate judge | R4 |
| QUAL-02 | No grounding verifier | References not claim support | Very High | Bad downstream spend | Hallucinations | Trust failure | new grounding/citations | Claim/formula verification | R4 |
| QUAL-03 | Truncated/partial output marked complete | Marker without blocking state | Very High | Dependent calls waste | Incomplete artifacts | Wrong learning content | synthesis/assessment | Validation gate | R1/R4 |
| QUAL-04 | Weak queries/fallback provenance | Unvalidated query/synthetic fallback | High | Wasteful grounding | Weak evidence | Weak notes | ingestion | Query validator/component types | R3/R4 |
| MODEL-01 | Gemini 2.5 lifecycle | Hard-coded retiring model | High | Retry storm after retirement | Pipeline stop | Outage | config/adapters | Capability registry/preflight | R1 |
| MODEL-02 | Claude ceiling/comment drift | Scattered assumptions | Medium | Oversized cost/unneeded splits | Confusing behavior | Misleading preview | config/synthesis/docs | Capability-based limits | R1 |
| SEC-01 | Secret file exposure | Review copy permissions/files | Critical | Account compromise | Credential leak | Security incident | env/credentials/docs | Remove/rotate/permissions | R0 |
| SEC-02 | Pickle token load | Executable deserialization | Critical if writable/untrusted | Credential/account risk | Code execution | Host compromise | config | Safe token serialization | R1 |
| SEC-03 | Path safety fragmented | Per-file checks only | Critical for hostile inputs | Possible prompt data leak | Arbitrary read | Privacy/security | ingestion/bridge/shared | Authorized-root resolver | R1/R2 |
| REL-01 | No controller/state machine | Independent scripts mutate state | Very High | Duplicate calls | Inconsistent phases | Manual operation | all/controller | Central orchestrator | R5 |
| REL-02 | No selective failed/stale rerun | Coarse completion | High | Broad repeat calls | Recovery complexity | Manual inspection | all/controller | Job/artifact graph | R3/R5 |
| REL-03 | Full guide runs dependents before retrieval | Incorrect orchestration | High | Calls on missing notes | Bad artifacts | Failed end-to-end | docs/controller | Enforced gates | R5 |
| UI-01 | UI refresh/action duplication risk | Reactive page lifecycle | Critical if direct calls | Duplicate spend | Duplicate jobs | Unsafe dashboard | dashboard/controller | Durable command IDs | R6 |
| TEST-01 | No tests/pytest | No regression harness | Critical | No proof of invariant | Regressions | Unsafe releases | dependencies/tests | Full offline suite | R1 onward |

---

# Section 5 — File-change matrix

## 5.1 Existing files

| File | Existing/new | Current responsibility and defects | Exact changes / new responsibility | Dependencies | Tests | Release |
|---|---|---|---|---|---|---|
| `config.py` | Existing | Paths/models/taxonomy/OAuth; scattered policy; pickle; ambiguous manifest | Typed settings; one manifest pointer; model capability/pricing config; authorized roots; no unsafe state loading | schema, adapters | config, secrets, paths, model policy | R0/R1 |
| `curriculum_mapper.py` | Existing | Direct equal source scan, LLM mapping, silent carry/merge | Consume approved source set; cached extraction/mapping; candidate only; stable identity service; typed module output | source/manifest services | historic ID/domain/admin/cache tests | R2 |
| `ingestion_researcher.py` | Existing | Classroom ingestion + multi-stage research with nested retries/fallback | Split local extraction, research planning, component jobs; provider adapters; component checkpoints; structured citations | controller, adapters, artifacts | query, fallback, checkpoint, rerun | R1/R3/R4 |
| `synthesis_gate.py` | Existing | Direct Gemini/Claude calls and unsafe batch state | Thin phase service using ledger; crash-safe batch coordinator; raw-first results; no publication until gates | adapters, ledger, artifacts | batch crashes, truncation, parts | R1/R4 |
| `visual_blueprint.py` | Existing | Module-level Mermaid call/in-place note mutation | Structured visual-plan artifact; per-visual engine routing; no note mutation as cache marker | artifacts, prompts | routing, stale invalidation | R3/R4 |
| `audio_pipeline.py` | Existing | Paid script only in memory; MP3 existence cache | Script artifact checkpoint; line-level TTS state; deterministic merge; dependency fingerprint | artifacts/jobs | crash during TTS/FFmpeg | R3 |
| `domain_compiler.py` | Existing | Local compilation with no fingerprints | Deterministic compiled artifact over approved notes; exact included IDs/hash | artifact registry | stale/removal/heading tests | R3 |
| `assessment_bank.py` | Existing | Truncated filenames; partial bank accepted; amplified calls | Separate component jobs/raw results; full ID paths; combined validation; approval gate | adapters/artifacts | collision, partial, call count | R1/R3/R4 |
| `soft_skills_engine.py` | Existing | Standalone cached generation | Optional governed content pack with prompt/model fingerprint; no implicit full-run requirement unless enabled | adapters/prompts | cache/error tests | R3/R4 |
| `viva_command_center.py` | Existing | Truncated IDs; automatic Claude→Gemini fallback; live paid evaluation | Full-ID bank; explicit fallback policy; quality gate; interactive evaluation budget/session ledger | adapters/artifacts | collisions/fallback/budget | R1/R4 |
| `overnight_batch_renderer.py` | Existing | Existence/render-ID cache; weak escaping | Artifact-driven local render jobs; input fingerprint; resumable failure state; safe filter escaping | controller/artifacts | FFmpeg crash/stale/title tests | R3/R5 |
| `windows_bridge.py` | Existing | Copies drop-zone files repeatedly; partial root policy | Source discovery adapter; immutable source IDs; authorized roots; no direct pipeline mutation | source registry | traversal/symlink/repeat tests | R2 |
| `jsonl_serializer.py` | Existing | Diagnostic request logic drifts from synthesis | Read-only preview/export using shared request builder; validate IDs and exact estimates | adapters/planner | parity/unique ID tests | R1 |
| `dedup_engine.py` | Existing | Destructive near-dedup; path-keyed embeddings; pre-stage registry updates | Source dedup service; content/model keys; exact duplicate auto-group; semantic conflicts reviewed; non-destructive default | source registry/adapters | embedding cache, false-positive safety | R2/R3 |
| `manifest_repair.py` | Existing | Direct one-time mutator, partial ID rewrite | Deprecated wrapper to import/migration command; never writes authoritative file directly | migrations/controller | legacy fixture migrations | R2 |
| `pipeline_guide_final.md` | Existing | Incorrect ordering/counts/cost/final claims | Replace with operator guide matching controller/dashboard and recovery | all | doc command checks | R5/R7 |
| `requirements.txt` | Existing | Lower bounds only; no tests/dashboard | Split/lock runtime/dev dependencies; add SQLite migration helper only if needed, Streamlit, pytest | packaging | clean-install test | R1/R6/R7 |
| `_remotion_project/*` | Existing | Empty scaffold, not connected | Either make an artifact renderer plugin after local FFmpeg path works or mark optional; do not block MVP | artifacts | lint/smoke if retained | R7 |

## 5.2 Cohesive new modules

Avoid one file per tiny concept. Use these packages:

| File/package | Existing/new | Responsibility | Exact contents | Dependencies | Tests | Release |
|---|---|---|---|---|---|---|
| `pve/manifest.py` | New | Schema, validation, identity, migration, repository | Pydantic/dataclass schema; validators; stable IDs/aliases; candidate/promotion; legacy migrations | settings/source/store | schema/identity/diff/migration | R2 |
| `pve/sources.py` | New | Source discovery/governance | Hashing; extraction fingerprints; authority/effective year; duplicate/conflict/supersession; approved set | paths/store | governance/version tests | R2 |
| `pve/store.py` | New | SQLite schema/repositories/migrations | DB initialization; runs/jobs/events/failures/budgets/API/artifact tables; transactions/locks | stdlib sqlite3 preferred | migration/transaction/recovery | R1 |
| `pve/providers/base.py` | New | Shared provider contracts | Request/result types; error taxonomy; usage/cost; adapter interface | settings | error/fingerprint contracts | R1 |
| `pve/providers/gemini.py` | New | Gemini adapter | generation/grounding/embedding configs; response normalization; lifecycle; mock seam | base | mocked all outcomes | R1 |
| `pve/providers/claude.py` | New | Claude adapter | standard/batch submit/poll/results; exact request builder; capability limits | base | mocked messages/batches | R1 |
| `pve/financial.py` | New | API ledger + budget guard + circuit breaker | Canonical fingerprint; reservations; attempts; fallback/force policy; ambiguity recovery | store/providers | P0 invariants | R1 |
| `pve/artifacts.py` | New | Artifact/dependency/checkpoint/citation registry | Collision-free paths; metadata; raw checkpoint; DAG; stale propagation; citation structures | store/manifest | hash/path/stale tests | R1/R3/R4 |
| `pve/quality.py` | New | Quality judge and grounding verifier | Separate schemas/services; thresholds; deterministic prechecks; manual review; bounded regen policy | adapters/prompts/artifacts | parse/gate/regeneration tests | R4 |
| `pve/prompts.py` + `prompts/*.yaml` | New | Versioned prompt loader | Prompt IDs/versions/hashes; provider-operation templates; parameter policy | settings | hash/change tests | R4 |
| `pve/controller.py` | New | Planner, command service, state machine | Preview; command idempotency; dependency gates; full-run plans; recovery; pause/cancel | all core packages | integration/state-machine | R1/R5 |
| `pve/worker.py` | New | Single local job runner | Lease/claim/heartbeat; sequential dependency execution; startup recovery | controller/store | lease/crash tests | R5 |
| `pve/metrics.py` | New | Queries/projections | Cost/cache/retry/fallback/quality metrics and redaction | store | aggregation tests | R5/R6 |
| `dashboard/app.py` | New | Streamlit entry point/navigation | Read-only page composition; controller command forms | controller/metrics | smoke/read-only | R6 |
| `dashboard/pages/*.py` | New | Ten required pages | Overview, Manifest, Sources, Pipeline, Ledger, Quality, Artifacts, Metrics, Failures, Settings | dashboard services | page/action tests | R6 |
| `pve/cli.py` | New | Unified CLI | Same preview/confirm/command methods as dashboard; legacy script migration path | controller | CLI contract tests | R5 |
| `tests/` | New | Offline safety suite | Unit, provider mocks, crash, integration, dashboard, invariants | all | itself | R1 onward |
| `pyproject.toml` + lockfile | New | Packaging/tooling | Package metadata, pinned dependencies, pytest/ruff/mypy configuration, commands | packaging | clean install | R1/R7 |

No separate `failure_tracker.py`, `event_store.py`, `job_store.py`, `budget_guard.py`, and `api_ledger.py` are needed as isolated files; those are cohesive repositories/services inside `store.py` and `financial.py`.

---

# Section 6 — Schema definitions

## 6.1 Authoritative manifest

```json
{
  "schema_version": "3.0",
  "manifest_id": "mf_<sha256>",
  "manifest_version": 1,
  "checksum": "sha256:...",
  "created_at": "...",
  "promoted_at": "...",
  "promoted_from_candidate": "cand_...",
  "source_set_id": "srcset_...",
  "modules": [
    {
      "module_id": "mod_<stable-128-bit-or-longer-digest>",
      "legacy_ids": [],
      "status": "active",
      "module_type": "lecture",
      "teachable": true,
      "title": "Matrices and Determinants",
      "subject": "Mathematics",
      "raw_subject": "Mathematics (JEE Mains & Advanced)",
      "course_title": null,
      "subject_code": null,
      "track": "JEE Mains & Advanced",
      "domain": "01_Math",
      "tier": "Senior",
      "grade_or_sem": "Class 11-12",
      "academic_year": "2025-26",
      "complexity": "Advanced",
      "priority": 5,
      "core_subtopics": [],
      "prerequisite_module_ids": [],
      "learning_chapters": [],
      "synthesis_request_parts": 1,
      "estimated_study_minutes": 90,
      "media_duration_seconds": 180,
      "default_visual_engine": "remotion",
      "provenance": [
        {
          "source_id": "src_...",
          "pages": [1, 2],
          "source_field": "title",
          "authority": "primary",
          "extraction_hash": "sha256:..."
        }
      ],
      "research": {
        "published_pack_artifact_id": null,
        "status": "not_started",
        "dependency_fingerprint": null
      },
      "note": {
        "published_artifact_id": null,
        "quality_status": "not_started",
        "grounding_status": "not_started",
        "dependency_fingerprint": null
      },
      "artifacts": {},
      "review": {
        "status": "approved",
        "reasons": []
      }
    }
  ]
}
```

Rules:

- `module_id` never changes when title/domain/semester metadata is corrected.
- `legacy_ids` support migration.
- Unknown or ambiguous items remain visible with `teachable=false` and review status.
- Only configured module types may enter each paid phase.
- No output path is authoritative without an artifact metadata record.

## 6.2 Source registry

```json
{
  "source_id": "src_<content-digest>",
  "byte_hash": "sha256:...",
  "extracted_content_hash": "sha256:...",
  "path": "relative/authorized/path.docx",
  "media_type": "application/vnd...",
  "document_type": "curriculum",
  "authority": "primary|secondary|unknown",
  "academic_year": "2025-26",
  "effective_from": "2025-07-01",
  "effective_to": null,
  "status": "approved|rejected|superseded|conflict|pending_review",
  "supersedes": ["src_..."],
  "duplicate_group_id": null,
  "version_group_id": "srcgrp_...",
  "extractor_version": "docx-v2",
  "discovered_at": "...",
  "review_notes": ""
}
```

## 6.3 Dependency fingerprint

```json
{
  "fingerprint_version": 1,
  "phase": "assessment",
  "module_id": "mod_...",
  "upstream_artifacts": {"note": "sha256:..."},
  "source_set_hash": "sha256:...",
  "prompt_id": "assessment.mcq",
  "prompt_version": "2.1",
  "prompt_hash": "sha256:...",
  "provider": "gemini",
  "model": "...",
  "parameters": {"temperature": 0.2, "max_output_tokens": 4096},
  "schema_version": "assessment-2",
  "phase_version": "3",
  "relevant_config_hash": "sha256:..."
}
```

Canonical UTF-8 JSON with sorted keys is SHA-256 hashed. Timestamps, run IDs, paths, and other irrelevant metadata are excluded.

## 6.4 API ledger tables

`api_calls`:

- `call_id`
- `request_fingerprint` unique within operation policy
- `run_id`, `job_id`, `module_id`, `phase`
- `provider`, `operation`, `model`
- `status`: `prepared|calling|ambiguous|submitted|running|received|succeeded|retryable_failed|permanent_failed|cancelled`
- canonical request metadata and prompt hash
- provider request/batch ID
- raw request/result artifact IDs
- attempt count
- input/output/cache tokens
- estimated/reserved/actual cost and currency
- created/updated timestamps
- force reason and superseded call ID

`api_attempts`:

- attempt ID/call ID/sequence
- start/end
- error class/status/provider request ID
- retry-after
- usage/cost
- redacted diagnostic

## 6.5 Jobs, runs, events, failures

`runs`:

- scope, plan fingerprint, manifest checksum, state, estimated/actual cost, initiator, confirmation record.

`jobs`:

- phase/module/component, state, dependency fingerprint, lease owner/expiry, attempts, checkpoint, parent job.

`events`:

- append-only event ID/run/job/time/type/severity/redacted payload.

`failures`:

- failure ID; class; retryability; ambiguity; last checkpoint; request fingerprint; recommended action; resolution notes/status.

## 6.6 Quality

```json
{
  "quality_schema_version": "1",
  "module_id": "mod_...",
  "note_artifact_hash": "sha256:...",
  "mandatory_sections": {"overview": true},
  "subtopic_coverage": [{"subtopic": "...", "status": "covered", "evidence": "..."}],
  "derivation_completeness": 0.91,
  "clarity": 0.88,
  "progression": 0.90,
  "exam_usefulness": 0.87,
  "blocking_issues": [],
  "score": 0.89,
  "decision": "approved|manual_review|rejected",
  "judge_fingerprint": "sha256:..."
}
```

Deterministic checks run first. An optional model judge is separately ledgered and cached.

## 6.7 Grounding

```json
{
  "grounding_schema_version": "1",
  "module_id": "mod_...",
  "note_artifact_hash": "sha256:...",
  "claims": [
    {
      "claim_id": "clm_...",
      "text_hash": "sha256:...",
      "type": "definition|formula|fact|derivation",
      "support": "supported|unsupported|uncertain|contradicted",
      "citations": ["cit_..."],
      "severity": "blocking|warning"
    }
  ],
  "formula_checks": [],
  "unsupported_count": 0,
  "decision": "approved|manual_review|rejected",
  "verifier_fingerprint": "sha256:..."
}
```

## 6.8 Artifact metadata

```json
{
  "artifact_id": "art_<digest>",
  "module_id": "mod_...",
  "phase": "note",
  "component": "published",
  "path": "artifacts/mod_.../note/...",
  "media_type": "text/markdown",
  "artifact_hash": "sha256:...",
  "dependency_fingerprint": "sha256:...",
  "status": "current|stale|failed|retired|quarantined",
  "upstream_artifact_ids": [],
  "raw_artifact_id": "art_...",
  "created_by_job_id": "job_...",
  "published_at": "...",
  "validation": {"schema": "passed", "module_id": "passed"}
}
```

---

# Section 7 — Pipeline state machine

## 7.1 Common legal states

```text
not_started → queued → prepared → running
running → received → parsed → validating
validating → approved → published
any active state → failed
validating → manual_review
approved/published → stale
active/stale/manual_review → retired
```

Transitions require controller guards and append an event.

## 7.2 Manifest

```text
discovered → candidate → validating
validating → invalid | conflicts | ready_for_promotion
ready_for_promotion → promoting → authoritative
authoritative → superseded
conflicts → manual_review → ready_for_promotion
```

Only `ready_for_promotion` can be promoted. Promotion requires no blocking conflict and a matching source-set checksum.

## 7.3 Research

```text
not_started → planned → component_queued → component_running
→ component_received → component_validated
→ pack_building → approved | manual_review | failed
approved → published
published → stale
```

Components are independently checkpointed. Synthetic fallback is its own component type.

## 7.4 Synthesis and Claude Batch

Synthesis:

```text
not_started → prepared → submitted/running → received
→ raw_checkpointed → parsed → validating
→ manual_review/rejected/approved → published
```

Batch:

```text
prepared → submitting → submitted → running → ended
→ results_received → reconciled
```

Special rule:

- A process restart that finds `submitting` without a provider ID transitions it to `ambiguous`, never back to `prepared`.
- `ambiguous` requires provider/manual reconciliation or an explicitly confirmed superseding call.

## 7.5 Quality and grounding

```text
not_started → queued → deterministic_checks → optional_model_check
→ parsed → approved | manual_review | rejected | failed
approved result + matching note hash is required for publication/dependents.
```

Quality and grounding are independent. Both must approve under configured thresholds.

## 7.6 Artifacts

```text
planned → generating → raw_received → processing → validating
→ current
current → stale
stale → regenerating → current
any → failed/manual_review/retired
```

## 7.7 Full runs

```text
draft → previewed → awaiting_confirmation → queued → running
running ↔ pausing → paused
running → completed | completed_with_review | failed | cancelled
failed/paused → recovering → running
```

Pause stops new job claims; it does not pretend a submitted provider batch is cancelled. Cancel marks future work cancelled and attempts provider cancellation only where supported.

---

# Section 8 — Dashboard specification

## 8.1 Framework decision

### Recommendation: Streamlit with a durable controller and SQLite-backed worker

Why:

- Fast delivery of local tables, filters, forms, charts, artifacts, and multipage views.
- Forms provide an explicit submit boundary.
- Fragments can poll live status without rerunning an entire page.
- The controller/store—not Streamlit session state—owns commands and execution.
- The application/controller APIs remain framework-neutral for later FastAPI/React migration.

Why not direct Streamlit-to-phase functions:

- Streamlit reruns scripts reactively.
- Session state is tied to a WebSocket and resets on reload.
- A page refresh must never be an execution trigger.

Why not FastAPI plus custom frontend now:

- Better long-term API boundary, but substantially more frontend/process work before P0 value.
- Add FastAPI later if external clients or a richer frontend become real requirements.

Why not Gradio:

- Strong for model demos, weaker fit for dense operational tables, multi-page governance, and job/ledger administration.

## 8.2 Runtime shape

```text
streamlit run dashboard/app.py
pve worker
```

Both use the same local SQLite file and controller library. The worker has a singleton lease. Dashboard never spawns a paid call during rendering.

## 8.3 Pages

1. **Overview:** active manifest path/version/checksum, counts, health, jobs, stale/failed/review totals, spend, estimate, providers, latest run.
2. **Manifest:** filterable modules, type/teachable/status, provenance, conflicts, candidate diff, promotion, retired modules, prerequisites.
3. **Sources:** hashes, years, authority, supersession, duplicate/version groups, selection, source changes.
4. **Pipeline Control:** scope builder, phase DAG, dry-run plan, call/cost preview, budget, confirmation, start/pause/recover/rerun stale/failed, scoped force.
5. **Jobs and API Ledger:** calls/attempts/fingerprints/provider IDs/raw checkpoints/cache/usage/cost/errors.
6. **Quality and Verification:** separate quality/grounding views, unsupported claims/formulas/missing sections, review queue, approval.
7. **Artifacts:** research/note/citations/visual/audio/assessment/viva/compiled/video, hashes/dependencies/staleness/regenerate.
8. **Metrics and Cost:** spend, tokens, avoided calls, hit rate, retries, fallback, projections/trends.
9. **Failures and Recovery:** classification, ambiguity, checkpoint, safe actions, notes.
10. **Settings:** model/prompt versions, budgets, retries, breakers, paths, thresholds, features, safe mode; never keys.

## 8.4 Backend interfaces

Read:

- `get_overview()`
- `list_modules(filters)`
- `list_sources(filters)`
- `get_manifest_diff(candidate_id)`
- `list_jobs/calls/events/failures/artifacts`
- `get_metrics(range)`
- `preview_run(scope, phases, force_policy)`

Commands:

- `create_run(plan_fingerprint, confirmation)`
- `promote_candidate(candidate_id, checksum, command_id)`
- `pause_run`, `cancel_run`, `recover_run`
- `retry_failure`, `rerun_stale`, `rerun_failed`
- `reconcile_batch`
- `approve/reject_quality`

Every command accepts a caller-created idempotency key and returns the existing command/job if repeated.

## 8.5 Refresh strategy

- Normal pages execute `SELECT` queries only.
- Status fragments poll every 2–5 seconds.
- Command forms submit once and then render returned command ID.
- Buttons disable after the durable command exists.
- Browser refresh reconstructs the view from SQLite.

## 8.6 Confirmation contract

Paid execution requires:

- exact module/component scope;
- provider/model/operation counts;
- best/expected/worst cost;
- budget remaining;
- cache hit/stale/miss counts;
- fallback policy;
- force reason if any;
- typed or checkbox confirmation;
- immutable plan fingerprint.

If dependencies change between preview and confirmation, execution is rejected and must be re-previewed.

## 8.7 Authentication/deployment

- MVP assumption: one trusted local user on localhost.
- Bind to `127.0.0.1` by default.
- For a single remote server, require an authenticated reverse proxy/TLS and filesystem permissions.
- No product-level RBAC or multi-tenancy.
- Secrets come from environment/OS secret storage and are never returned by query methods.

---

# Section 9 — API-bleed protection design

## 9.1 Request fingerprint algorithm

Fingerprint inputs:

```text
provider + operation + model/version
+ canonical model parameters/tool/thinking config
+ prompt ID/version/hash
+ structured input hash
+ module ID
+ upstream artifact/source hashes
+ output schema version
+ phase implementation version
+ relevant policy/config hash
```

Algorithm:

1. Normalize strings to UTF-8/NFC.
2. Serialize canonical JSON with sorted keys and no irrelevant metadata.
3. Hash large inputs separately.
4. SHA-256 the canonical envelope.
5. Store the envelope and digest.

Run IDs, timestamps, local absolute paths, and retry numbers are excluded.

## 9.2 Ledger algorithm

Within one SQLite transaction:

1. Look up the fingerprint.
2. If a successful call has a valid raw checkpoint, return cache hit.
3. If submitted/running, attach the job to it.
4. If permanent-failed and unchanged, return the failure without calling.
5. If calling/submitting with an expired lease or superseded fencing token, mark ambiguous and block.
6. Check circuit breaker and budget.
7. Insert/transition call to `prepared`, reserve worst-case cost, bind the approved preview/config/pricing snapshots, then commit.
8. Transition to `calling`/`submitting` and commit before network I/O.
9. Call provider exactly once for that attempt.
10. Immediately persist provider ID/raw response and usage before parsing.

## 9.3 Claude Batch crash algorithm

1. Build exact request list and batch fingerprint.
2. Reserve all module/component fingerprints in one transaction.
3. Write canonical request artifact.
4. Commit batch state `prepared`.
5. Set `submitting` and commit.
6. Call Anthropic create once.
7. Persist returned provider batch ID immediately.
8. Crash before step 7 leaves `submitting`.
9. Startup changes expired `submitting` to `ambiguous`; it never automatically resubmits.
10. Reconciliation searches recent provider batches where possible and compares time, counts, workspace, custom IDs/results, and stored request artifact.
11. If uniquely matched, attach provider ID.
12. If not provable, require explicit manual choice: keep blocked or create a separately fingerprinted superseding submission with acknowledged cost.

This is fail-closed. Without provider-supported idempotency, the system cannot prove whether an HTTP request accepted just before process death; it can prevent automatic duplicate spending by refusing to guess.

## 9.4 Budgets

Budgets exist at:

- global daily/monthly;
- provider;
- run;
- phase;
- optional module/component.

Reserve worst-case estimated cost before calling. A call cannot start if reservation exceeds the applicable limit. Unknown pricing or model capability blocks paid execution unless an explicit conservative override is recorded.

## 9.5 Retries

- One adapter-level retry loop only.
- Logical “generate more” requests are separate ledger calls and count toward the plan.
- Permanent errors: zero retry.
- Transient errors: bounded attempts, exponential backoff, full jitter, provider `Retry-After`.
- Ambiguous transport outcomes: no automatic retry unless provider operation is known idempotent.
- Retry counters persist across restarts.

## 9.6 Circuit breakers

Per provider/operation/model:

- open on repeated auth/model-not-found/permanent configuration failures immediately;
- open on a configured transient-failure rate/window;
- prevent new calls while open;
- allow read-only health checks that do not call paid generation endpoints;
- manual or timed half-open canary.

## 9.7 Fallbacks

- Disabled by default for paid/provider-switch fallback.
- Included in preview as an explicit branch and maximum cost.
- Synthetic research fallback is labeled `synthetic_fallback`, never `source`.
- A fallback uses a new fingerprint linked to the failed primary call.
- Permanent invalid input does not trigger fallback automatically.

## 9.8 Force

No global Boolean force in normal UI.

Force command requires:

- exact module/component IDs;
- old and new dependency fingerprints;
- reason;
- preview and confirmation;
- new generation ID;
- preservation of the old artifact;
- budget reservation.

## 9.9 Raw response preservation

- Stream/download to a temporary file where applicable.
- Flush and atomically rename.
- Compute checksum.
- Insert artifact metadata.
- Only then parse.
- Parsing, TTS, FFmpeg, validation, or publication resumes from raw artifacts without provider recall.

## 9.10 Zero-call rerun algorithm

Planner computes desired dependency fingerprints for every phase:

1. Valid current artifact with matching fingerprint → cached.
2. Valid in-flight provider transaction → recover.
3. Mismatch → stale and planned.
4. Invalid manifest/unapproved upstream → blocked, not planned.
5. No desired changes → plan has zero external calls.

Invariant test executes the complete plan twice against counting mocks; the second run must record zero new provider adapter invocations and report all expected cache hits.

---

# Section 10 — Migration plan

## 10.1 Freeze and inventory

1. Disable legacy paid entry points via safe-mode guard.
2. Copy both manifests and calculate checksums.
3. Inventory state, batches, artifacts, source files, and collisions without mutation.
4. Remove/rotate secrets and fix permissions.
5. Record a migration snapshot/backup.

## 10.2 Manifest selection

1. Import both 274- and 142-entry manifests as immutable legacy candidates.
2. Validate both under legacy schemas.
3. Confirm the expected 142-module scope against approved curriculum sources.
4. Do not assume “142” alone proves authority.
5. Produce module add/remove/change/source-conflict reports.
6. Promote only the verified candidate to schema v3.

## 10.3 Stable ID migration

1. Assign stable new module IDs.
2. Build `legacy_id → module_id` mapping.
3. For exact-safe duplicate mappings, preserve all aliases/provenance.
4. For ambiguous semantic matches, create conflict records.
5. No first-entry deletion.
6. Verify global uniqueness before promotion.

## 10.4 Artifact migration

For each existing artifact:

1. Infer candidate module from embedded metadata/path.
2. Validate content metadata and source hash.
3. If uniquely attributable, copy/move into new immutable identity path and record checksum.
4. If colliding or ambiguous, quarantine for review.
5. Never treat one old colliding file as valid for multiple modules.
6. Regenerate only after the applicable paid-action preview.

The 8 note/audio collision groups and 31 assessment/viva groups require explicit reports. Existing overwritten content cannot be reconstructed locally and may require regeneration.

## 10.5 State migration

- Import valid completed IDs only when matching artifacts and module IDs can be verified.
- Import provider batch IDs with exact known module/request sets where evidence exists.
- Unknown batch provenance becomes `manual_review`, not pending.
- Missing/corrupt state is preserved as evidence and blocks automatic paid recovery.

## 10.6 Cache migration

- Hash/embedding registries are imported with a legacy cache version.
- Embeddings lacking content/model/task metadata are not trusted as current; they may be retained as hints but cannot suppress correctness-critical processing until verified.
- Mapper cache is rebuilt from approved source hashes without calling providers.

## 10.7 Prompt/reference migration

- Extract prompt text into versioned prompt files and hash current behavior as version `legacy-1`.
- Existing references become unverified citation candidates.
- Synthetic fallback references are relabeled where detectable.
- Old notes require quality/grounding validation before dependent reuse.

## 10.8 Repair-tool migration

`manifest_repair.py` becomes a legacy importer/migration wrapper. It outputs a candidate and report; it never writes the authoritative manifest.

## 10.9 Rollback

- All legacy files remain read-only under a timestamped migration snapshot.
- Manifest promotion stores prior version/checksum.
- SQLite migration uses backup plus versioned migrations.
- Rollback restores the prior manifest, DB backup, and artifact registry pointers; immutable artifacts are retained.

---

# Section 11 — Testing plan

## 11.1 Unit tests

- Stable identity and legacy aliases.
- Long-prefix collision fixture.
- `&`/`and` exact-safe normalization and ambiguous non-merge.
- Biology/CS minor/domain regressions.
- all domains across path/compiler/filter/router.
- module type/teachable rules.
- source authority/supersession.
- manifest schema and promotion guards.
- query construction/validation.
- separated subject/raw subject/track.
- chapter/request/media fields.
- per-visual routing.
- multi-question single-line PYQ extraction.
- authorized-root, traversal, symlink, null-byte, absolute-path behavior.
- mapper/cache fingerprints.
- Gemini config shape.
- model capability/output ceiling.
- dependency fingerprints.
- provider error classification.
- budget/circuit breaker.
- artifact path uniqueness.
- quality/grounding parsing.
- citation validation.

## 11.2 Provider-mocked tests

For Gemini generation, grounding, embedding, Claude standard, and Claude Batch:

- success and usage;
- cache hit;
- 400/401/403/404/model retired;
- 429/retry-after;
- 500/503/timeout;
- connection loss after possible acceptance;
- malformed/empty/blocked/truncated output;
- multiple content blocks;
- partial/expired/cancelled batch results;
- fallback disabled/enabled;
- raw checkpoint write failure.

Tests must patch the adapter boundary and poison network access.

## 11.3 Crash tests

Inject process termination or exceptions:

- before call intent;
- after intent;
- after budget reservation;
- after `calling/submitting`;
- immediately after provider acceptance;
- before provider ID save;
- after provider ID save;
- after raw result;
- during parsing;
- during manifest atomic projection;
- during TTS line generation;
- during FFmpeg;
- during quality/grounding;
- during artifact publish.

Restart must either resume locally, recover provider work, or block as ambiguous—never silently recall.

## 11.4 Integration tests

- Source discovery through approved source set.
- Candidate creation, validation, diff, conflict resolution, promotion.
- Research component checkpointing and pack build.
- Junior synthesis and Claude batch simulation.
- Quality and grounding gates.
- Assessment/viva/audio/visual/compile/video.
- Stale propagation and selective rerun.
- Full dry run with a small fixture manifest.

## 11.5 Invariant tests

- Second unchanged full run invokes zero provider methods.
- Dashboard refresh invokes zero provider methods and creates zero commands.
- Invalid/unpromoted manifest invokes zero paid methods.
- Non-teachable modules invoke zero paid methods.
- Failed/unapproved quality or grounding invokes zero dependent paid methods.
- Corrupt financial state invokes zero automatic resubmissions.
- Removed modules become retired.
- Prompt/model/source/upstream changes invalidate only affected descendants.
- Unrelated display metadata invalidates nothing.
- Artifact paths are globally unique.
- Every provider invocation has exactly one ledger call/attempt.
- Every successful paid response has a raw artifact before local parse.
- No automated test can reach live provider hosts.

## 11.6 Dashboard tests

- Page GET/render/read creates no jobs.
- Double-click with the same command ID creates one run.
- Refresh after submission creates no new run.
- Changed plan after preview invalidates confirmation.
- Paid command requires scope/cost/budget/confirmation.
- Force requires reason.
- Keys and secrets never appear in HTML, logs, API responses, or event payloads.
- Failure and ambiguity remain visible after restart.
- Filters, promotion, review, and artifact links use correct IDs.

## 11.7 Acceptance tests

Use three datasets:

1. Tiny synthetic fixture: all edge cases, zero live calls.
2. Sanitized demo curriculum: complete local workflow with mocked providers.
3. Verified 142-module manifest: dry run, then limited approved canary, then full release gate.

---

# Section 12 — Release roadmap

## Release 0 — Freeze and recovery preparation

**Complexity:** Medium  
**Files:** settings/config, safe-mode wrapper, inventory/migration scripts, docs  
**Migrations:** immutable backup of manifests/state/artifacts; secret removal  
**Tests:** checksum/inventory/read-only/secret checks  
**Acceptance:**

- Paid legacy entry points blocked by default.
- Both manifests checksummed and preserved.
- Expected 142-module authority is explicitly verified or remains blocked.
- Collision and active-batch inventory exists.
- No secrets in repository/deployment copy.

**Rollback:** Restore snapshot; safe mode remains on.  
**API policy:** Zero provider calls.

## Release 1 — Financial and identity safety

**Complexity:** Very High  
**Files:** store, financial, provider adapters, artifacts, settings; synthesis/assessment/viva adapters; tests  
**Migrations:** SQLite v1; artifact-ID map; financial-state import  
**Tests:** all P0 ledger/batch/crash/collision/corrupt-state tests  
**Acceptance:**

- Every external call requires ledger/budget/fingerprint.
- Financial attempts and reconciliation decisions are append-only; query projections rebuild from them.
- Ambiguous submissions never auto-repeat.
- In-flight module sets are reserved.
- Worker takeover uses transactional leases and fencing tokens; stale workers cannot commit.
- SQLite integrity, consistent backup, restore verification, and corruption fail-closed tests pass.
- JSON–SQLite–artifact reconciliation passes at every simulated crash boundary.
- Execution rejects expired previews and manifest/config/dependency/pricing drift.
- Artifact paths are globally unique.
- Irrecoverable artifact collisions are quarantined rather than guessed.
- Permanent errors receive zero retry.
- Raw responses survive local failures.
- P0 test suite passes with network blocked.

**Rollback:** DB/artifact registry backup; legacy paid paths remain disabled.  
**API policy:** Mocks only; at most an explicitly approved one-call sandbox canary after acceptance.

## Release 2 — Manifest governance

**Complexity:** Very High  
**Files:** manifest, sources, mapper, dedup, bridge, repair importer, domain constants  
**Migrations:** legacy manifests → candidates → schema v3; stable-ID/alias map  
**Tests:** historical bug suite, authority, candidate/promotion/removal  
**Acceptance:**

- One promoted manifest/checksum.
- Every module has provenance/type/teachable/status.
- No duplicate IDs or blocking conflicts.
- Removed sources retire entries explicitly.
- Mapper never overwrites authority.

**Rollback:** Promote prior manifest version; retain candidate/diff.  
**API policy:** Mapping calls only through approved, previewed jobs; unchanged approved sources make zero calls.

## Release 3 — Cache and dependency correctness

**Complexity:** High  
**Files:** artifacts, controller foundations, research/audio/visual/assessment/compiler/renderer  
**Migrations:** import verifiable artifacts; quarantine collisions; build DAG  
**Tests:** selective invalidation, checkpoints, stale/failed reruns  
**Acceptance:**

- Every artifact has dependency and content hashes.
- Prompt/upstream changes invalidate exact descendants.
- Unrelated metadata changes invalidate nothing.
- Failed local steps resume without provider recall.
- Rerun-stale/failed is selective.

**Rollback:** Restore prior registry projection; immutable artifacts remain.  
**API policy:** Counting mocks until zero-call invariant passes.

## Release 4 — Educational quality

**Complexity:** Very High  
**Files:** prompts, quality, citations/grounding inside artifacts/quality, phase prompts  
**Migrations:** legacy prompts/references; existing notes enter unverified state  
**Tests:** deterministic gates, mocked judges, unsupported claims, bounded regeneration  
**Acceptance:**

- Quality and grounding stored separately.
- Truncated/partial notes cannot approve.
- No dependent paid generation from unapproved notes.
- Citations and unsupported claims are visible.
- Regeneration is bounded, previewed, and never automatic beyond policy.

**Rollback:** Disable optional model judge; deterministic validation/manual review remains.  
**API policy:** Quality/verifier calls ledgered and budgeted as separate financial transactions.

## Release 5 — End-to-end controller

**Complexity:** Very High  
**Files:** controller, worker, CLI, metrics; legacy script wrappers; docs  
**Migrations:** job/run/event initialization  
**Tests:** full state machine, pause/recover, full mocked pipeline  
**Acceptance:**

- One command can plan and execute the valid full DAG.
- Restart recovery works at every checkpoint.
- Dry-run planner shows exact external-call plan.
- Full mocked integration run completes.
- Second run makes zero provider calls.

**Rollback:** Controller safe mode; inspect/recover durable jobs.  
**API policy:** No unconfirmed calls; canary gate only after dry-run acceptance.

## Release 6 — Dashboard MVP

**Complexity:** High  
**Files:** Streamlit app/pages, dashboard query/command facade, packaging  
**Migrations:** none beyond settings defaults  
**Tests:** read-only refresh, idempotent actions, confirmation, secret redaction  
**Acceptance:**

- All ten pages exist and query durable state.
- All paid actions use preview/confirmation.
- Refresh/double-click cannot duplicate jobs.
- Failures/recovery/artifacts/costs are operable without filesystem inspection.

**Rollback:** Stop dashboard; CLI/controller remains fully functional.  
**API policy:** Dashboard has no provider dependency; only controller worker can call.

## Release 7 — Startup MVP polish

**Complexity:** Medium–High  
**Files:** packaging, onboarding, docs, demo, backup/export, optional Remotion  
**Migrations:** supported backup/restore/export format  
**Tests:** clean install, first-run, backup/restore, demo/UAT  
**Acceptance:**

- Reproducible install/launch.
- First-run configuration validation.
- Sanitized demo dataset.
- Backup/restore and export.
- Clear operations/recovery guide.
- Investor/user demo flow passes.

**Rollback:** Reinstall prior locked release and restore backup.  
**API policy:** Demo defaults to mock/safe mode; live canary explicitly enabled.

---

# Section 13 — Startup MVP readiness checklist

## 13.1 Safe for dry run

- [ ] Release 0 inventory complete.
- [ ] Secrets removed/rotated and permissions validated.
- [ ] One candidate/authoritative manifest path is explicit.
- [ ] SQLite migrations pass.
- [ ] Provider hosts blocked in test mode.
- [ ] Planner reports calls/cost/blockers without calling.
- [ ] Invalid/corrupt state fails closed.

## 13.2 Safe for limited provider canary

- [ ] Release 1 P0 tests pass.
- [ ] SQLite integrity check, consistent backup, and test restore pass.
- [ ] Cross-store crash-boundary reconciliation and fencing-token tests pass.
- [ ] Canary scope is one or a few non-sensitive modules.
- [ ] Exact fingerprint, immutable run config, pricing snapshot, cost, budget, and confirmation are recorded.
- [ ] Preview drift and duplicate-command tests produce zero calls.
- [ ] Circuit breakers enabled.
- [ ] Raw result checkpoint verified.
- [ ] Crash-after-acceptance tests pass.
- [ ] No ambiguous historical batch remains in the selected scope.

## 13.3 Safe for full 142-module generation

- [ ] The manifest is verified from source authority and provenance—not entry count—then schema-v3 validated, promoted, and checksummed.
- [ ] No blocking source or semantic conflicts.
- [ ] Source selection/supersession decisions are reviewable and audited.
- [ ] Every paid module is teachable and approved.
- [ ] Permanent module identities are independent of mutable metadata.
- [ ] No unresolved artifact/custom-ID collisions; irrecoverable legacy collisions are quarantined.
- [ ] Full cost and call-count preview approved.
- [ ] Budgets cover worst case without exceeding limits.
- [ ] Full mocked run and unchanged zero-call rerun pass.
- [ ] Batch reconciliation/recovery drill passes.
- [ ] Quality and grounding gates are operational.
- [ ] Quality-driven regeneration has tested per-artifact call and cost ceilings.

## 13.4 Safe for everyday use

- [ ] Release 5 complete.
- [ ] Selective stale/failed reruns work.
- [ ] Backup/restore tested.
- [ ] Failures have actionable recovery guidance.
- [ ] Model lifecycle/pricing settings are current.
- [ ] No normal workflow requires filesystem inspection.

## 13.5 Safe for user/investor demonstration

- [ ] Dashboard pages complete and stable.
- [ ] Sanitized demo sources/artifacts.
- [ ] Safe/mock mode is visually explicit.
- [ ] Costs/cache savings/quality/provenance are demonstrable.
- [ ] No secrets/local personal files appear.
- [ ] First-run and demo scripts pass on a clean environment.

## 13.6 Safe for external beta

- [ ] Single-server deployment is authenticated and TLS-protected.
- [ ] Threat review covers uploads, paths, artifacts, logs, and secrets.
- [ ] Backup/restore/upgrade procedure tested.
- [ ] Data retention and provider disclosure documented.
- [ ] Usage/budget limits cannot be bypassed through the UI.
- [ ] Support/recovery process exists.
- [ ] Multi-user limitations are explicitly disclosed.

---

# Section 14 — Deferred scope

Explicitly out of scope for this MVP:

- PostgreSQL.
- Redis, Celery, RabbitMQ, Kafka, or distributed queues.
- Kubernetes, containers orchestration, autoscaling, or microservices.
- Multi-tenancy, enterprise RBAC, organizations, billing accounts.
- Public SaaS launch.
- Mobile application.
- Neo4j or graph database.
- Full RAG/vector database.
- Multiple new model providers.
- Broad local parallelism.
- Automatic provider switching.
- Distributed rendering farm.
- Real-time collaborative manifest editing.
- Complex workflow-engine adoption.
- Enterprise observability stacks.

Possible post-MVP bounded improvements:

- Small local concurrency for purely local independent rendering.
- FastAPI facade when an external frontend/client is justified.
- PostgreSQL migration when concurrent users/writers are real.
- Benchmark harness for alternative models, separate from production routing.

---

# Section 15 — Final recommendation

## 15.1 Is the startup MVP realistic?

Yes. The target is realistic for a single-user local or single-server MVP, but it is a staged hardening/productization effort—not a dashboard-only enhancement. The main complexity lies in financial idempotency, migration, and dependency correctness rather than UI rendering.

## 15.2 Architecture to adopt

Adopt:

- a versioned, promoted JSON manifest for authoritative curriculum and published artifact references;
- SQLite/WAL for operational jobs, API ledger, budgets, events, failures, locks, and live state;
- immutable content-addressed filesystem artifacts;
- one controller and one local process-safe worker;
- Gemini and Claude behind provider adapters;
- Streamlit as the dashboard over controller/query methods;
- top-level serial phase orchestration;
- fail-closed ambiguity and state corruption behavior.

## 15.3 Minimum safe implementation path

```text
Release 0 freeze
  → Release 1 financial + artifact identity safety
  → Release 2 authoritative manifest/source governance
  → Release 3 dependency correctness
  → Release 4 quality/grounding
  → Release 5 end-to-end controller
  → Release 6 dashboard
  → Release 7 polish
```

Do not start dashboard implementation before Releases 1–3 establish durable command, ledger, identity, and dependency semantics. The dashboard must expose safe operations, not compensate for unsafe internals.

## 15.4 Greatest remaining risks

1. Ambiguous provider acceptance where the provider offers no idempotency key.
2. Incorrectly promoting the expected 142-module manifest without source-authority proof.
3. Lossy migration of already-colliding/overwritten artifacts.
4. Quality/grounding judge cost or false confidence if deterministic/manual controls are weak.
5. Model/pricing lifecycle changes.
6. Scope pressure to add UI polish before P0 invariants pass.

## 15.5 First implementation release

Execute **Release 0 — Freeze and recovery preparation** first.

Its first concrete deliverables should be:

1. paid safe-mode guard;
2. immutable checksum/backup inventory;
3. authoritative-manifest verification report;
4. secret removal/rotation and permission repair;
5. active batch/state/artifact collision inventory;
6. migration map skeleton;
7. network-blocked test harness baseline.

No paid provider call should be made during Release 0.
