# Release 0 operations

Release 0 is offline-only. Direct paid legacy CLIs fail closed before creating a
provider client unless `PVE_ALLOW_LEGACY_PAID_EXECUTION` exactly equals
`I_HAVE_FRESH_APPROVAL`. Setting that variable is only a technical rollback; it
does not replace the fresh approval required by C1.

## Backup and recovery

The initial snapshot is under `_migration_backups/20260729T111313+0530/`.
Credential-bearing files are excluded from its distributable `snapshot/` and
held separately under `_protected_credentials/20260729T111313+0530/` with
restrictive permissions. Verify `SHA256SUMS` before recovery. Restore only to a
new staging directory, compare checksums, then atomically replace an approved
target. Safe mode must remain enabled during recovery.

## Manifest status

Both legacy manifests are immutable candidates. Neither is authoritative. The
142-entry count is diagnostic only; source-registry authority, provenance,
effective dates, supersession decisions, validation, a promotion preview, and
fresh C2 approval are still required.

## Credential warning

Known credential-bearing files were permission-restricted and excluded from
distributable material without displaying their contents. Rotation or deletion
requires fresh C5 approval. Operators should rotate any credential that may have
previously been exposed or tracked.

## Rollback

To reverse only the legacy CLI guard, set the exact environment value documented
above for the single approved process. Unset it immediately afterward. Never use
the rollback without a fresh, scope-specific provider approval.
