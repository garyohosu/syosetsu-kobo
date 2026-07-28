# Canon finalization and mail lineage design

## Scope

This change completes only the two defects identified by instruction-14:

- crash-recoverable, version-scoped publication and SQLite finalization in `CanonManager.finalize()`;
- preservation of the originating AgentMail conversation, parent, and hop lineage through audit, approval, rejection, re-audit, and scene-planner handoff.

The existing canon workflow, adapters, artifact formats, and unrelated devloop/mail behavior remain unchanged.

## Publication protocol

`canon_publications` reserves one `(work_id, version)` and one publication per session under `BEGIN IMMEDIATE`. An unfinished reservation is reused on retry. Five final documents are generated into `canon/.staging/{publication_id}` and validated as a complete set before publication. The completed version directory is published with one same-volume directory replacement; existing final directories are never overwritten.

After publication, the final directory is validated from disk. One SQLite transaction inserts all five `canon_documents`, marks the publication completed, completes the canon session, and updates the work handoff state. If that transaction fails, no partial database commit is retained and a later `finalize()` resumes the same publication/version without republishing.

Recovery is state-driven: complete staging is resumed, incomplete staging is rebuilt in place, prepared publication is renamed, published publication proceeds to database finalization, and completed sessions are rejected. Any ambiguous filesystem/database mismatch raises a diagnostic error without deletion or overwrite.

## Mail lineage protocol

Audit completion sends `canon-auditor -> manager` using the current parent. The resulting message becomes `latest_mail_id`. Approval and rejection both derive `manager -> canon-updater` from that message, inheriting conversation ID, parent ID, and hop count. Rejection includes the reason and optional instruction path. After approval and finalization, the updater sends one `canon-updater -> scene-planner` handoff derived from the approval message, with all required paths and no duplicate unrelated notification.

Mail sends are recorded/idempotently checked after database finalization, so a retry cannot create a second finalized publication or duplicate handoff.

## Verification

Tests use dummy adapters, temporary SQLite databases, temporary stores, deterministic failure injection, and AgentMail rows. They cover version reservation, complete-set publication, interrupted staging/rename/DB recovery, transaction rollback, no overwrite, staging exclusion, idempotent mail retry, and complete parent/conversation/hop chains for approval, rejection, re-audit, and handoff.
