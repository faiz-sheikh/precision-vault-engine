# PVE Operator Guide

1. Keep `.env` outside Git and start with every `PVE_ALLOW_*` switch set to `0`.
2. Run `pve doctor`, inspect provider routes, and generate a preview bound to the current manifest/configuration.
3. Submit work with a unique command idempotency key through the controller. Refreshing the dashboard is read-only.
4. Treat `AMBIGUOUS`, manifest conflicts, corrupt state, checksum mismatches, and stale previews as manual-review conditions.
5. Back up with `pve backup <path>` and require `restore_test_result: PASS`.

Never use legacy provider scripts for production execution. Legacy Anthropic support is compatibility-only and live-disabled.
