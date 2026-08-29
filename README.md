# 🏫 School Discord Manager

A Discord bot for generating and managing a clean, role-driven school server for Moroccan secondary education.

## ✨ New compact architecture

The server no longer creates a channel for every class and every subject. That design grows too quickly and can hit Discord's 500-channel server cap. citeturn359286search0

The new structure is:

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
├── 📚 cours-1bac          ← Forum
├── 💬 questions-1bac      ← Forum
├── 📝 devoirs-1bac        ← Forum
└── 🇲🇦 préparation-régional

🔊 SALLES VIRTUELLES
├── 🔊 TC-classe
├── 🔊 1BAC-classe
└── 🔊 2BAC-classe
```

### How subjects are organized

Subjects are **Forum tags**, not channels. Discord Forum channels support organized posts and tags, with a current limit of 20 tags per Forum channel. citeturn359286search11turn359286search1

Classes are **roles**, not channels. A student is assigned one class role with `/assignstudent`; the role controls access to the appropriate level area.

This keeps the server small even when a school has many classes.

## ⚙️ Setup

Run:

```text
/setup
```

The wizard lets an administrator:

1. Select the levels present in the school.
2. Select the available filières for each level.
3. Choose the number of classes per filière (using the curriculum's predefined class names as defaults).
4. Review the configuration.
5. Build the server.

The configuration is saved locally and can later be rebuilt with:

```text
/build
```

Use:

```text
/status
```

to inspect the saved configuration and the compact architecture.

## 📚 Curriculum

The complete academic catalogue lives in `config/curriculum.py` under:

```python
CURRICULUM["niveaux"]
```

It contains the level, filière, class names and subjects. The catalogue does **not** duplicate BIOF variants as separate subjects; regular subject names are used once.

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
    └── (generated local JSON files)
```

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
```

Then configure `.env` and run:

```bash
python bot.py
```

Forum channels require Community to be enabled on the Discord server. citeturn359286search11

## 🤖 Commands

`/setup` — configure the school's levels, filières and classes.

`/build` — build/reconcile the saved compact server structure.

`/status` — inspect the saved configuration.

`/assignstudent` — assign a student to a generated class role.

`/assignteacher` — assign the `Professeur` role.

`/reportabsence` — publish a teacher absence announcement.

## 🧪 First test

For the cleanest test, create a **new empty Discord server** and invite the current bot there. The old test server may already be at Discord's channel cap because it contains the legacy per-class/per-subject structure.

After starting the bot:

```text
/setup
→ choose your levels
→ choose filières
→ choose classes
→ Construire le serveur
```

The important result to check is that you get a small number of level Forums and class roles instead of a long list of subject channels.
