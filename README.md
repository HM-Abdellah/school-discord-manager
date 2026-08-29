# 🏫 School Discord Manager

A Discord bot for generating and managing a clean, role-driven school server for Moroccan secondary education.

## ✨ Compact Discord architecture

The server does **not** create a channel for every class or every subject. Subjects are Forum tags and classes are roles, keeping the Discord structure small and readable.

```text
🏢 INFORMATIONS & ADMINISTRATION
├── 📢 actualités
├── 👨‍🏫 absences-professeurs
├── 📊 résultats-et-annonces
├── 🎓 opportunités-post-bac
└── 🏆 concours-et-activités

👨‍🏫 ESPACE PROFESSEURS
├── 💬 discussion-professeurs
└── 🔊 réunion-professeurs

📚 1ÈRE ANNÉE BAC
├── 📢 annonces
├── 🗓️ organisation
├── 📚 cours-1bac          ← Forum + subject tags
├── 💬 questions-1bac      ← Forum + subject tags
├── 📝 devoirs-1bac        ← Forum + subject tags
└── 🇲🇦 préparation-régional

🔊 SALLES VIRTUELLES
├── 🔊 TC-classe
├── 🔊 1BAC-classe
└── 🔊 2BAC-classe
```

## 📅 Academic years and student history

School data is separated from Discord channels. The bot now creates a local SQLite database at `data/school.db` containing:

- Academic years (`2025/2026`, `2026/2027`, `2027/2028`, ...)
- Students and their Discord IDs
- Classes for each academic year
- Enrollments with start/end dates
- Transfer history when a student changes class
- `left_school` status when a student leaves the institution

A student is **not deleted** when leaving. Their previous years, classes and enrollment history remain available.

### Year workflow

At the start of a new school year:

```text
/newyear 2027/2028
/setup
/build
```

`/newyear` makes the selected year active. `/setup` then uses that active year instead of hard-coding the current year. Previous years remain in the database.

### Student workflow

```text
/assignstudent
```
assigns the student's Discord role and creates/updates the current enrollment. If the student changes class, the previous enrollment is closed and a new one is created.

```text
/studenthistory
```
shows the student's academic history.

```text
/leavschool
```
marks the student as having left the institution without deleting their history.

## ⚙️ Setup

Run:

```text
/setup
```

The wizard lets an administrator:

1. Select the levels present in the school.
2. Select the available filières.
3. Choose the real number of classes per filière.
4. Review the configuration and active academic year.
5. Build the server.

Class names are generated as `Classe 1`, `Classe 2`, etc. so the school can configure any realistic number of classes without changing the curriculum catalogue.

## 📚 Curriculum

The academic catalogue lives in `config/curriculum.py` under:

```python
CURRICULUM["niveaux"]
```

It contains the level, filière, classes and subjects supplied by the school structure. It does not create Discord channels for each subject.

## 📁 Project structure

```text
school-discord-manager/
├── bot.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── config/
│   ├── __init__.py
│   └── curriculum.py
├── cogs/
│   ├── __init__.py
│   ├── setup.py
│   ├── server.py
│   ├── students.py
│   └── teachers.py
├── services/
│   ├── __init__.py
│   ├── permissions.py
│   ├── server_builder.py
│   └── storage.py
└── data/
    ├── guild_config.json
    └── school.db
```

`data/` remains local and should not be committed with real school data.

## 🔐 Token security

Never commit your real Discord token to GitHub.

Create `.env` from `.env.example`:

```env
DISCORD_TOKEN=YOUR_REAL_DISCORD_BOT_TOKEN
DISCORD_GUILD_ID=YOUR_TEST_SERVER_ID
```

## 🛠️ Local installation

```bash
git clone https://github.com/HM-Abdellah/school-discord-manager.git
cd school-discord-manager
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python bot.py
```

## 🤖 Commands

- `/setup` — configure levels, filières and class counts for the active academic year.
- `/build` — build/reconcile the compact Discord structure.
- `/status` — inspect the saved school configuration.
- `/newyear 2027/2028` — create and activate a new academic year.
- `/years` — list saved academic years and show the active year.
- `/assignstudent` — assign a student to a class and record enrollment history.
- `/studenthistory` — view a student's academic history.
- `/leavschool` — mark a student as having left without deleting their history.
- `/assignteacher` — assign the `Professeur` role.
- `/reportabsence` — publish a teacher absence announcement.

## 🧪 First test

For the cleanest Discord test, use a **new empty Discord server**. The old test server may already contain the legacy channels and may have reached Discord's channel cap.

After `git pull origin main`:

```text
python bot.py
/setup
→ choose levels
→ choose filières
→ choose class counts
→ Construire le serveur
```

Then test the lifecycle:

```text
/assignstudent
/studenthistory
/newyear 2027/2028
/years
```

The important result is a compact Discord server plus a local database that can survive class changes and future academic years.
