🛡️ Commander Livia

A Discord bot for Guild Wars 2 event organization and WvW squad management.

Commander Livia is a modular, event-focused Discord bot designed to bring structure, strategy, and style to your Guild Wars 2 community.
She manages RSVPs, builds balanced WvW squads, and helps commanders coordinate players effortlessly.

✨ Features
🎉 Event RSVPs

Create and manage guild events with simple slash commands.

Members respond with one click — yes or no.

Livia automatically updates event summaries across multiple channels.

⚔️ WvW Squad Builder

Players select class → elite spec → WvW role.

Livia builds balanced squads of 5 (up to 10 squads total).

Includes intelligent role balancing and tier-based ranking logic.

Warns commanders when key roles (Primary, Secondary, Tertiary support) are missing.

📊 Meta & Build Management

Each specialization can have editable roles and tier rankings (S+ → C).

Edit directly via Discord or bulk import/export a CSV file.

Built-in “Build Helper” (/suggest) to list top specs for any role.

💾 Persistent Data

Stores RSVP and WvW participation data in lightweight JSON files.

Auto-cleans inactive data after configurable days (default: 7).

Exports full event lists as CSV with one command.

🧭 Administration Tools

/event start, /add_channel, /reset, /export, /clear_all

/wvw_event start, /squad_analyze, /show_stats

Optional DM setup for meta editing and role customization.

🧱 Tech Stack

Language: Python 3.11+

Framework: discord.py 2.x

Storage: JSON (SQLite planned)

Deployment: Native or Docker-Compose compatible

⚙️ Future Roadmap

SQLite migration for faster queries & stability

REST API (Flask/FastAPI) for event/squad data

Engagement features (badges, leaderboards, reminders)

“Build Helper” expansion with external GW2 resources

🔐 Ethical Use

Commander Livia may not be used by, or for, individuals or groups promoting hate, fascism, or authoritarian agendas — including Donald Trump, Vladimir Putin, or neo-Nazi movements.
This project supports inclusive, respectful, and creative gaming communities only.

🧑‍💻 Developer

Author: Tim Palm
Language: Python
License: MIT

For bug reports, feature requests, or collaboration, feel free to open an issue or pull request.

❤️ Special Thanks

A heartfelt thank-you to Madpie for discovering early bugs and helping shape Livia into the reliable commander she is today.
