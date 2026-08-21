# Precision Vault Engine — Independent Audit Report

**Audit date:** 2026-07-29  
**Method:** Read-only code, configuration, manifest, state, documentation, source-inventory, and static-behavior audit  
**External API calls made:** None  
**Production files modified during audit:** None

---

# Section 1 — Executive conclusion

## Overall assessment

Precision Vault Engine is a functional prototype with several useful safeguards, but it is not financially or operationally safe for an unattended full run in its current state.

| Question | Conclusion |
|---|---|
| Overall maturity | Prototype / early beta |
| Financially safe to run end-to-end | **No** |
| Safe to submit Claude batches | **No**—restart duplication is possible |
| Safe to regenerate the manifest automatically | **No**—source authority, stale-entry handling, and manifest identity are unresolved |
| Authoritative manifest adequately protected | **No**—two divergent master manifests exist |
| Unchanged rerun guaranteed to make zero model calls | **No** |
| Tests prove API-call idempotency | **No tests were supplied** |

The five most serious risks are:

1. **Claude Batch duplicate-submission window — Critical.**  
   In `synthesis_gate.py`, the batch is accepted by Anthropic before its ID is persisted. A crash between those operations makes the next run submit the same modules again.

2. **Artifact filename collisions — Critical.**  
   Vault files are keyed by tier/subject/title rather than module ID. In the configured 274-entry manifest, 8 filename-collision groups affect 16 modules. Assessment and viva filenames truncate IDs to 30 characters; 31 collision groups affect 103 modules.

3. **Two incompatible authoritative manifests — Critical.**  
   `config/master_manifest.json` contains 274 entries and is the runtime default. Root `master_manifest.json` contains 142 entries and matches later documentation/code assumptions. They are not interchangeable.

4. **No dependency-aware cache invalidation — Very High.**  
   Notes, diagrams, audio, assessments, viva banks, compiled files, and videos are generally considered complete merely because a file or ID exists. Source, prompt, model, manifest, and upstream-output changes do not invalidate them.

5. **Source governance is absent — Very High.**  
   Numerous overlapping 2024–25, 2025–26, “FY”, “FY_25 Sep”, “Latest”, and copied curriculum documents are processed together with no authority, effective-year, supersession, or conflict rules.

No project files were modified during the initial audit, no manifest was regenerated, and no provider API was called.

---

# Section 2 — Repository and architecture map

## Files inspected

| File | Responsibility |
|---|---|
| `config.py` | Paths, models, retry constants, taxonomy, OAuth, parsing utilities |
| `dedup_engine.py` | File extraction, hashing, embeddings, near-dedup, handwriting OCR, staging |
| `curriculum_mapper.py` | Curriculum extraction, Gemini parsing, IDs, normalization, manifest generation |
| `manifest_repair.py` | One-time schema/domain/dedup manifest mutation |
| `ingestion_researcher.py` | Classroom/Drive ingestion and web research enrichment |
| `synthesis_gate.py` | Gemini Junior synthesis and Claude Batch submission/retrieval |
| `visual_blueprint.py` | Gemini Mermaid generation and in-place note mutation |
| `audio_pipeline.py` | Gemini dialogue generation, Edge TTS, FFmpeg audio merge |
| `domain_compiler.py` | Local domain compilation |
| `assessment_bank.py` | Gemini MCQ, formula, and short-answer generation |
| `soft_skills_engine.py` | Standalone Gemini soft-skills generation |
| `viva_command_center.py` | Claude/Gemini viva generation and live evaluation |
| `overnight_batch_renderer.py` | Local FFmpeg video generation |
| `windows_bridge.py` | Windows drop-zone scanning and copying |
| `jsonl_serializer.py` | Offline Claude batch preview; not an active submitter |
| `pipeline_guide_final.md` | Operational guide and cost claims |
| `requirements.txt` | Unpinned Python dependencies |
| `_remotion_project` | Installed Remotion scaffold; composition currently returns `null` |
| Manifests/registries | Inspected structurally and statistically |
| Curriculum files | Inventoried and SHA-256 hashed; overlapping versions identified |
| Generated/source media | Inventoried by location/type; bulk binary contents were not treated as absent defects |

Listed project files were present. No test files, `vault_state.json`, `render_state.json`, or `curriculum_mapper_state.json` were supplied.

## Actual dependency graph

```text
Windows/Classroom/raw material
        |
        v
Dedup (-1) -> staging + hash/embedding registries
        |
Curriculum sources -> Mapper (0) -> master manifest
                                      |
                                      v
Classroom attachment linking + research enrichment (1)
                                      |
                                      v
Synthesis (2) -> vault Markdown
                    |       |       |
                    v       v       v
               diagrams   audio   assessments/viva
                    |       |       |
                    +-------+-------+
                            |
                    domain compilation/video
```

Required serial boundaries:

- Manifest validation and source-authority resolution must precede every paid module phase.
- Enrichment should precede synthesis if research is expected to ground notes.
- Synthesis must precede diagrams, audio, assessments, viva, compilation, and video.
- Dependent outputs must be invalidated when notes change.
- Claude Batch already supplies provider-side parallelism; local parallel submission is unnecessary and would increase duplicate/rate-limit risk.

The guide’s “full pipeline” violates this graph by submitting Claude batches and then immediately continuing into diagrams/audio/assessments before retrieval has produced notes.

## State and cache files

