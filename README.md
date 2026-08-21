# Precision Vault Engine

PVE is a local-first, provider-neutral study-content controller. Its core installs without provider SDKs and defaults every live-provider switch to off.

```bash
python -m pip install -e .
pve doctor
pve providers
pve preview --manifest-checksum <sha256>
python -m pytest
```

Mutable operational truth is stored in SQLite/WAL under `.pve/`; large outputs are content-addressed under `artifacts/`. Historical `master_manifest.json` files are never promoted automatically. The current legacy scripts remain compatibility inputs and their paid entry points are fail-closed.

See [operator guide](docs/operator_guide.md), [recovery guide](docs/recovery_guide.md), [deployment guide](docs/deployment_guide.md), and [UAT checklist](docs/uat_checklist.md).
