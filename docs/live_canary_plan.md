# Controlled Live Canary Plan

Do not run an `execute` command without fresh approval for that exact provider and scope. Credentials must already be present in the process environment and must never be placed in shell history. Each canary has one request, zero retries, a 60-second HTTP timeout, 128 maximum output tokens, a $0.05 controller budget, and a conservative $0.01 reservation. Enable only one provider.

The separate approval token is deliberately required in addition to provider switches. It records operator intent but does not replace fresh user approval.

## Gemini — WEB_GROUNDED_RESEARCH

Exact input: `In at most two sentences, define a PID controller and cite one authoritative web source.`

Expected fingerprint: `sha256:81185a7b071721be98d96961eeab3f63f6dbd267cf0b6b44f58d8a29d6435a77`

```bash
env PVE_STATE_DIR=/home/faiz-sheikh/precision_vault_engine/.pve/canary/WEB_GROUNDED_RESEARCH PVE_ARTIFACT_DIR=/home/faiz-sheikh/precision_vault_engine/artifacts/canary/WEB_GROUNDED_RESEARCH PVE_ALLOW_LIVE_PROVIDERS=0 PVE_ALLOW_GOOGLE_LIVE=0 PVE_ALLOW_NVIDIA_LIVE=0 PVE_ALLOW_OPENAI_LIVE=0 PVE_ALLOW_ANTHROPIC_LIVE=0 .venv/bin/python -m pve.canary preview --capability WEB_GROUNDED_RESEARCH --max-output-tokens 128 --budget-usd 0.05 --estimated-cost-usd 0.01

env PVE_STATE_DIR=/home/faiz-sheikh/precision_vault_engine/.pve/canary/WEB_GROUNDED_RESEARCH PVE_ARTIFACT_DIR=/home/faiz-sheikh/precision_vault_engine/artifacts/canary/WEB_GROUNDED_RESEARCH PVE_CANARY_APPROVAL=PVE_LIVE_CANARY_APPROVED PVE_ALLOW_LIVE_PROVIDERS=1 PVE_ALLOW_GOOGLE_LIVE=1 PVE_ALLOW_NVIDIA_LIVE=0 PVE_ALLOW_OPENAI_LIVE=0 PVE_ALLOW_ANTHROPIC_LIVE=0 .venv/bin/python -m pve.canary execute --capability WEB_GROUNDED_RESEARCH --max-output-tokens 128 --budget-usd 0.05 --estimated-cost-usd 0.01 --approval-token PVE_LIVE_CANARY_APPROVED
```

Raw response: `artifacts/canary/WEB_GROUNDED_RESEARCH/mod_canary/web_grounded_research/op_81185a7b071721be98d96961eeab3f63f6dbd267cf0b6b44f58d8a29d6435a77/raw/response.bin`

Parsed result: adjacent `parsed/result.json`.

## Nemotron Super — ROUTINE_GENERATION

Pending NVIDIA account/API access. Exact input: `Return JSON with one key named summary containing one sentence defining a PID controller.`

Expected fingerprint: `sha256:e7a1cabf522414486bcb5a2437ae6a31dfecd4d86d2c405c82274c6e3b1233bf`

```bash
env PVE_STATE_DIR=/home/faiz-sheikh/precision_vault_engine/.pve/canary/ROUTINE_GENERATION PVE_ARTIFACT_DIR=/home/faiz-sheikh/precision_vault_engine/artifacts/canary/ROUTINE_GENERATION PVE_ALLOW_LIVE_PROVIDERS=0 PVE_ALLOW_GOOGLE_LIVE=0 PVE_ALLOW_NVIDIA_LIVE=0 PVE_ALLOW_OPENAI_LIVE=0 PVE_ALLOW_ANTHROPIC_LIVE=0 .venv/bin/python -m pve.canary preview --capability ROUTINE_GENERATION --max-output-tokens 128 --budget-usd 0.05 --estimated-cost-usd 0.01

env PVE_STATE_DIR=/home/faiz-sheikh/precision_vault_engine/.pve/canary/ROUTINE_GENERATION PVE_ARTIFACT_DIR=/home/faiz-sheikh/precision_vault_engine/artifacts/canary/ROUTINE_GENERATION PVE_CANARY_APPROVAL=PVE_LIVE_CANARY_APPROVED PVE_ALLOW_LIVE_PROVIDERS=1 PVE_ALLOW_GOOGLE_LIVE=0 PVE_ALLOW_NVIDIA_LIVE=1 PVE_ALLOW_OPENAI_LIVE=0 PVE_ALLOW_ANTHROPIC_LIVE=0 .venv/bin/python -m pve.canary execute --capability ROUTINE_GENERATION --max-output-tokens 128 --budget-usd 0.05 --estimated-cost-usd 0.01 --approval-token PVE_LIVE_CANARY_APPROVED
```

Raw response operation: `op_e7a1cabf522414486bcb5a2437ae6a31dfecd4d86d2c405c82274c6e3b1233bf`; parsed result is adjacent under `parsed/result.json`.

## Nemotron Ultra — ENGINEERING_REASONING

Pending NVIDIA account/API access. Exact input: `Return JSON with one key named result explaining in at most three sentences why integral action removes steady-state error for a constant disturbance.`

Expected fingerprint: `sha256:ea34196d4923a1acbb99409491202789e185db1523d44ac1e262271c322e594f`

