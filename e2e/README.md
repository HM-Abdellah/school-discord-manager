# School Discord Manager — E2E Harness

This directory contains the Phase 3 live E2E observation layer.

## Design

The harness deliberately separates **execution** from **observation**:

- A real Discord test user executes `/setup`, `/build`, `/addstream`, `/removestream`, and later commands from the E2E matrix.
- The harness logs in with a bot token that has access to the dedicated test guild and captures the real Discord resource state.
- `diff` compares snapshots by Discord ID, so renames are reported as changes rather than mistaken for new resources.

The harness does **not** automate a normal user account. Using a self-bot/user token to invoke slash commands is intentionally out of scope.

## Environment

```text
E2E_BOT_TOKEN=<observer bot token>
E2E_GUILD_ID=<dedicated test guild id>
```

Keep these values outside Git. The observer bot only needs the guild access required to read channels and roles.

## Usage

Capture the guild before a real user action:

```bash
python -m e2e.runner capture --output .e2e/before.json
```

Execute the slash command manually in the dedicated test guild, then capture again:

```bash
python -m e2e.runner capture --output .e2e/after.json
python -m e2e.runner diff --before .e2e/before.json --after .e2e/after.json
```

The resulting diff is the raw Discord-side evidence that the Phase 3 scenario consumed. Later Phase 3 steps can layer scenario-specific assertions on top of the same snapshots.