| State/artifact | Writer | Meaning currently used | Problem |
|---|---|---|---|
| `config/master_manifest.json` | Mapper, ingestion, repair | Runtime master manifest | Conflicts with root manifest |
| `master_manifest.json` | Supplied separately | Alternate master | 142 vs 274 entries |
| `config/curriculum_mapper_state.json` | Mapper | Extracted-text hash per source | Absent |
| `config/hash_registry.json` | Dedup | Byte/text hash → path | No schema/model/version; stale path semantics |
| `config/embedding_registry.json` | Dedup | Path → embedding | No content/model fingerprint |
| `vault_state.json` | Synthesis | Synthesised IDs and batch IDs | Absent; no prompt/output fingerprints |
| `render_state.json` | Renderer | Rendered IDs | Absent; no input fingerprints |
| Existing Mermaid block | Visual phase | “Diagram complete” | Non-empty marker only |
| MP3 existence | Audio phase | “Audio complete” | No script/note/model fingerprint |
| Assessment JSON existence | Assessment | “Assessment complete” | No completeness or dependency validation |
| Viva JSON existence | Viva | “Viva complete” | Same |
| Soft-skill JSON existence | Soft skills | “Complete” | No schema/model/prompt validation |

## Manifest readers and writers

### Writers

- `curriculum_mapper.py` replaces the manifest after mapping.
- `ingestion_researcher.py` replaces it after Classroom linking or enrichment.
- `manifest_repair.py` creates a backup and replaces it after repair.

### Readers

- `synthesis_gate.py`
- `visual_blueprint.py`
- `audio_pipeline.py`
- `domain_compiler.py`
- `assessment_bank.py`
- `viva_command_center.py`
- `overnight_batch_renderer.py`
- `jsonl_serializer.py`
- `ingestion_researcher.py`

None of the readers enforce a shared schema version or validate every entry before use.

## Generated locations

- `vault/<tier>/<subject>/<title>.md`
- `_audio/<tier>/<subject>/<title>.mp3`
- `_assessment/exam_prep/*.json`
- `_assessment/viva/*.json`
- `_assessment/soft_skills/*.json`
- `_compiled/<domain>.md`
- `_video_output/final/*.mp4`
- `_handwriting_extracts/*.txt`
- `_classroom_downloads/**`
- `vault_staging/**`
- `_quarantine/**`
- `_batch_jsonl/**`
- `_raw_fallback/**`

## External providers

Gemini generation, Gemini Google Search grounding, Gemini embeddings, Anthropic Messages, Anthropic Message Batches, Google OAuth, Classroom, Drive, YouTube transcript retrieval, NPTEL/HTTP scraping, arXiv, Edge TTS, and local FFmpeg.

---

# Section 3 — External API-call inventory