```bash
env PVE_STATE_DIR=/home/faiz-sheikh/precision_vault_engine/.pve/canary/ENGINEERING_REASONING PVE_ARTIFACT_DIR=/home/faiz-sheikh/precision_vault_engine/artifacts/canary/ENGINEERING_REASONING PVE_ALLOW_LIVE_PROVIDERS=0 PVE_ALLOW_GOOGLE_LIVE=0 PVE_ALLOW_NVIDIA_LIVE=0 PVE_ALLOW_OPENAI_LIVE=0 PVE_ALLOW_ANTHROPIC_LIVE=0 .venv/bin/python -m pve.canary preview --capability ENGINEERING_REASONING --max-output-tokens 128 --budget-usd 0.05 --estimated-cost-usd 0.01

env PVE_STATE_DIR=/home/faiz-sheikh/precision_vault_engine/.pve/canary/ENGINEERING_REASONING PVE_ARTIFACT_DIR=/home/faiz-sheikh/precision_vault_engine/artifacts/canary/ENGINEERING_REASONING PVE_CANARY_APPROVAL=PVE_LIVE_CANARY_APPROVED PVE_ALLOW_LIVE_PROVIDERS=1 PVE_ALLOW_GOOGLE_LIVE=0 PVE_ALLOW_NVIDIA_LIVE=1 PVE_ALLOW_OPENAI_LIVE=0 PVE_ALLOW_ANTHROPIC_LIVE=0 .venv/bin/python -m pve.canary execute --capability ENGINEERING_REASONING --max-output-tokens 128 --budget-usd 0.05 --estimated-cost-usd 0.01 --approval-token PVE_LIVE_CANARY_APPROVED
```

Raw response operation: `op_ea34196d4923a1acbb99409491202789e185db1523d44ac1e262271c322e594f`; parsed result is adjacent under `parsed/result.json`.

## OpenAI — FRONTIER_ESCALATION

Exact input: `Return JSON with one key named result containing a two-sentence explanation of derivative kick in PID control.`

Expected fingerprint: `sha256:dfe23f3aa2cafc924ab226e166e7e10abd750df76c180e2948f9ad8effb7bc28`

The canary uses the Responses API, `store=false`, low reasoning effort, and `gpt-5.6-sol`. Official model documentation lists Responses support and current $5/M input and $30/M output token pricing: <https://developers.openai.com/api/docs/models/gpt-5.6-sol>.

```bash
env PVE_STATE_DIR=/home/faiz-sheikh/precision_vault_engine/.pve/canary/FRONTIER_ESCALATION PVE_ARTIFACT_DIR=/home/faiz-sheikh/precision_vault_engine/artifacts/canary/FRONTIER_ESCALATION PVE_ALLOW_LIVE_PROVIDERS=0 PVE_ALLOW_GOOGLE_LIVE=0 PVE_ALLOW_NVIDIA_LIVE=0 PVE_ALLOW_OPENAI_LIVE=0 PVE_ALLOW_ANTHROPIC_LIVE=0 .venv/bin/python -m pve.canary preview --capability FRONTIER_ESCALATION --max-output-tokens 128 --budget-usd 0.05 --estimated-cost-usd 0.01

env PVE_STATE_DIR=/home/faiz-sheikh/precision_vault_engine/.pve/canary/FRONTIER_ESCALATION PVE_ARTIFACT_DIR=/home/faiz-sheikh/precision_vault_engine/artifacts/canary/FRONTIER_ESCALATION PVE_CANARY_APPROVAL=PVE_LIVE_CANARY_APPROVED PVE_ALLOW_LIVE_PROVIDERS=1 PVE_ALLOW_GOOGLE_LIVE=0 PVE_ALLOW_NVIDIA_LIVE=0 PVE_ALLOW_OPENAI_LIVE=1 PVE_ALLOW_ANTHROPIC_LIVE=0 .venv/bin/python -m pve.canary execute --capability FRONTIER_ESCALATION --max-output-tokens 128 --budget-usd 0.05 --estimated-cost-usd 0.01 --approval-token PVE_LIVE_CANARY_APPROVED
```

Raw response operation: `op_dfe23f3aa2cafc924ab226e166e7e10abd750df76c180e2948f9ad8effb7bc28`; parsed result is adjacent under `parsed/result.json`.

## Verification, unchanged rerun, and recovery

Immediately after the first request, set every live switch to `0`. Run the same `execute` command with its provider switch changed back to `0`; keep the identical state/artifact paths, capability, token limit, budgets, and approval token. The controller must reuse the successful raw checkpoint before live preflight. Expected output: `"reused": true`; provider attempt count remains `1`; no event is appended.

Replace `CAPABILITY` and its state/artifact path below with the exact canary value:

```bash
env PVE_STATE_DIR=/home/faiz-sheikh/precision_vault_engine/.pve/canary/CAPABILITY PVE_ARTIFACT_DIR=/home/faiz-sheikh/precision_vault_engine/artifacts/canary/CAPABILITY PVE_ALLOW_LIVE_PROVIDERS=0 PVE_ALLOW_GOOGLE_LIVE=0 PVE_ALLOW_NVIDIA_LIVE=0 PVE_ALLOW_OPENAI_LIVE=0 PVE_ALLOW_ANTHROPIC_LIVE=0 .venv/bin/python -m pve.canary verify --capability CAPABILITY --max-output-tokens 128 --budget-usd 0.05 --estimated-cost-usd 0.01
```

Expected first-call ledger transitions are exactly `INTENT_CREATED → RESERVED → CALLING → SUCCEEDED`; `attempts=1`, `event_count=4`, `raw_exists=true`, and `total_operations=1`. The unchanged rerun must leave those values unchanged.

If state is `AMBIGUOUS`, `CALLING`, `SUBMITTED`, or `RUNNING`, keep all switches OFF, preserve SQLite and raw artifacts, and do not rerun. Reconcile provider state manually using the recorded fingerprint/provider ID. If parsing fails after `SUCCEEDED`, repair parsing and rerun with switches OFF so the durable raw response is reused. Never delete the canary database as a retry mechanism.
