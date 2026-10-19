---
name: sdm-resetserver-forensics
description: Use when /resetserver fails, partially deletes resources, differs from historical behavior, reports Forbidden/HTTPException, or must be restored to a proven stable version.
---

# School Discord Manager — `/resetserver` Forensics

This is a destructive operation in a real-world school bot. Diagnose first; never use a real guild as a debugging instrument. Preserve the intended narrow target scope and recovery data.

## Known starting evidence for the current incident

At the time this skill was added, the user reported this exact response after updating the bot and invoking `/resetserver`:

```text
❌ Reset interrompu : Forbidden. Aucun nom n'a été utilisé pour élargir le scope.
```

The inspected `cogs/security_hardening_v3.py` returned only the exception type in its broad failure message. Therefore, this output alone does **not** identify which API call failed or prove the specific cause. The inspected GitHub `main` ref was `446f39b614346ee1fc1fc82afbcfb527f5e4688d`; refetch the live ref before using it as a baseline. The deployed/installed bot version may differ from GitHub `main`.

Treat the reported error as a symptom, not as a diagnosis. Reconfirm all current evidence at the start of the investigation.

## Non-negotiable safety boundaries

- Do not invoke `/resetserver` yourself or trigger any remote destructive Discord action through an agent/tool.
- Do not ask for repeated retries. First establish whether a prior attempt partially deleted channels, categories, or roles, and whether local database state was cleared.
- Any live test must use a dedicated, verified test guild with no real school data and explicit authorization. Use the documented normal-user executor plus separate observer-bot model; never use a self-bot or user token.
- Preserve the existing authorization, exact confirmation requirement, persisted-ID target scope, managed-identity validation, archive/recovery behavior, and unmanaged-resource boundary unless evidence demonstrates a specific defect and a separate change is justified.
- Never expand targets by resource names, category names, similarity, or guesses. Never “fix” a Forbidden by automatically adding permissions or overwrite grants without identifying the failed operation and proving why the permission change is correct.
- Do not edit `main` directly. Use a branch and a reviewable PR.

## Investigation sequence

### A. Establish the runtime that failed

1. Fetch the current GitHub `main` head, inspect working-tree status if local access exists, and record the exact branch/commit used for investigation.
2. Establish how the running bot was deployed: local process, Docker image tag/digest, latest built image, and whether the bot was actually restarted after the update. Do not assume a successful pull/rebuild means the running process uses that code.
3. Inspect the active command owner and cog-loading path to confirm which `resetserver` implementation is registered at runtime. Check for duplicate/legacy command definitions and sync/restart assumptions.
4. If the exact deployed commit cannot be established, label that uncertainty explicitly.

### B. Reconstruct state after the failed attempt

Before another destructive attempt, determine from available logs and read-only observations:
- which managed channel/category/role IDs are still present in Discord;
- which IDs remain in the persisted managed registry;
- whether the pre-reset archive exists, is integrity-valid, and contains the expected pre-reset data;
- whether SQLite and the JSON cache still agree and whether the guild's logical records were cleared;
- whether unrelated/unmanaged resources remained untouched.

Do not infer state only from channel names or from the final user-facing message. If the state cannot be established safely, stop and report the missing evidence.

### C. Identify the exact failing operation

Read the current implementation and tests, not just documentation. Trace the reset in execution order, including:
- registry/ownership validation and role hierarchy checks;
- archive creation and lock acquisition;
- each channel deletion;
- category deletion and its empty-category guard;
- each managed-role deletion;
- database/cache cleanup;
- all exception handlers and follow-up messages.

`Forbidden` generally indicates Discord rejected a specific HTTP action, but the exception type alone is not enough. Determine the stage and target resource first. If existing logs do not retain it, propose a diagnostic-only change that records the operation and exact target ID/name plus safe HTTP status/API code; keep messages bounded and do not log tokens, interaction secrets, database content, or unrelated personal data. Add a focused unit test for the diagnostic path.

Only after the failing request is known, inspect the relevant Discord-specific constraints (for example bot permissions, role hierarchy, role type, channel access, category overwrites, resource state, and deployed version). Do not assume every Forbidden has the same cause.

### D. Search for the last *proven* good behavior

Inspect the full history of all relevant files, at minimum:
- `cogs/security_hardening_v3.py`
- `services/server_builder.py` when permissions or managed-resource creation may be relevant
- `services/storage.py` when archive/reset persistence is relevant
- `tests/test_security_hardening.py`
- `tests/test_reset_scope.py`
- `tests/test_phase_3_4_recovery_boundary.py`
- relevant CI workflow runs and merged PR diffs.

Use `git log --follow`, `git show`, and a full relevant-file diff from the candidate baseline to current. Determine what exactly made the old behavior work and what changed later. Treat prior user-observed behavior as important evidence, but confirm which commit and deployed runtime corresponded to it where possible.

Do not equate the oldest implementation, a previous merged commit, or a “restore” PR with the correct stable baseline automatically. Restoring text does not guarantee identical runtime behavior if other shared code, permissions, stored IDs, cached state, or deployment versions have changed.

### E. Fix only after evidence separates cause from correlation

Write down:
1. confirmed symptom and current deployed version;
2. exact failed operation/resource (or the precise reason it remains unknown);
3. ranked hypotheses and evidence for/against each;
4. last known-good candidate and why it is believed to be good;
5. smallest candidate correction;
6. regression test that proves the desired behavior;
7. risk/rollback plan for a partial reset.

Prefer diagnostic-only visibility when the cause is still unknown. Once root cause is established, implement the minimal correction and a regression test. Do not combine diagnostics, permission redesign, archive redesign, and rollback behavior in one patch.

## Verification and exit criteria

- Run focused reset/security tests first, then `python -m pytest -q`.
- Review the full diff and `git diff --check`; explicitly confirm no widening of the target set, permission escalation, secret leakage, unrelated edits, or lost recovery behavior.
- Check actual GitHub Actions for all configured Python versions and Docker gates; do not claim CI passed without viewing the run result.
- Keep live Discord verification separate from automated tests. Only use the dedicated test guild and documented E2E model.
- A successful unit test does not prove live reset works. A successful command response does not prove all intended resources were deleted or that persistence is consistent.

Do not close this incident until the target behavior is specified, the root cause is evidence-backed, automated regression checks pass, and any required live acceptance has been explicitly performed and observed. Report remaining gates if live verification cannot safely be completed.
