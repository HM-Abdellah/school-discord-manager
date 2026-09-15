# 🏫 School Discord Manager

A Discord bot for generating and managing a clean, role-driven school server for Moroccan secondary education.

## ✨ Current architecture

Each stream is represented by a real Discord category. The stream name is therefore the category title rather than a fake writable/readonly channel.

```text
📘・TC・🔬 TCS
├── 📌-TCS・informations
├── 🗓️-TCS・emploi-du-temps
├── 📝-TCS・examens
└── 📚-TCS・Mathématiques / PC / SVT / ...
```

Each stream uses shared academic spaces. The bot does not create a Discord channel or role per class.

The builder is idempotent and protected by a per-guild mutation lock. Build and stream-add operations now pass through the transactional build boundary: managed identities are validated before mutation, canonical resources that are not in the managed registry are rejected instead of silently adopted, and resources created by a failed build are rolled back on a best-effort basis.

## 👥 Roles and permissions

Core roles:

- `Administration`
- `Prof`
- `Prof (F)`
- `Élève`

Stream roles are persisted by Discord ID in the guild configuration. Management authorization uses the configured Administration role ID, while dangerous maintenance paths are owner-restricted.

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

Previous academic years remain available for history/archive features, including controlled rollback of the active year.

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

`/resetserver` is restricted to the server owner and targets only resources recorded as School Manager managed resources. Unrelated Discord resources remain outside its scope.

## 🧱 Code organization

```text
bot.py
  └── loads runtime cogs in a deliberate order

cogs/
  setup.py                 interactive setup wizard
  server_v3.py             build/add/remove/status/year commands
  students.py              student assignment and history
  teachers.py              teacher/subject/absence commands
  admin.py                 admin dashboard + health checks
  command_fixes.py         assignteacherfull implementation
  removestream_fix.py      fail-closed stream removal
  section_aware_exam.py    section-aware exam workflow
  section_aware_timetable.py section-aware timetable workflow
  security_hardening_v3.py destructive reset boundary
  year_rollback.py         academic-year rollback workflow

services/
  permissions.py           authorization + hierarchy/preflight checks
  server_builder.py        idempotent Discord resource reconciliation
  build_transaction.py     transactional build + rollback orchestration
  build_guard.py           per-guild concurrency lock
  discord_ownership.py     managed-ID validation + canonical collision checks
  discord_registry.py      managed-resource registry helpers
  role_conflicts.py        shared student/teacher role conflict checks
  storage.py               SQLite authority + JSON cache persistence
  storage_recovery.py      cache recovery from SQLite
  audit.py                 audit-event persistence
```

The active runtime has one application-command owner per critical command. Shared logic belongs in `services/`; command cogs do not import other command cogs.

## 🧪 Tests and CI

GitHub Actions runs the test suite for pushes to `main` and pull requests targeting `main`, using Python 3.12 and 3.13.

The suite covers command registration, managed-resource ownership, transactional build rollback, permission edge cases, academic-year validation, storage durability, enrollment idempotency, destructive-operation boundaries, section handling, and concurrency semantics.

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