| ID | File/function | Provider/API | Trigger | Cost/quota | Cache | Retry/fallback | Duplicate risk | Severity |
|---|---|---|---|---|---|---|---|---|
| API-01 | `dedup_engine.get_embedding` | Gemini `gemini-embedding-2` | Every new dedup candidate | Free or paid by account; quota-limited | Path-based embedding registry | Up to 10 calls on 429/other API errors | Changed file at same path reuses stale vector; registry loss recalls API | Very High |
| API-02 | `extract_handwriting_if_present` | Gemini 2.5 Flash vision | Near-duplicate image loser | Potentially paid/quota-limited | Annotation file not checked before call | Up to 3 | Repeated near-dedup can repeat OCR | High |
| API-03 | `curriculum_mapper._gemini_map` | Gemini 2.5 Flash | Changed/uncached syllabus file | Potentially paid | Extracted-text hash state | Up to 3 | State absent; failed parsing recalls next run | Very High |
| API-04 | `YouTubeTranscriptFetcher._fetch` | YouTube transcript service | Each selected video | Quota/network-limited | None | Language fallbacks | Repeated every re-enrichment | Medium |
| API-05 | `YouTubeTranscriptFetcher._fallback` | Gemini generation | Missing transcript | Potentially paid | None | Up to 3 | Automatic paid fallback | High |
| API-06 | `search_and_fetch` | Gemini grounded search | Senior/Engineering enrichment | Grounding quota/cost | Only whole-module `content` sentinel | Up to 3 | Repeated on force/crash | Very High |
| API-07 | `NPTELScraper.search_nptel` | Gemini grounded search | Every enriched module | Grounding quota/cost | Same coarse sentinel | Up to 3 | Same | Very High |
| API-08 | `NPTELScraper.scrape` | HTTP/NPTEL | Search result | Network/rate-limited | None | Up to 3 HTTP tries | Repeated on rerun | Medium |
| API-09 | `NPTELScraper._fallback` | Gemini generation | 403/429/SSL/timeout/5xx | Potentially paid | None | Up to 3 | Failure automatically becomes extra LLM spend | High |
| API-10 | `DeepWebResearcher._web_search` | Gemini grounded search | Every enriched module | Grounding quota/cost | Coarse content sentinel | Up to 3 | Repeated after crash/force | Very High |
| API-11 | `ArxivSearcher.search` | arXiv HTTP API | Engineering module | Rate-limited | None | Up to 3 only for selected HTTP errors | Repeated | Medium |
| API-12 | OAuth refresh/flow | Google OAuth | Classroom startup/expiry | Quota/network | `token.pkl` | Interactive fallback | Token deletion can repeat OAuth | Medium |
| API-13 | `list_courses` | Classroom API | Classroom ingestion | Quota-limited | None | Pagination, no retries | Every run lists all courses | Medium |
| API-14 | `list_materials` | Three Classroom endpoints | Per course | Quota-limited | None | Pagination | Multiplicative per course and rerun | High |
| API-15 | `file_meta` | Drive `files.get` | Per attachment | Quota-limited | None | None | Repeated even for cached downloads | High |
| API-16 | `download` | Drive media/export | Missing local file | Quota-limited | Non-empty destination | Up to 3 | Partial files removed safely; filename cache can be wrong for changed Drive content | Medium |
| API-17 | `_synthesise_junior` | Gemini 2.5 Flash | Unsynthesised Junior ID | Potentially paid | `synthesised_ids` only | Up to 3 | State loss or stale ID repeats full synthesis | Very High |
| API-18 | `submit_batch` | Claude Message Batches | Senior/Engineering submit | Paid | State written after acceptance | No submit retry | Crash can duplicate entire batch | **Critical** |
| API-19 | `retrieve_batch` polling | Claude Batch retrieve | `--retrieve` | Quota-limited | Batch ID only | Indefinite retries until 30h | Permanent bad ID can cause about 3,600 polls | Very High |
| API-20 | `retrieve_batch` results | Claude Batch results | Ended batch | Quota-limited | No raw-response cache | One attempt | Local write/parsing failure requires refetch; results expire | High |
| API-21 | `visual_blueprint.generate_mermaid` | Gemini | Note without Mermaid or `--force` | Potentially paid | Mermaid substring | Up to 3 | Prompt/note/model changes ignored; force recalls all | High |
| API-22 | `audio_pipeline.generate_script` | Gemini | Missing MP3 | Potentially paid | Final MP3 only | Up to 3 | TTS/merge failure loses paid script | High |
| API-23 | `_tts_line` | Edge TTS service | Per dialogue line | External quota/service | Part file not reused safely | No retry | Many per module; crash restarts dialogue | High |
| API-24 | `assessment_bank._call` | Gemini | Missing assessment file | Potentially paid | Non-empty output file | Up to 3 per request | MCQ loop can call up to 7 logical generations, each with 3 transport attempts | Very High |
| API-25 | `soft_skills_engine._generate` | Gemini | Missing output | Potentially paid | Non-empty JSON | Up to 3 | Parse failure loses paid response | High |
| API-26 | `viva._claude_call` | Claude Messages | Missing viva bank | Paid | Non-empty output file | Up to 3 | All `APIError`s retried; then Gemini fallback | Very High |
| API-27 | `viva._gemini_call` | Gemini | Claude failure or live scoring | Potentially paid | None for evaluation | Up to 3 | Automatic second-provider spend | High |

## Current model status

- The configured Gemini embedding family is current, but the code uses `"models/gemini-embedding-2"` while official documentation gives the stable code as `gemini-embedding-2`. Compatibility should be tested under mocks/controlled canary rather than assumed.
- Gemini 2.5 Flash has an announced shutdown date of October 16, 2026.
- Anthropic documentation confirms batch `custom_id` values must be unique and limited to 1–64 allowed characters.
- Current Claude documentation contradicts several stale 8,192-token comments in the project.

Official references:

- https://ai.google.dev/gemini-api/docs/models/gemini-embedding-2
- https://ai.google.dev/gemini-api/docs/deprecations
- https://platform.claude.com/docs/en/build-with-claude/batch-processing
- https://platform.claude.com/docs/en/build-with-claude/extended-thinking

---

# Section 4 — API-bleed risk matrix

| Risk | Root cause / scenario | Maximum likely amplification | Financial impact | Existing protection / weakness | Recommended correction | Severity |
|---|---|---:|---|---|---|---|
| Duplicate Claude submission | Provider acceptance precedes state write | 2× per crash; unbounded across restarts | Entire Senior/Engineering batch repeated | Batch ID saved atomically, but too late | Durable submission intent/fingerprint; reconciliation with provider batches; refuse ambiguous resubmit | **Critical** |
| Concurrent/manual repeat submission | Pending module IDs are not reserved | One full batch per invocation | Full duplicate spend | Only completed IDs excluded | Submission lock plus exact request-set ledger | Critical |
| Invalid batch ID polling | `NotFound`, auth, bad request treated as generic transient | About 3,600 calls over 30h at 30-second intervals | Quota/operational harm | 30h timeout only | Classify permanent errors; bounded retry count | Very High |
| Enrichment retry cascade | Several independent 3-attempt grounded/search/fallback calls | Roughly 15–21 Gemini calls per module in worst paths | Large unexpected spend | Only non-empty `content` skips whole phase | Stage-level fingerprints and per-call durable results | Very High |
| Assessment amplification | Up to 7 MCQ logical calls + formula + short, each up to 3 attempts | Up to 27 Gemini requests/module | Large avoidable spend | Final file only | One structured request or checkpoint each bank component; cap budget | Very High |
| Viva fallback amplification | 3 Claude attempts followed by 3 Gemini attempts | 6 paid/quota calls/module | Two-provider spend | Fallback is automatic | Explicit fallback budget/approval and permanent-error classification | Very High |
| Lost paid audio script | Script held only in memory before many TTS calls/merge | 3 Gemini calls repeated per restart | Moderate at scale | Final MP3 cache | Persist script before TTS | High |
| Lost assessment/viva response | Parsing or write failure after successful generation | Full component repeated | Moderate/high | Atomic final write only | Save raw provider response first | High |
| Force flags | Mapper/research/visual force reprocess broad scopes | Full-phase repeat | Potentially large | Help text warns vaguely | Exact call-count/cost preview and confirmation | Very High |
| Corrupt/missing state | Load functions silently reset to empty state | Full phase repeated | Full re-generation | Atomic writes | Treat corrupt state as fail-closed; retain backup/checksum | Critical |
| Prompt/model/source changes | Caches key only on ID/file existence | Usually zero calls but stale products; manual force then full calls | Stale data followed by broad regeneration | None | Content-addressed dependency fingerprints | Very High |
| Model retirement | Generic retries on permanent provider errors | 3 per item; 10 for some embeddings | Quota waste across every module | Embedding handles 400/401/403/404 correctly | Shared provider error classifier | Very High |
| Invalid credentials | Most generation loops retry generic exception | 3 per item | Repeated useless calls/errors | None | Preflight once; fail closed | High |
| Batch result loss | No raw JSONL preservation; results expire after provider retention period | Re-fetch or resubmit after expiry | Potential full batch repeat | Provider retains results temporarily | Download and atomically preserve raw results before transformation | Very High |

