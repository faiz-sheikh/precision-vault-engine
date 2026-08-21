# Recovery Guide

- Run `pve doctor` and inspect `Store.reconcile_startup()` before starting a worker.
- `CALLING`, `SUBMITTED`, `RUNNING`, or `AMBIGUOUS` provider operations require reconciliation; never automatically resubmit.
- A parser or validator failure after provider success resumes from the checksummed raw checkpoint.
- A corrupt SQLite database fails integrity checks. Restore only from a backup whose checksum and restore test passed.
- A stale worker is rejected by its fencing token. The new worker reconciles provider state before doing work.
- Missing/corrupt legacy JSON state is not interpreted as empty work.
