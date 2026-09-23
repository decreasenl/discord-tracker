# Production

## Purpose

This document defines the intended production stack, deployment model and
operational requirements for the application.

For implemented commands, Linux deployment instructions and current limitations,
see [SETUP.md](SETUP.md). This document also describes requirements for later
features that are not implemented in the initial milestone.

## Software Stack

The decided production stack is:

- **Python** for the application.
- **Docker** for packaging and runtime isolation.
- **SQLite** for application-owned persistent data.
- **Discord API** for the user and management interface.
- **Wise Old Man API** for OSRS statistics and competitions.

The Python, dependency and base-image versions must be pinned in the project
files. The initial implementation uses `discord.py`, `httpx`, Python `sqlite3`
and transactional SQLite schema versions. An asyncio worker handles persisted
competition schedules every 15 seconds.

Automatic application ranks use the read-only `config/ranks.yaml` policy and a
daily worker (03:00 community time by default). SQLite schema version 6 adds
clan joining dates, assigned/calculated ranks and evaluation history. Back up
SQLite before upgrading and back up the policy file alongside it. See
[RANKS.md](RANKS.md) for a preview-first rollout and data prerequisites.
The health check monitors both the competition and rank worker tasks.

## Deployment Model

Production runs as one application container and one application process. That
process handles Discord interactions and scheduled background work.

Only one production instance may write to the SQLite database. Horizontal
replicas are not supported by this deployment model. If future scale or
availability requirements demand multiple writers, changing the database and
coordination model requires an explicit architecture decision.

The container receives configuration and secrets at runtime. It runs as a
non-root user, has a restart policy and exposes a health check suitable for the
chosen hosting environment.

## Connectivity and Initial Setup

The service needs outbound connectivity to Discord and Wise Old Man. Discord
interactions use the Gateway; no public inbound port or application API is
required. Health checks may run locally inside the container.

Initialize server and access roles according to
[ENVIRONMENT.md](ENVIRONMENT.md#initial-setup-and-recovery). Bootstrap values
seed SQLite once; changing them does not reset stored settings on restart.
If managers lose access, the operator stops the service and uses the documented
local recovery command, then restarts and verifies access. That command and its
invocation must be implemented and documented before production launch.

## SQLite Storage

The database file is stored in a persistent volume or bind-mounted host
directory. It must not be stored only in the container layer because replacing
the container would lose the data.

Production SQLite configuration should include:

- Foreign-key enforcement.
- Write-ahead logging where supported by the mounted filesystem.
- A busy timeout appropriate for concurrent scheduled tasks and commands.
- Versioned schema migrations.
- Transactions around state changes and external-operation bookkeeping.

The database volume must use a local or otherwise SQLite-compatible filesystem
with reliable file locking. A network filesystem must not be assumed safe
without explicit validation.

## Startup and Shutdown

On startup, the application:

1. Validates required configuration without logging secrets.
2. Opens the SQLite database and validates its schema version.
3. Applies migrations only according to the chosen controlled migration
   policy.
4. Recovers incomplete scheduled operations.
5. Connects to Discord and starts scheduled work.

The service is not ready until configuration and database checks succeed.
Shutdown stops accepting new work, finishes or safely records in-progress
transactions, closes the Discord connection and closes the database cleanly.

## Health and Logging

The application emits structured logs to standard output and standard error.
Logs cover startup, shutdown, scheduled jobs, state transitions, management
actions and external API failures without exposing credentials.

Health reporting distinguishes:

- **Alive:** the process is running.
- **Ready:** configuration and SQLite are usable and startup recovery finished.
- **Degraded:** the application is running but Discord or Wise Old Man is
  temporarily unavailable.

Managers configure Discord operational alerts with `/set-alert-channel` and
verify delivery with `/test-alert`. SQLite retains undelivered alerts and the
worker retries with backoff. External host monitoring remains a hosting decision:
a stopped bot or unavailable host cannot deliver its own Discord alerts.

## Backups

The SQLite database is backed up on a defined schedule using SQLite's supported
backup mechanism or a transactionally consistent snapshot. Copying only the
main database file while it is live is not an accepted backup procedure.

Backups are stored outside the application container and separately from the
primary data volume. Retention, encryption and off-host destination are chosen
before production launch.

A backup is not considered operational until restoration has been tested. The
restore procedure must document how to stop the writer, restore the database,
verify integrity and restart the application.

## Updates and Rollback

Production images are immutable and versioned. An update consists of backing
up the database, pulling or loading the new image, applying the approved
migration process and replacing the container.

Application rollback is permitted only when the previous version is compatible
with the current database schema. Database migrations require an explicit
recovery plan; restoring a verified pre-update backup may be necessary for a
schema rollback.

## Security

The service runs with the minimum required Discord permissions and host
filesystem access. Secrets are injected at runtime and are excluded from images,
logs and repository files.

Access to the production host, data volume and backups is restricted to
operators. Dependency and image updates should be reviewed regularly, with
urgent security fixes deployed promptly.

## Production Readiness Checklist

Before the first production deployment:

- Pin application dependencies and the container base image.
- Create the production Discord application and grant minimal permissions.
- Configure the production guild, channels and manager roles.
- Confirm the Wise Old Man group and required API operations.
- Provision and protect the persistent SQLite data directory.
- Configure secrets and validate that logs do not expose them.
- Establish and test database backup and restoration.
- Configure health monitoring and an operator alert path.
- Test restart recovery and duplicate-prevention behavior.
- Document the update and rollback commands for the selected host.