## Unchanged rerun conclusion

An unchanged rerun is not zero-call safe:

- No synthesis state was supplied, so all configured modules appear pending.
- Enrichment skips only if `content` is non-empty; all 274 configured entries have empty content.
- Dedup registries can reduce calls, but are not keyed by content/model version.
- Completed artifacts skip some phases, but their identity can collide and their freshness is not checked.

---

# Section 5 — Manifest audit

## Schema and counts

### Configured runtime manifest

- 274 entries.
- Engineering: 193.
- Senior: 54.
- Junior: 27.
- No duplicate exact IDs.
- All 274 lack `track`.
- All 274 lack `prerequisite_context`.
- All 274 have empty `content`.
- All 274 have empty `source_files`.
- 112 entries merge multiple `source_docx` values.
- No explicit schema version or generation metadata.

### Root manifest

- 142 entries.
- Engineering: 80.
- Senior: 43.
- Junior: 19.
- Contains `track` and `prerequisite_context`.
- Also has empty enrichment/source-file fields.
- Not read by default.

## Findings

| Finding | Evidence | Impact | Severity | Status | Recommended correction |
|---|---|---|---|---|---|
| Two master manifests | 274 configured vs 142 root entries | Different paid workload and curriculum | **Critical** | Confirmed | Select one authoritative path; reject alternates unless explicitly imported |
| Runtime manifest predates current mapper schema | Missing `track` and prerequisites on all 274 | Current code/docs do not describe actual runtime data | Very High | Confirmed | Versioned schema plus migration/validation |
| Vault filename collision | 8 groups/16 modules share tier+subject+title | Notes overwrite nondeterministically; both IDs may be marked complete | **Critical** | Confirmed | Module-ID-based filenames |
| Assessment/viva filename collision | ID truncated to 30 chars; 31 groups/103 modules | Wrong cached artifact reused for another module | **Critical** | Confirmed | Full ID/hash filename and metadata verification |
| Stale-entry carry-forward | Mapper carries every existing entry not freshly produced | Removed/replaced sources leave zombie modules | Very High | Confirmed | Source-owned entry sets and explicit retirement |
| Failed reparse retains stale entry | Failed file produces none; old unmatched entries are carried | Changed source may silently retain obsolete modules | Very High | Confirmed | Quarantine source result; do not silently carry |
| First-entry-wins semantic dedup | Mapper and repair merge duplicates without authority comparison | Newer authoritative version can lose to older first entry | Critical | Confirmed | Authority-aware conflict records; no silent deletion |
| Weak semantic key | Domain + raw grade string + normalized title | Cross-version equivalence and renamed modules inconsistently handled | High | Confirmed | Stable source-qualified identity plus explicit equivalence |
| ID instability | ID includes domain, raw grade, normalized title | Classification/title correction changes ID and orphans downstream state | High | Confirmed | Stable UUID/content identity with mutable metadata |
| 32-bit hash suffix | Eight hex MD5 characters | Low but non-zero collision probability; no collision detection | Medium | Confirmed design risk | Longer digest and uniqueness assertion |
| Grade normalization not used by mapper | `_build_entry` accepts raw LLM grade string | `Sem 3`, `Semester 3`, etc. create distinct identities | High | Confirmed | Apply strict grade normalization/validation |
| Tier trusted from LLM | Invalid tiers default Engineering; valid but incorrect tiers accepted | School material can trigger Claude spend | Very High | Confirmed | Deterministic tier derivation and validation |
| Administrative courses survive | Entries include internship evaluation, participative learning, liberal arts labs, project evaluations | Paid outputs for non-lecture records | High | Confirmed | Explicit module-type and teachability gate |
| Subject canonicalization inconsistent | Configured manifest has many descriptive subjects despite current canonical mapper | Paths/prerequisites/caches fragment | High | Confirmed | Migration and controlled vocabulary |
| Source provenance is a string | `source_docx` uses `" | "` concatenation | No per-source year, authority, hash, page, or extraction version | Very High | Confirmed | Structured provenance objects |
| No source authority | 2024–25, 2025–26, latest, FY, Sep variants coexist | Conflicts silently merged | Very High | Confirmed | Authority/effective-date/supersession registry |
| No deletion propagation | State/artifacts retain IDs absent from later manifests | Orphaned content remains “complete” | High | Confirmed | Reconciliation report and tombstone policy |
| Repair mutates IDs | Domain repair rewrites ID prefix only | ID no longer matches mapper’s hash algorithm | High | Confirmed | Central ID function; migration mapping |
| Unsafe auto-overwrite | Mapper and ingestion atomically replace manifest without promotion validation | Valid manifest can be atomically replaced by invalid content | Critical | Confirmed | Validate, diff, backup, and require promotion step |

