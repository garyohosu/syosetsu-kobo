# Canon finalization and mail lineage design

## Scope

This change completes only the two defects identified by instruction-14:

- crash-recoverable, version-scoped publication and SQLite finalization in `CanonManager.finalize()`;
- preservation of the originating AgentMail conversation, parent, and hop lineage through audit, approval, rejection, re-audit, and scene-planner handoff.

The existing canon workflow, adapters, artifact formats, and unrelated devloop/mail behavior remain unchanged.

## Publication protocol

`canon_publications` reserves one `(work_id, version)` and one publication per session under `BEGIN IMMEDIATE`. An unfinished reservation is reused on retry. Five final documents are generated into `canon/.staging/{publication_id}` and validated as a complete set before publication. The completed version directory is published with one same-volume directory replacement; existing final directories are never overwritten.

After publication, the final directory is validated from disk. One SQLite transaction inserts all five `canon_documents`, marks the publication completed, completes the canon session, and updates the work handoff state. If that transaction fails, no partial database commit is retained and a later `finalize()` resumes the same publication/version without republishing.

Recovery is state-driven: complete staging is resumed, incomplete staging is rebuilt in place, and a prepared publication branches according to filesystem state. When only a complete staging directory exists, it is published by one same-volume rename. When staging is absent and the reserved final directory already contains a complete, validated, metadata-matching five-file set, the rename is treated as already completed and database finalization resumes with the same publication and version. A published publication proceeds to database finalization, and completed sessions are rejected.

If staging and final both exist, the final directory is incomplete, or the reserved work, version, or fixed-reference metadata does not match, the operation stops with a diagnostic error. It must not delete, replace, overwrite, choose one side, or reserve a new version.

### Prepared publication recovery after rename

When a publication is `prepared`, staging is absent, and the reserved final version directory contains a complete, validated set of the same five files matching the publication's work, version, and fixed reference metadata, recovery treats the directory as already renamed. It proceeds directly with the same publication/version's database finalization; it does not reserve or generate a new version. If staging and final both exist, the final directory is incomplete, or either directory's contents do not match the reservation, recovery stops with a diagnostic inconsistency error and does not delete, replace, or overwrite anything.

## Mail lineage protocol

Audit completion sends `canon-auditor -> manager` using the current parent. The resulting message becomes `latest_mail_id`. Approval and rejection both derive `manager -> canon-updater` from that message, inheriting conversation ID, parent ID, and hop count. Rejection includes the reason and optional instruction path. After approval and finalization, the updater sends one `canon-updater -> scene-planner` handoff derived from the approval message, with all required paths and no duplicate unrelated notification.

Mail idempotency uses the deterministic operation keys described below rather than an unspecified send-and-record check. A retry first looks up the operation key, reuses the single existing message when present, sends only when no matching message exists, and stops as ambiguous if multiple matching messages are found.

### Mail send crash recovery and idempotency

Every finalized handoff uses a deterministic idempotency key `(publication_id, event_type)` stored with the AgentMail record under a unique constraint. Before sending, the workflow checks that key; an existing single record supplies its `mail_id` and is recorded in the session/publication state without sending again. No record means the message is sent with the key, and the resulting `mail_id` is recorded. If more than one record is found for a key, recovery stops as an ambiguous duplicate rather than choosing or sending another message. This closes the crash window between successful `AgentMail.send()` and recording its ID.

Approval and rejection use the same rule with an operation-specific event key tied to the source audit message (for example, `(session_id, approval:{audit_mail_id})` or `(session_id, rejection:{audit_mail_id})`). Their database state transition is committed before mail delivery is attempted; if delivery fails, retry reuses the same key and parent message, sends only the missing operation message, records its ID, and never creates a new conversation or a second approval/rejection message. A completed mail state is adopted on retry, while conflicting or multiple records remain a diagnostic error.

## Verification

Tests use dummy adapters, temporary SQLite databases, temporary stores, deterministic failure injection, and AgentMail rows. They cover version reservation, complete-set publication, interrupted staging/rename/DB recovery, transaction rollback, no overwrite, staging exclusion, idempotent mail retry, and complete parent/conversation/hop chains for approval, rejection, re-audit, and handoff.
