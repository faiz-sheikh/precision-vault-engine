# Controlled Live Canary Plan

Do not run these until the operator supplies credentials and explicit approval for the exact scope. Use a fresh empty state directory and a cost/request cap of one. Disable retries for each canary.

| Capability | Provider/model | Scope | Maximum requests | Expected tokens | Raw checkpoint |
|---|---|---|---:|---:|---|
| WEB_GROUNDED_RESEARCH | Google / `gemini-3.7-flash` | One two-sentence grounded definition | 1 | 100–400 | `artifacts/mod_canary/research/<operation>/raw/response.bin` |
| ROUTINE_GENERATION | NVIDIA / `nvidia/nemotron-3-super-120b-a12b` | One short structured paragraph | 1 | 100–300 | `artifacts/mod_canary/synthesis/<operation>/raw/response.bin` |
| ENGINEERING_REASONING | NVIDIA / `nvidia/nemotron-3-ultra-550b-a55b` | One small derivation | 1 | 150–500 | `artifacts/mod_canary/reasoning/<operation>/raw/response.bin` |
| FRONTIER_ESCALATION | OpenAI / `gpt-5.6-sol` | One tiny escalation only if desired | 1 | 100–300 | `artifacts/mod_canary/escalation/<operation>/raw/response.bin` |

For each canary: create and record the canonical fingerprint and `INTENT_CREATED` event, set global plus one provider switch only for the process, execute once, turn switches off, and repeat the identical controller request. The repeat must reuse the raw checkpoint with zero network calls. Pricing is intentionally not asserted here; fetch and approve a current pricing snapshot before execution, and set the budget cap from that evidence.