## Collision evidence

The configured manifest contains eight exact output-path collision groups affecting sixteen modules because vault and audio paths use only tier, subject, and title. Examples include:

- `Applied Physics`, Sem 1 and Sem 2.
- `Advanced Artificial Intelligence`, unknown semester and Sem 4.
- `AI in Natural Language Processing`, unknown semester and Sem 7.
- `Microcontrollers and Interfacing`, Sem 3 and Sem 4.
- `Robot Safety and Maintenance`, Sem 6 and Sem 7.
- `Kinematics of Robot`, Sem 3 and Sem 4.

Assessment and viva output names use `mid[:30]`. Static grouping found:

- 31 collision groups.
- 103 affected modules.
- Particularly large collision groups among Senior Mathematics, Natural Sciences, Physics, Geography, Political Science, and similarly prefixed engineering theory/lab modules.

## Single-source-of-truth conclusion

The manifest is not currently a reliable single source of truth. Important state is fragmented among two manifests, hash/embedding registries, missing synthesis/render state, artifact existence, and in-place Mermaid mutations.

---

# Section 6 — Bugs and defects

## API financial safety

### Critical — duplicate Claude batches

- **File/area:** `synthesis_gate.py`, `SynthesisGate.submit` and `submit_batch`.
- **Scenario:** Anthropic accepts the batch; the process crashes before `_save_state`.
- **Current behavior:** The provider has a paid in-progress batch, but local state has no batch ID. Restart selects the same modules and creates another batch.
- **Expected behavior:** An accepted or ambiguously submitted request set must never be silently resubmitted.
- **Downstream impact:** Duplicate full Senior/Engineering synthesis cost.
- **Suggested test:** Mock acceptance, raise immediately before state save, restart, and assert zero second submissions.

### Critical — no in-flight reservation

- Running `--submit Senior` twice before retrieval submits the same modules twice.
- `batch_ids` records only tier/count, not module IDs, request fingerprints, models, or prompt hashes.

### Very High — permanent polling failures loop for 30 hours

- Invalid batch IDs, authorization failures, and other permanent exceptions enter the generic 30-second retry loop.
- At 30-second intervals, the upper bound is approximately 3,600 polling calls in 30 hours.

### Very High — no cost preview or hard budget

- Batch submission logs request count but not a cost ceiling.
- Force flags do not provide an exact call-count preview or require an explicit financial confirmation.

### Very High — research fallback is automatic

- Transcript and NPTEL failures automatically invoke Gemini generation.
- The fallback output is treated as research enrichment even though it is synthetic and not derived from the unavailable source.

### Very High — assessment amplification

- MCQ generation can make up to seven logical calls.
- Each logical call may make three transport attempts.
- Formula and short-answer generation add up to six more attempts.
- Worst-case upper bound: approximately 27 Gemini requests for one module.

### High — successful provider responses are not journaled

- Parsing, TTS, merge, or local write failures can discard successful billable responses.
- A rerun repeats the generation rather than recovering the raw result.

## Manifest and data integrity

- Two authoritative manifests exist.
- Semantic deduplication silently merges entries.
- Removed sources do not retire entries.
- Multiple curriculum versions have no authority model.
- Different schema generations are mixed.
- Artifact identity uses lossy title paths or truncated IDs.
- Current mapper-generated canonical fields are absent from the configured manifest.

## Caching and state

- Non-empty files are treated as completed.
- No prompt/model/source/upstream hashes exist.
- Corrupt state silently becomes empty.
- No schema versions or migrations exist for state files.
- No file locking protects concurrent manifest/state writes.
- Mermaid injection modifies notes without invalidating audio, assessment, viva, compiled, or video artifacts.
- Renderer state ignores note/audio changes.
- Deleted modules are not reconciled out of state or artifacts.

## Retry and fallback

- Most Gemini loops retry generic exceptions.
- Viva retries all Anthropic `APIError`s, including likely permanent request/auth failures.
- Jitter exists only in embedding retry logic.
- Retry counters do not persist across restarts.
- Batch polling has a time limit but no maximum error count.
- Some 429 waits can reach five minutes per attempt.

Provider errors should be classified as:

### Permanent, non-retryable

- Invalid request/schema.
- Authentication/authorization failure.
- Missing or retired model.
- Invalid batch ID.
- Unsupported option.
- Provider content-policy rejection where the request is unchanged.

### Transient, retryable within a strict budget

- 429 with valid retry metadata.
- 500/502/503/504.
- Connection reset.
- Bounded network timeout.
- Provider overloaded error.

### Uncertain

- Empty/blocked response.
- Malformed structured output.
- Partial batch result.
- Should be checkpointed and surfaced for explicit handling rather than repeatedly regenerated by default.

