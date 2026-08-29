# 🏫 School Discord Manager

An open-source Discord bot for generating and managing a structured school server.

The project is designed around a Moroccan secondary-school structure with:

- Tronc Commun
- 1ère Année Bac
- 2ème Année Bac
- Configurable streams (filières)
- Configurable number of classes per stream
- Subject forums with educational tags
- Private class areas
- Teacher-only private area
- Regional and national exam channels
- Virtual classroom voice channels
- Student and teacher role management

## ✨ Main idea

The school structure is **configured interactively inside Discord** instead of hard-coding the number of classes.

An administrator runs:

```text
/setup
```

Then selects the levels, the streams available at the school, and the number of classes for each stream. The configuration is saved locally and can be built with:

```text
/build
```

## 📁 Project structure

```text
school-discord-manager/
├── bot.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
│
├── config/
│   ├── __init__.py
│   └── curriculum.py
│
├── cogs/
│   ├── __init__.py
│   ├── setup.py
│   ├── server.py
│   ├── students.py
│   └── teachers.py
│
├── services/
│   ├── __init__.py
│   ├── permissions.py
│   ├── server_builder.py
│   └── storage.py
│
└── data/
    └── (generated local JSON files)
```

## 🔐 Token security

Never put your real Discord bot token in GitHub.

Create a local `.env` file from `.env.example`:

```env
DISCORD_TOKEN=YOUR_REAL_DISCORD_BOT_TOKEN
```

`.env` is ignored by Git through `.gitignore`.

## 🛠️ Local installation

Python 3.8+ is required. Python 3.13 is supported by the project dependencies.

### 1. Clone the repository

```bash
git clone https://github.com/HM-Abdellah/school-discord-manager.git
cd school-discord-manager
```

### 2. Create a virtual environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 4. Configure the bot token

```powershell
Copy-Item .env.example .env
```

Open `.env` and set your own Discord bot token.

### 5. Discord Developer Portal

Enable these privileged gateway intents for the bot:

- Server Members Intent
- Message Content Intent

Invite the bot to your test server with the permissions required to create and manage the configured channels and roles. For initial development, Administrator is the simplest option.

Forum channels require the appropriate Discord server/community configuration.

### 6. Run

```bash
python bot.py
```

## 🤖 Commands

### `/setup`

Interactive setup wizard. Select:

1. A school level
2. The streams available at your school
3. The number of classes for each selected stream
4. Confirm the configuration

### `/build`

Builds the saved configuration on the current server. Existing matching resources are reused when possible.

### `/status`

Shows the saved configuration and estimated number of classes and subject forums.

### `/assignstudent`

Assigns a student to a class role.

### `/assignteacher`

Assigns the `Professeur` role to a member.

### `/reportabsence`

Publishes a teacher-absence announcement in the configured school announcements area.

## 📚 Curriculum catalogue

The stream and subject catalogue is stored in `config/curriculum.py`.

The catalogue defines **what exists** academically. It intentionally does not define the number of classes: class counts are selected through `/setup`.

## 🧭 Roadmap

- Persistent database instead of JSON storage
- Better student onboarding
- Teacher-to-class assignment
- Automatic absence notifications to affected classes
- Timetable management
- More granular subject permissions
- Audit logs
- Web dashboard
- Automated tests and CI

## 📜 License

This project is open source. Add the license that matches how you want the project to be reused.
