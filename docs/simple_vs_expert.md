# Simple, Demo, and Expert modes

**Simple Mode** is the default student experience. It presents study tasks, materials, progress, health, and help without exposing operational internals.

**Demo Mode** uses the same guided interface with bundled deterministic outputs and zero external provider calls.

**Expert Mode** adds read-only operational views for jobs, the provider ledger, budgets and routing, manifest/source governance, artifact dependencies, quality evidence, failures, and diagnostics.

Changing mode changes visibility only. It never enables a provider, raises a budget, approves a retry, promotes a manifest, changes source authority, or grants execution authority. Dangerous actions still require their controller authorization and explicit confirmation.