## Model configuration

- `gemini-2.5-flash` is currently valid but scheduled for shutdown on October 16, 2026.
- Current code comments still claim an 8,192-token Claude ceiling in several places while code uses 64,000.
- `jsonl_serializer.py` says synthesis splits modules where `suggested_chapters > 1`; active synthesis splits only above 15 subtopics.
- Curriculum mapping uses structured output; assessment and viva generation rely on prompt-only JSON.
- Claude batches allow up to 64,000 output tokens per ordinary note without a cost-derived per-module ceiling.
- No token-count preflight exists.
- Claude result extraction preserves only the first content block.
- `retrieve_batch`’s truncation marker still says “8,192-token output ceiling.”

## Educational quality

- No independent factual verifier or hallucination checker exists.
- No quality gate verifies mandatory sections, derivations, formulas, coverage, or citations.
- References are links/snippets, not claim-level citations.
- Gemini fallbacks synthesize content about unavailable URLs/videos.
- Assessments and viva banks can be generated from missing notes using only subtopics.
- Truncated synthesis is marked but still written and marked complete.
- Partial assessment payloads are accepted if any single generator succeeds.
- No manual approval boundary exists before dependent outputs.
- Note changes do not invalidate assessments, viva, audio, or video.

## Security

- `config/.env` and `credentials.json` are mode `755`, readable by other local users.
- `token.pkl` is correctly mode `600`.
- No top-level `.gitignore` was supplied.
- `pickle.load(token.pkl)` is unsafe if the token file can be replaced.
- `_safe_path` correctly enforces `VAULT_ROOT`, but no single containment policy governs all custom paths.
- FFmpeg uses argument arrays and avoids shell execution, but `drawtext` syntax does not fully escape colon, percent, brackets, or backslashes.
- Edge TTS sends generated educational text to an external service; this data flow is not documented.
- Manifest/log output can expose local source paths.
- Credential contents were deliberately not reproduced or tested.

## Reliability

- Batch results are not saved raw before transformation.
- Partial Claude batch errors are silently omitted.
- Chapter reassembly does not verify that every planned chapter returned.
- Truncated notes are marked complete.
- Audio part files can remain after failures.
- Missing vault files are skipped without durable failed-module state.
- The full guide proceeds before Claude retrieval.
- Reruns cannot reliably target only failed components because success and failure state is inconsistent across phases.

## Maintainability and documentation

- The guide claims approximately 564 modules; supplied manifests contain 274 and 142.
- The guide calls files “Final,” contradicting executable behavior.
- Cost/free-quota claims are presented as static despite account and pricing dependencies.
- The guide recommends live API health checks without a safe no-spend mode.
- Requirements use only lower bounds and are not reproducible.
- Documentation and comments describe multiple generations of chapter splitting.

---

# Section 7 — Already-correct protections

The following protections are genuinely present:

- Atomic temporary-file replacement is used for manifests, most states, vault files, assessment/viva JSON, compiled files, and registries.
- `_safe_path` rejects null bytes and files resolving outside `VAULT_ROOT`.
- Gemini non-search calls generally disable thinking and automatic function calling.
- Curriculum parsing uses a response schema.
- Mapper retries shrink source context instead of resending the same oversized prompt.
- Curriculum IDs include a hash suffix and exact duplicate IDs are absent in both supplied manifests.
- Gemini finish-reason and Claude stop-reason truncation checks exist.
- Batch polling has a 30-hour wall-clock bound.
- Drive downloads cache non-empty files and remove failed partial downloads.
- Dedup treats embedding 400/401/403/404 errors as permanent.
- Google OAuth token permissions are set to `0600` when newly written.
- External commands use argument arrays rather than `shell=True`.
- FFmpeg rendering has bounded timeouts.
- JSONL serialization itself does not submit a batch.
- Source curriculum files were all byte-distinct in the supplied folder; no identical SHA-256 duplicates were found there.
- Windows path conversion rejects empty input.
- The ingestion source-file reader rejects paths escaping the configured vault root.

These protections are useful but do not close the higher-level identity and financial-transaction gaps.

---

# Section 8 — Incomplete or misleading fixes

| Claimed fix | Remaining issue |
|---|---|
| “Per-file mapper cache” | State file is absent; cache has no parser/prompt/model/schema version |
| “Collision-proof IDs” | Eight-hex suffix is not collision-proof, and downstream filenames discard much of the ID |
| “Semantic dedup” | Silently merges potentially different curriculum versions |
| “Canonical subject” | Configured runtime manifest is not canonicalized |
| “Prerequisite context” | Missing from every configured manifest entry |
| “Claude ceiling corrected” | Comments and serializer documentation still describe old 8,192/splitting behavior |
| “Batch API cost protection” | No idempotent submission ledger |
| “Diagram cache” | Mermaid substring does not prove validity or freshness |
| “Assessment/viva cache” | Non-empty, collision-prone filenames are accepted without metadata verification |
| “Atomic state writes” | Atomicity does not help when financial commitment occurs before the write |
| “Permanent embedding errors” | Other provider call sites still retry permanent failures |
| “Source dedup” | Similarity does not establish curriculum authority or supersession |
| “Final pipeline guide” | Runs downstream phases before Claude retrieval and reports incompatible module counts |
| “One call per syllabus file” | Up to three calls occur on retries, and all overlapping root DOCX files are independently parsed |
| “Successful completion” | Many phases accept any non-empty or partial artifact as complete |

