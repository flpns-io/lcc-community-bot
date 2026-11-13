# Community Discord Bot

A Python bot built with `discord.py` application commands. It includes member moderation, server and user information, interactive polls, and announcement schedules persisted in SQLite.

## Directory structure

```text
discord-bot/
├── .env.example
├── .gitignore
├── README.md
├── requirements.txt
└── bot/
    ├── __init__.py
    ├── config.py
    ├── database.py
    ├── embeds.py
    ├── main.py
    └── cogs/
        ├── __init__.py
        ├── announcements.py
        ├── moderation.py
        ├── polls.py
        └── utility.py
```

The SQLite database is created automatically at `data/announcements.sqlite3` by default. The local `.env` file and database directory are ignored by Git.

## Commands

| Command | Purpose | Required permission |
| --- | --- | --- |
| `/kick member reason` | Kick a server member | Kick Members |
| `/ban member reason` | Ban a server member | Ban Members |
| `/clear amount` | Delete 1–100 recent messages | Manage Messages |
| `/ping` | Show Discord API latency | None |
| `/userinfo member` | Show account, join date, avatar, and roles | None |
| `/serverinfo` | Show server information | None |
| `/announce create channel interval_time message` | Create a schedule | Manage Server |
| `/announce list` | List schedules in this server | Manage Server |
| `/announce delete announcement_id` | Delete a schedule in this server | Manage Server |
| `/poll question option1 option2` | Create an interactive two-option poll | None |

### Announcement schedule formats

- Recurring: `30m`, `2h`, or `1d` (allowed range: 5 minutes to 365 days)
- One-time: `once:2027-01-01T18:00:00Z` (UTC, at least 30 seconds in the future)

The background worker checks every 15 seconds. Schedules survive restarts. A channel that was deleted or is no longer accessible is removed from the schedule. Repeated schedule messages are sent as embeds, and mention notifications are disabled to avoid accidental mass pings.

The SQLite lease prevents two bot processes from dispatching the same due row simultaneously. As with any external message API, a process crash after Discord accepts a message but before SQLite records success can cause that message to be retried after its lease expires. Run one bot instance for the simplest and most predictable operation.

Poll definitions and votes are stored in SQLite. Poll votes update immediately, voters can change their choice, and active poll buttons continue working after a bot restart. Polls close after 24 hours; closed poll data is removed after 30 days.

## Setup

### 1. Create the Discord application

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and select **New Application**.
2. Give it a name, then open **Bot** and select **Add Bot**.
3. Use **Reset Token** to generate a bot token. Keep it private; anyone with the token can control the bot.
4. In **General Information**, copy the **Application ID**.
5. Under **Bot → Privileged Gateway Intents**, enable **Server Members Intent**. The bot uses member data for user information. Message Content Intent is not needed.

### 2. Invite it to a test server

1. In **OAuth2 → URL Generator**, select the `bot` and `applications.commands` scopes.
2. Select the bot permissions it needs: **View Channels**, **Send Messages**, **Embed Links**, **Read Message History**, **Manage Messages**, **Kick Members**, and **Ban Members**.
3. Open the generated URL and add the bot to a test server where you can manage roles and permissions.
4. In **Server Settings → Roles**, move the bot's role above the roles it may need to moderate. Discord will not let the bot kick or ban members whose highest role is equal to or higher than its own.

### 3. Install and configure

From this directory, use Python 3.10 or later:

```bash
python -m venv .venv
source .venv/bin/activate       # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env
```

Edit `.env` and set:

- `DISCORD_TOKEN`: the bot token from the Developer Portal.
- `CLIENT_ID`: the application ID.
- `TEST_GUILD_ID`: optional server ID for immediate command updates while testing.
- `DATABASE_PATH`: optional database file path.

To copy a server ID in Discord, enable **User Settings → Advanced → Developer Mode**, then right-click the server and choose **Copy Server ID**.

### 4. Run it

```bash
python -m bot.main
```

With `TEST_GUILD_ID` set, slash commands are synced to that server for development. Remove it when ready to sync globally; global command propagation can take longer. Keep the process running for scheduled dispatches. Back up the SQLite database file if schedules need to be preserved during host migrations.

## Tests

From this directory, after installing `requirements.txt`, run the automated tests with:

```bash
python -m unittest discover -s tests -v
```

Announcement parser and SQLite persistence tests use temporary databases and do not connect to Discord.

## Operational notes

- Give moderators the specific Kick Members, Ban Members, or Manage Messages permissions they need. `/announce` requires Manage Server.
- The bot role must be high enough in the role hierarchy to moderate the selected members.
- `clear` is limited to 100 messages per command.
- Keep `.env` out of source control. If a token is exposed, reset it in the Developer Portal immediately.
- Run a single worker instance unless you deliberately manage the SQLite database and process lifecycle. The database uses WAL mode and atomic due-row leases.
