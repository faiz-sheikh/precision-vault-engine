# User-friendliness acceptance record

## Fresh-user walkthrough

- **Scenario A — zero setup:** onboarding explains PVE, health and provider presence; Demo Mode creates the deterministic PID Control pack; all six outputs appear in My Materials; provider calls remain zero.
- **Scenario B — normal study user:** task pages ask only for module, workflow, and depth; the preview explains outputs, services, bounded requests, unknown cost, and reuse before execution.
- **Scenario C — failure:** account/quota, rate-limit, authentication, model-access, and ambiguous states have plain-language explanations, preserved-work and provider-effect copy, and policy-sensitive retry guidance.
- **Scenario D — expert owner:** Expert Mode exposes jobs, ledger, budget/routing, artifact identity, failure evidence, diagnostics, manifest and sources without mutating state.

## Usability findings addressed

- Replaced engineering-area-first navigation with task-first navigation.
- Added five-step onboarding and a zero-key first action.
- Added clear Demo Output labels and provider-call statements.
- Bound structured commands to config, manifest, dependencies, preview ID, routing capabilities, and bounded operations.
- Added local natural-language parsing and local material search.
- Added understandable progress stages, failure copy, contextual help, and Doctor status.
- Kept technical evidence in Expert Mode while keeping authority in existing controller gates.

The interface uses descriptive labels and text alongside status icons, avoids color-only meaning, keeps forms narrow enough for laptop/tablet layouts, and uses standard Streamlit keyboard-accessible controls.