---

# Section 9 — Missing tests

No test files were supplied. The bundled environment also lacks `pytest`; safe test collection returned `No module named pytest`.

| Test | Risk protected | Status | Files | Mock needed | Priority |
|---|---|---|---|---|---|
| Crash after Claude acceptance | Duplicate paid batch | Missing | synthesis | Anthropic | P0 |
| Repeated submit while batch pending | Duplicate paid batch | Missing | synthesis | Anthropic | P0 |
| Unchanged full rerun = zero calls | API bleed | Missing | all provider phases | All providers | P0 |
| Permanent batch polling error stops | Quota bleed | Missing | synthesis | Anthropic | P0 |
| Vault filename uniqueness | Note overwrite | Missing | manifest/synthesis | No | P0 |
| Assessment/viva filename uniqueness | Wrong cached artifacts | Missing | assessment/viva | No | P0 |
| Manifest schema/version validation | Invalid paid inputs | Missing | mapper/all consumers | No | P0 |
| Removed source retires modules | Zombie entries | Missing | mapper | Gemini | P0 |
| Overlapping syllabus authority | Silent version merge | Missing | mapper | Gemini | P0 |
| Prompt/model/source invalidation | Stale outputs | Missing | all phases | Provider mocks | P0 |
| Successful response survives local failure | Repeat spend | Missing | enrichment/audio/assessment/viva | Provider mocks | P0 |
| Partial/truncated output not complete | Quality propagation | Missing | synthesis/assessment | Provider mocks | P0 |
| Error-class matrix | Retry amplification | Missing | all clients | Provider mocks | P1 |
| Corrupt state fails closed | Full regeneration | Missing | synthesis/render/mapper | No | P1 |
| Concurrent state writers | Lost updates | Missing | mapper/ingestion/synthesis | No | P1 |
| Path/symlink/null containment | Arbitrary reads | Missing | ingestion/bridge | No | P1 |
| Malicious token pickle | Code execution | Missing | config | No | P1 |
| FFmpeg title escaping | Local render failure/injection | Missing | renderer | Subprocess mock | P1 |
| Formula/section/coverage quality gate | Educational correctness | Missing | synthesis/assessment | Provider mocks | P1 |
| Force-mode preview | Unexpected broad spend | Missing | mapper/research/visual | Provider mocks | P1 |
| Missing batch chapters | Partial note completion | Missing | synthesis | Anthropic | P1 |
| Model retirement | Permanent retry storm | Missing | all provider phases | Provider mocks | P1 |
| Deleted module reconciliation | Stale state/artifacts | Missing | all phases | No | P1 |

---

# Section 10 — Recommended file changes

No corrective changes have been implemented.

## Existing files requiring correction

### `synthesis_gate.py` — P0

- Add a financial-transaction ledger.
- Persist exact request fingerprints and module sets.
- Reserve in-flight modules before submission.
- Reconcile ambiguous submissions with provider-side batch listings.
- Use collision-free custom IDs and artifact paths.
- Preserve raw batch results before transformation.
- Classify permanent polling errors.
- Verify all expected results/chapters.
- Do not mark truncated output complete.

**API-safety impact:** Critical.  
**Schema impact:** New batch/request state schema.  
**Test impact:** Crash, repeat submission, reconciliation, partial-result, and polling tests.

### `config.py` — P0

- Declare one authoritative manifest.
- Add shared schema version/constants.
- Add a shared provider error classifier and retry policy.
- Add model lifecycle/preflight metadata.
- Remove unsafe pickle storage or constrain it strongly.
- Enforce secret-file permission checks.

### `curriculum_mapper.py` — P0

- Add source authority, effective year, supersession, and structured provenance.
- Normalize grade/semester before ID generation.
- Stop silently carrying entries from removed or failed sources.
- Produce a candidate manifest and diff; require validated promotion.
- Version cache keys by model, prompt, schema, and extractor.
- Detect exact ID/hash collisions explicitly.

### `assessment_bank.py` — P0

- Use full module identity for output names.
- Validate cached `_meta.module_id`.
- Fingerprint notes, prompt, model, and schema.
- Checkpoint MCQ/formula/short-note components individually.
- Do not accept partial banks as fully complete.
- Add a hard call budget.

### `viva_command_center.py` — P0

- Use full module IDs in artifact paths.
- Validate cached metadata.
- Separate permanent/transient Claude errors.
- Require explicit fallback authorization/budget.
- Persist raw responses.
- Fingerprint note and prompt dependencies.

### `ingestion_researcher.py` — P0

- Add stage-level research caches.
- Persist each successful search/transcript/scrape result immediately.
- Add source hashes and exact provenance.
- Make synthetic fallback distinct and opt-in.
- Add exact call-count/cost preview.
- Prevent coarse `content` sentinel from representing every enrichment component.

### `manifest_repair.py` — P1

- Stop silent deletion/merging of semantic duplicates.
- Stop partial ID-prefix rewriting.
- Use a centralized ID/migration function.
- Emit a reviewed migration plan instead of directly rewriting authoritative data.

### `dedup_engine.py` — P1

- Key embeddings by content hash, model, dimensions, and task type.
- Update registries only after successful staging.
- Do not destructively quarantine near-duplicates without review.
- Detect stale paths and changed-in-place files.
- Cache/reuse handwriting extraction safely.

