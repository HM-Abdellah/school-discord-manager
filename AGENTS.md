# School Discord Manager — Engineering Contract

This repository implements a Discord bot intended for real school operations and persistent school records. Treat destructive commands, permissions, recovery, persistence, and deployment as production-sensitive. Correctness and recoverability outrank speed.

## Required workflow

For bug fixes, regressions, security-sensitive work, persistence changes, destructive commands, and release decisions, apply the project's established ECC methodology and the relevant repository-local skill under `.agents/skills/`. Do not invent or rename the project's ECC terminology.

Use this evidence-led sequence:
1. Establish the actual repository state and requested scope.
2. Gather reproducible evidence and trace the relevant execution path.
3. Identify and justify the root cause; separate observations from hypotheses.
4. Choose the smallest change that addresses the demonstrated cause.
5. Add or update regression coverage, then run the appropriate checks.
6. Review the complete diff and side effects before proposing a merge.
7. Report exactly what was inspected, changed, and verified, including remaining uncertainty.

Do not treat conversation history, a prior assistant answer, a commit's age, or a user's hypothesis as a substitute for the current repository and test evidence.

## Repository-state and change control

- Start by inspecting the current branch/head and working tree (`git status --short --branch`, `git log -8 --oneline --decorate`) whenever a local checkout is available. Inspect relevant source, tests, configuration, workflows, and documentation before editing.
- If only a remote GitHub view is available, do not claim to have inspected the local working tree or run local tests.
- Never overwrite unknown local work. Do not silently discard user changes, reset branches, force-push, or push directly to `main`.
- Make scoped changes on a dedicated branch and propose them in a pull request. Inspect the final diff; keep unrelated cleanup and feature additions out of a bug fix.
- Use the current repository tree as the source of truth. Historical commits are evidence to compare, not automatically correct targets.

## Production safety invariants

- Never expose, copy into source, print, or commit secrets, real Discord tokens, private environment files, or runtime databases/archives.
- Never automate a normal Discord user account or use a self-bot/user token.
- Do not execute destructive Discord actions against a production or unverified guild. Live E2E belongs in a dedicated test guild, with the documented normal-user executor and separate observer-bot model.
- For destructive behavior, define the precise target set, preconditions, partial-failure behavior, recovery point, and postconditions before changing implementation.
- Fail closed when managed identity, ownership, permissions, or recovery state is ambiguous. Never broaden destructive scope by matching names or guessing intended resources.
- Do not expand permissions or add automatic permission overwrites as a shortcut. Prove which operation is failing and justify any permission change separately.
- Do not repeatedly retry a partially completed destructive operation without first assessing its current state and recovery procedure.

## Project-specific checks

- The CI workflow runs `python -m pytest -q` on Python 3.12, 3.13.2, the latest available Python 3.13 patch, and Python 3.14. Docker build/runtime smoke checks and Docker Compose validation run in the Python 3.14 job. Use the actual workflow as the source of truth if it changes.
- Automated unit tests are not equivalent to live Discord E2E. The live E2E process in `e2e/README.md` is a separate operational acceptance gate.
- Destructive-resource boundaries, SQLite authority and archive/recovery behavior, authorization, resource ownership, and concurrency are core contracts. Add regression tests when modifying them.
- Prefer narrow targeted tests during diagnosis, then the full suite and relevant CI/deployment checks before merge.
- A command callback completing is not proof that its business operation succeeded; use observed application state and explicit assertions.

## Reporting standard

State the current base/head, relevant evidence, root-cause confidence, proposed/actual change, tests actually run and their results, CI status, and unverified live gates. Never claim a fix, test, review, or release gate succeeded unless the corresponding evidence is available.

Load `.agents/skills/sdm-ecc-engineering/SKILL.md` for engineering changes, `.agents/skills/sdm-resetserver-forensics/SKILL.md` for `/resetserver` incidents, and `.agents/skills/sdm-production-release-gate/SKILL.md` for release/readiness work.
