# PVE model-quality benchmark

This benchmark measures route-specific content quality after transport qualification. It is fully
offline: previewing cases, validating outputs, generating review forms, and scoring preserved
checkpoints do not construct provider adapters or access provider credentials.

## Routes and cases

The initial live benchmark is deliberately bounded to twelve calls with no retry: three cases each
for `ROUTINE_GENERATION`, `QUALITY_REVIEW`, `ENGINEERING_REASONING`, and `ENGINEERING_REVIEW`.
Super serves the first two routes; Ultra serves the latter two. Cases cover correct, defective, and
false-positive-control examples rather than only easy prompts. Every future execution must use the
normal PVE fingerprint, approval, reservation, checkpoint, and reuse flow.
`pve.quality_benchmark.canonical_benchmark_operation` is the sole benchmark request definition;
the canary CLI is not an identity authority.

The canonical case data is in `config/quality_benchmark_cases.json`. Reference facts are compact
concepts and calculations, not preferred prose. They establish correctness without rewarding
surface imitation.

## Rubrics and scoring

Each route has its own dimensions in `pve.quality_benchmark.RUBRICS`. Every dimension uses the same
anchored integer scale:

- 0: unusable or fundamentally wrong
- 1: major defects requiring replacement
- 2: partially correct but materially deficient
- 3: acceptable with limited corrections
- 4: strong, correct, and useful
- 5: excellent with no meaningful defect

Reviewers record dimension evidence, comments, identity, timestamp, unsupported and total claim
counts, and hard failures. Automated checks run first and retain observed evidence for structure,
required/prohibited concepts, length, and array cardinality. Semantic scores remain unfilled until
a human reviewer evaluates the response; no model judge is used.

## Hard failures and thresholds

Malformed required structure, materially false engineering claims, unsafe recommendations,
invented sources, failure to answer, constraint contradiction, or a mathematically invalid required
result overrides the average score.

A case passes only when deterministic validation passes, there is no hard failure, normalized mean
score is at least 0.75 (3.75/5), and unsupported-claim rate is at most 5%. A route passes only when
all its cases pass, its median normalized score is at least 0.80, and its worst case is at least
0.70. These conservative thresholds require consistently strong work and cannot be rescued by one
excellent response.

## Reproducibility and API bleed

The benchmark version is the `sha256:` digest of the exact case-file bytes. Prompt identity uses
the namespace `pve-benchmark-prompt-v1` over benchmark version, case ID, and exact ordered messages;
it never includes time, execution order, or random identifiers. The provider-operation fingerprint
binds provider/model, exact request and messages, response format, temperature, top-p, output
ceiling, required JSON schema, validation-contract version, reference/constraint/check hashes, and
runtime configuration hash.

Planning reads `provider_operations` by that fingerprint. Only an unambiguous `SUCCEEDED`
projection with a present SHA-256-valid raw checkpoint is reusable. Failed operations require a new
planned call under the applicable failure policy; ambiguous or corrupt evidence blocks for
reconciliation. A caller-provided list of completed case IDs is never benchmark completion
authority.

The initial execution approval is immutable and binds the benchmark version, case, complete
canonical contract, one request, exact token budget, optional cost ceiling, accountable actor,
purpose, reason, and timezone-aware expiry. Approval creation and preview perform no provider I/O.
Consumption, attempt creation, and reservation happen atomically, so duplicate workers can produce
at most one transport. Re-evaluating the same valid checkpoint is a zero-call operation.

The preserved Super and Ultra canaries validate only the harness: both pass envelope, completion,
JSON-object, and required-field checks offline. Their simple prompts do not cover the route rubrics,
so they receive no human quality score and cannot qualify a route by themselves.