### `visual_blueprint.py` — P1

- Fingerprint note content, prompt, model, and diagram schema.
- Validate Mermaid syntax before marking complete.
- Store diagrams separately or update dependent hashes after note mutation.

### `audio_pipeline.py` — P1

- Persist the generated script before TTS.
- Reuse valid partial TTS output.
- Fingerprint note/script/voice/model.
- Track failed and completed lines.

### `domain_compiler.py` — P2

- Record input/manifest fingerprint.
- Remove or flag compiled domains no longer present.
- Verify included module count rather than reporting manifest count when files are missing.

### `overnight_batch_renderer.py` — P1

- Fingerprint note/audio/render options.
- Store durable failure information.
- Escape FFmpeg filter values correctly.
- Verify state against actual output.

### `windows_bridge.py` — P2

- Enforce configured root containment.
- Track source identity to avoid endlessly copying unchanged drop-zone files under new names.

### `jsonl_serializer.py` — P2

- Reuse actual synthesis request construction.
- Remove stale chapter-splitting documentation.
- Validate unique custom IDs and show exact request/token preview.

### `pipeline_guide_final.md` — P1

- Correct phase ordering.
- Correct manifest/module counts.
- Correct stale token-ceiling claims.
- Present cost as conditional.
- Remove unsafe generic live-health-check instructions.
- Document recovery and financial safeguards.

### `requirements.txt` — P2

- Add reproducible compatible bounds or a lockfile.
- Include the test tooling required for the safety suite.

### Secret files — P0

- Remove live secrets from distributable review copies.
- Rotate credentials if exposure cannot be ruled out.
- Restrict `.env` and OAuth credential files to the owner.
- Add ignore rules.

## New files genuinely required

### `manifest_schema.py` or `manifest_schema.json` — P0

Responsibility:

- Central manifest schema.
- Schema version and migrations.
- Strict validation.
- Canonical enums and field meanings.

This cannot cleanly remain duplicated implicitly across every phase.

### `api_ledger.py` — P0

Responsibility:

- Durable call intent.
- Request fingerprint.
- Provider/model.
- Request/batch ID.
- Submission and retrieval status.
- Raw result location.
- Reconciliation.

This is cross-cutting financial state and should not be independently reimplemented by each phase.

### Source governance registry/module — P0

Responsibility:

- Source hash.
- Authority.
- Academic year.
- Effective date.
- Superseded-by relationship.
- Selected/rejected status.
- Conflict records.

### `tests/` — P0

Responsibility:

- Provider-mocked financial safety.
- Manifest integrity.
- Cache invalidation.
- Crash recovery.
- Security regression tests.

A queue, database, workflow engine, or parallel-worker framework is not justified by the supplied code.

---

# Section 11 — Implementation order

1. Freeze paid phases and select the single authoritative manifest.
2. Add manifest schema validation and artifact-path uniqueness checks.
3. Implement the Claude submission/retrieval ledger and restart reconciliation.
4. Add shared permanent/transient provider-error classification and hard retry budgets.
5. Introduce content-addressed cache/dependency fingerprints.
6. Fix vault, assessment, viva, audio, and video artifact identities.
7. Add source authority/effective-year/supersession governance.
8. Make mapper regeneration diff-based and promotion-based; eliminate zombie carry-forward.
9. Preserve raw paid responses before parsing/rendering.
10. Add quality gates before downstream assessments/media.
11. Add provider-mocked P0 tests, including proof of zero calls on unchanged rerun.
12. Correct documentation.
13. Perform a controlled no-provider dry run.
14. Perform a small explicitly approved provider canary only after the prior steps pass.

Parallelization should not be introduced during these fixes. Claude Batch already supplies useful server-side concurrency.

---

# Section 12 — Open questions and unavailable evidence

- The supplied folder appears to combine multiple project versions. The 142-entry root manifest aligns with newer comments and documentation; the 274-entry configured manifest is what executable defaults actually consume.
- No synthesis state, renderer state, mapper state, batch IDs, raw batch results, completed vault notes, assessment files, or video outputs were supplied, so historical recovery behavior could not be observed directly.
- Bulk source/generated binaries were inventoried and curriculum files were hashed, but their academic authority cannot be inferred solely from filenames or byte content.
- No provider billing configuration, account tier, quota policy, spend limits, or historical usage logs were available.
- No source document is designated authoritative.
- No academic-year/effective-date policy was supplied.
- Provider-side batch existence and validity cannot be checked without making external API calls, which were prohibited.
- Secret artifacts are present and locally over-permissive, but whether the credentials are live was deliberately not tested.
- Some supplied paths and generated folders are review-copy artifacts; path-layout differences alone were not classified as production bugs.
- The official model review is time-sensitive: Gemini 2.5 Flash is active as of this audit but has an announced October 16, 2026 shutdown date.
- No prior Claude or architecture findings were requested or used, preserving the independence of this report.

---

# Final disposition

**Do not run the full pipeline or submit Claude batches until the P0 transaction, manifest-authority, and artifact-identity defects are corrected and covered by mocked restart/idempotency tests.**

The minimum safe gate before a paid run is:

1. one validated authoritative manifest;
2. zero artifact-path collisions;
3. durable idempotent batch submission;
4. permanent/transient error classification;
5. a call-count and cost preview;
6. raw response preservation;
7. tests proving that an unchanged rerun produces zero external model calls.
