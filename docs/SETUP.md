# Setup and verification

Implemented: Discord Gateway connection, server/role authorization, SQLite
bootstrap and schema versioning, explicit account linking, statistics retrieval,
manual refresh, read-only diagnostics, audit records and operator backup/recovery.

Also implemented: archival/restoration/relinking with retained history, settings
commands, audit browsing, manual rank overrides, scheduled SOTW/BOTW polls,
weekly recurrence, voting, winner selection, Wise Old Man competition creation
and manager recovery commands.

Automatic E-Scapers ranks are implemented using calendar membership duration
and previous-month XP activity. Configure joining dates and review
[RANKS.md](RANKS.md) before enabling production evaluation. Leaderboard scoring
remains disabled. Live Docker/Discord/Wise Old Man verification is still required
before production use.
The other product documents describe the target application, not a claim that
all features already exist. This milestone supports one OSRS account per member
and manager-created links only. Existing OSRS players must be tracked in Wise
Old Man before linking.

## Create the Discord application

1. Open the [Discord Developer Portal](https://discord.com/developers/applications)
   and create an application. Use a test server for the first run.
2. On its **Bot** page, generate/reset the bot token and save it privately.
   Leave privileged intents disabled; this milestone uses guild events and
   the member information supplied with commands. Never paste the token in chat.
3. Under **Installation**, enable **Guild Install**. Select the `bot` and
   `applications.commands` scopes. Grant **View Channels** and **Send Messages**
   for the test channel. Also grant **Read Message History** in event channels
   for poll recovery. Administrator and Manage Roles are unnecessary.
4. Use the installation link to add the application to your test server.
   You need permission to manage that server. Leave **Interactions Endpoint
   URL** empty because this application receives commands over the Gateway.
5. In the server, create or choose two ordinary roles: for example **Bot User**
   and **Bot Manager**. Assign yourself Bot Manager. These are separate from
   the integration-managed role Discord automatically creates for the bot.
6. Enable **Developer Mode** in Discord user settings under **Advanced**.
   Copy the server ID and each role ID using their context menus.

The installation flow follows Discord's [official application setup guide](https://docs.discord.com/developers/tutorials/developing-a-user-installable-app).
Only server installation is used here. Gateway delivery is described in
[Discord's interactions overview](https://docs.discord.com/developers/interactions/overview).

## Configure

Copy `.env.example` to `.env` and fill in:

```dotenv
DISCORD_BOT_TOKEN=your-private-bot-token
DISCORD_GUILD_ID=your-server-id
DISCORD_BOT_ACCESS_ROLE_ID=your-bot-user-role-id
DISCORD_MANAGER_ROLE_ID=your-bot-manager-role-id
COMMUNITY_TIMEZONE=Europe/Amsterdam
SQLITE_PATH=data/community.sqlite3
WOM_GROUP_ID=
WOM_GROUP_VERIFICATION_CODE=
```

Use actual numeric IDs. `WOM_GROUP_ID` is optional; without it, provide an OSRS
name when running Wise Old Man diagnostics. The implemented player/group reads
and player refresh do not require a configured Wise Old Man secret. Group
competition creation requires `WOM_GROUP_VERIFICATION_CODE` from your group's
management controls. Never enter it in a Discord command. The secret must
correspond to the selected group; recreate the container after changing it.
The Discord application ID is discovered by the library from the bot login.

`.env` is ignored by Git and excluded from the Docker build context. On Linux,
restrict it to your operator account with `chmod 600 .env`. Compose explicitly
injects it into the container; the file is not baked into the image.

Server/role IDs, timezone and optional Wise Old Man group are stored in SQLite
on first successful setup. Later changes to those `.env` values do not override
the stored settings. The token remains a runtime setting.

## Run on Linux with Docker Compose

Install Docker Engine and the Compose plugin using the
[official instructions for your Linux distribution](https://docs.docker.com/engine/install/).
From the project directory, after creating `.env`:

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f --tail=100 bot
```

The image runs Python 3.12 as UID/GID 10001. The named `bot-data` volume mounts
at `/data`; Compose overrides `SQLITE_PATH` to `/data/community.sqlite3`.
The image creates `/data` with ownership for the bot user; verify permissions
if substituting an existing volume or host bind mount. Use local disk, not NFS.

The bot publishes no ports. It needs outbound HTTPS and a persistent Discord
WebSocket connection. Enable Docker at host boot through your Linux service
manager. `unless-stopped` restarts an exited container; an unhealthy health
check alone does not restart it. Logs rotate locally. Health checks report a
recent database/event-loop heartbeat and Gateway connection, not Wise Old Man
availability.

Only run one bot instance against this database. Keep development and
production credentials and volumes separate. Do not run a local Python bot
alongside the production container with the same credentials.

## Verify in Discord

1. `/status` should report that the bot is connected.
2. `/diagnostics integration:Discord` checks the current channel and connection.
3. `/diagnostics integration:Wise Old Man osrs_name:YOUR_OSRS_NAME` reads a player.
4. `/link member:@you osrs_name:YOUR_OSRS_NAME` creates the explicit account link.
5. `/stats` retrieves your linked player's statistics.
6. `/member member:@you` retrieves a member as a manager.
7. `/refresh member:@you` requests an upstream update and returns its result.

All replies are private. Test a user with only Bot User: `/stats` is permitted
but `/link`, `/member`, `/refresh` and `/diagnostics` must be rejected. A person
with neither role must be denied all commands. Discord may still show commands
that the application denies; server integration settings can further control
their visibility.

If a player is missing, start tracking them on Wise Old Man and retry the link.
An API lookup returns upstream data as it exists; `/refresh` explicitly requests
an update. Concurrent refreshes for one player share the same request. This
milestone enforces a 60-second in-process refresh cooldown, which resets after a
restart; upstream rate limits still apply. Failed requests can be retried by
running the command again. There is no persisted background refresh queue yet.

Player calls follow the [Wise Old Man player API](https://docs.wiseoldman.net/api/players/player-endpoints).

## Stop, update and back up

For member management, events and manual ranks, see the command reference below.

```bash
docker compose stop bot
docker compose up -d --build
```

Make a backup before upgrading. The following creates a consistent SQLite
backup in the volume and exports it to the host; choose a fresh filename:

```bash
docker compose exec bot tracker-admin backup /data/backup-2026-09-17.sqlite3
docker compose cp bot:/data/backup-2026-09-17.sqlite3 ./backup-2026-09-17.sqlite3
```

Move the exported backup into restricted, backed-up storage outside the project.
The copy remaining inside the volume is not a disaster-recovery backup. The
command refuses to overwrite an existing destination. It uses SQLite's online
backup API and checks integrity. Backup automation and off-host retention remain
operator setup tasks.

For restoration, stop the bot, preserve the existing volume, and restore the
verified backup into a fresh volume as `/data/community.sqlite3`, owned by UID
10001. Do not mix a restored database with old WAL/SHM files. Start against the
restored volume and verify settings, links and `/status`. This restoration
procedure must be exercised on the deployment host before production use.

Avoid `docker compose down --volumes`: it deletes the persistent database.

## Recover a manager role

With the bot stopped, replace the stored manager role with an ordinary role
that exists in the configured server:

```bash
docker compose stop bot
docker compose run --rm --no-deps bot tracker-admin recover-manager ROLE_ID --operator YOUR_NAME
docker compose up -d bot
```

Replace `ROLE_ID` with a numeric ID. This operation is audited and the role is
validated against Discord on startup. It requires host access and does not
temporarily open bot access to everyone.

## Community management commands

- `/archive discord_id:ID reason:TEXT` retains history and stops member refreshes.
  The numeric ID works even after someone has left Discord.
- `/restore member:@member reason:TEXT` reactivates a membership.
- `/relink member:@member osrs_name:NAME reason:TEXT` corrects a link and preserves
  the previous record. A player cannot be linked to two members, including
  archived members.
- `/settings`, `/set-role`, `/set-timezone` and `/set-group` manage non-secret
  settings. Assign yourself a replacement manager role before selecting it.
- `/audit` shows the latest eight actions privately.
- `/rank-set`, `/rank-clear` and `/rank` manage manual clan rank overrides with
  optional expiry. They do not change Discord roles or in-game ranks.
- `/member-joined` records a member's actual clan joining date.
- `/rank-rules` displays the file-based rules and daily schedule.
- `/rank-preview` explains an individual decision without applying it.
- `/rank-dry-run` previews all active linked members without applying changes.
- `/rank-summary` shows saved automatic run results; `last_upgrade:true` selects
  the last run with rank changes. Use `page` for additional members.
- `/rank-evaluate` applies the same evaluation used by the daily scheduler.
  See [RANKS.md](RANKS.md) for activity gates, protected staff and override behavior.

## Competition commands

1. Use `/set-group` and configure the group's verification secret in `.env`.
2. Use `/event-config` with `kind`, comma-separated Wise Old Man metric keys,
   `choices` and `cooldown_months`. Example skills: `attack,defence,strength,magic`.
   The default cooldown is 12 months; select another value where appropriate.
   Boss identifiers are finally validated by the upstream API during creation.
3. Use `/event-schedule` with the channel and ISO timestamps for `poll_open`,
   `poll_close`, `starts` and `ends`, including timezone offsets. Example:
   `2026-10-01T18:00:00+02:00`. All dates must be ordered and in the future.
4. `repeat_weekly:true` repeats every 168 hours. The local hour therefore shifts
   across daylight-saving transitions. Existing event dates and group IDs are
   not rewritten when community settings change.

The worker checks every 15 seconds. It selects random eligible options when
the poll opens and posts a numbered message. `/vote event_id:ID choice:NUMBER`
casts or changes one vote per user. Bot-access or manager roles grant voting
access; no linked account is required. Eligibility is checked when voting;
votes are not removed retroactively if a role changes.

Polls are managed by the bot rather than the native Discord poll widget.
`/competition` shows options and the competition link. Ties are resolved only
among the highest-voted options. Cooldown starts when a winner is selected;
cancellation does not remove it. The Wise Old Man group determines competition
participants, independently of who voted.

`/events` shows failures, deletion status and pending interventions.
`/event-winner` resolves an undecided poll. After uncertain competition creation,
the worker automatically searches the group's competitions and attaches a unique
match on title, group, metric and dates. New competitions include a unique marker
in their title, persisted before creation. The search is paginated and limited
to 200 records; an incomplete search never establishes uniqueness. Up to three
reconciliation attempts are made with delays before manager intervention.

`/event-reconcile` manually attaches an existing competition with matching
group, metric and dates, including for an already locally cancelled event.
`/event-retry` resumes after review; uncertain writes require confirmation that
no poll/competition was created externally before a new create request is
allowed. Never confirm this without checking the external system. Automatic
reconciliation never repeats the create request.

`/event-cancel` stops local automation and cancels the next recurrence if it has
not opened. By default it retains the external competition. To also delete it,
provide `delete_remote:true` and `confirm_competition_id:ID` matching the recorded
Wise Old Man competition. Deletion is irreversible. The bot verifies the remote
group, metric and dates before deleting, and uses the group's runtime secret.

Local cancellation is committed before the external request. If deletion fails
or times out, the private response explicitly reports partial completion, an
alert is queued and scheduling remains stopped. Repeat the confirmed cancellation
to check remote state: an already-absent competition counts as completed cleanup.
Interrupted deletions are retained for review after restart. Unknown competition
IDs must be reconciled before requesting deletion. Rescheduling currently means
cancelling and creating a replacement. Missed windows and empty polls pause for
manager review.

## Operational alerts and command feedback

Use `/set-alert-channel channel:#bot-alerts` to select a channel in the configured
server where the bot can view and send messages. Prefer a manager-only channel.
Use `/test-alert` to queue a test message and verify delivery.

Background failures and reconciliation outcomes go to this channel. Alert
messages include event IDs and recovery guidance, with mentions disabled.
Alerts are persisted in SQLite and retained through restarts. Delivery failures
retry with backoff up to one hour between attempts. Without a configured channel,
alerts remain queued and are logged. Setting a channel delivers that backlog.
An interrupted send may produce a duplicate alert, identifiable by its alert ID.

Commands acknowledge requests and return private results. They distinguish
failure from partial completion; if a reply itself cannot be delivered, an alert
advises checking `/audit` before repeating the action. Unexpected command errors
are logged without raw exception payloads or credentials. Command outcomes and
background transitions are recorded in SQLite. View logs with
`docker compose logs -f bot` and recent audit entries with `/audit`.

Creation follows the [Wise Old Man competition API](https://docs.wiseoldman.net/api/competitions/competition-endpoints).

## Run and test locally

With Python 3.12:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install -e '.[test]'
python -m pytest -q
python -m discord_tracker
```

On Windows use `.venv\Scripts\python.exe` instead of activating the Linux path.
Tests use temporary databases and mocked HTTP; they do not require a token or
write to Discord/Wise Old Man. Live Discord verification requires your setup.
