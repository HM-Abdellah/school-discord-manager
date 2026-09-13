# 🏫 School Discord Manager

A Discord bot for generating and managing a clean, role-driven school server for Moroccan secondary education.

## ✨ Current architecture

The server uses a **real Discord category for every stream**. The stream name is therefore the category title rather than a fake writable/readonly channel.

```text
📘・TC・🔬 TCS
├── 📌-TCS・informations
├── 🗓️-TCS・emploi-du-temps
├── 📝-TCS・examens
└── 📚-TCS・Math / PC / SVT / ...
```

Each stream has one shared academic space for its classes/groups. No Discord channel or role is created per class.

The builder is idempotent and uses a per-guild build lock. Re-running `/build` reconciles the configured School Manager resources instead of formatting the server.

## 👥 Roles and permissions

Core roles:

- `Administration`
- `Prof`
- `Prof (F)`
- `Élève`

Stream roles are recorded by Discord ID in the guild configuration. Management authorization uses the configured Administration role ID, while the server owner remains an owner-only management path for dangerous operations.

Destructive operations resolve targets from the persisted managed-resource registry rather than expanding their scope from matching names.

## 📅 Academic years and student history

Academic data is stored separately from Discord resources in SQLite at `data/school.db`.

Student enrollment is idempotent: assigning a student to the same active stream again does not create a duplicate active enrollment, and SQLite enforces one active enrollment per student.

Example yearly workflow:

```text
/newyear 2027/2028
/setup
/build
```

Previous academic years remain available for history/archive features.

## ⚙️ Commands

Setup and server maintenance:

```text
/setup
/build
/addstream
/removestream
/status
/years
/newyear 2027/2028
/rollbackyear
```

Teacher/student administration:

```text
/assignteacher
/assignteacherfull
/assignsubjectteachers
/assignstudent
/studenthistory
/leave_school
/reportabsence
```

Scheduling and exams:

```text
/set_timetable
/setexam
```

Administration:

```text
/adminpanel
/serverhealth
```

Dangerous maintenance:

```text
/resetserver RESET SCHOOL MANAGER
```

`/resetserver` is restricted to the server owner and targets only resources recorded as School Manager managed resources. Unrelated channels and categories are intentionally outside its scope.

## 🧱 Code organization

```text
bot.py
  └── loads the cogs in a deliberate order

cogs/
  setup.py                 interactive setup wizard
  server_v3.py             build/add/remove/status/year commands
  students.py              student assignment and history
  teachers.py              teacher/subject/absence commands
  admin.py                 admin dashboard + health checks
  command_fixes.py         active compatibility replacements
  security_hardening_v2.py fail-closed security overrides
  edge_case_hardening.py  isolated config-consistency compatibility patch
  year_rollback.py         academic-year rollback workflow

services/
  permissions.py           authorization + hierarchy/preflight checks
  server_builder.py        idempotent Discord resource reconciliation
  storage.py               JSON configuration + SQLite persistence
  build_guard.py           per-guild concurrency lock
  audit.py                 audit-event persistence
```

`command_ui.py` and the older unused security-harden­ing layer are not part of the active extension set. The hardening cog is loaded after the normal command implementations so its strict versions are the effective application commands.

## 🧪 Tests and CI

GitHub Actions runs the test suite on pushes to `main` and pull requests, using Python 3.12 and 3.13.

The suite covers command registration, managed-resource scope, permission edge cases, academic-year validation, storage durability, enrollment idempotency, destructive-operation boundaries, and edge-case configuration repair.

## 📚 Curriculum

The academic catalogue lives in `config/curriculum.py`. Only streams present in that catalogue are accepted by the bot.

## 🔐 Token security

Never commit the real Discord bot token. Create `.env` from `.env.example`:

```env
DISCORD_TOKEN=YOUR_REAL_DISCORD_BOT_TOKEN
DISCORD_GUILD_ID=YOUR_TEST_SERVER_ID
```

Local `.env` and runtime SQLite/JSON data are ignored by Git.

## 🛠️ Local installation

```bash
git clone https://github.com/HM-Abdellah/school-discord-manager.git
cd school-discord-manager
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python bot.py
```

The bot uses Discord application commands and does not enable the message-content intent because the current architecture does not require message-content events.
