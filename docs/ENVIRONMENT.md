# Runtime Environment

## Purpose

This document defines how runtime environments are configured and kept
separate. Production operation is described in [PRODUCTION.md](PRODUCTION.md).

## Environments

The project has two initial environment types:

- **Development** uses a developer-controlled Discord application, test guild,
  local SQLite database and non-production Wise Old Man resources where
  practical.
- **Production** uses the community's production Discord application, guild,
  channels, roles, Wise Old Man group and persistent SQLite database.

Development must not post messages, create polls or create competitions in
production resources. Environment separation is achieved with different
credentials, Discord identifiers, Wise Old Man identifiers and database files.

A separate staging environment is optional and should only be introduced when
the operational need justifies it.

## Configuration Sources

Configuration has four categories:

1. **Secrets** are injected as environment variables or Docker secrets.
2. **Bootstrap settings** needed before the database can be read are supplied
   through environment variables.
3. **Community settings** managed by the application are stored in SQLite and
   changed through authorized management commands where supported.
4. **Automatic rank policy** is stored in `config/ranks.yaml`, mounted read-only
   by Compose. Rank thresholds, activity gate and daily run time belong here,
   not in `.env` or SQLite. Restart after changes; see [RANKS.md](RANKS.md).

Environment variables are deployment inputs. A local `.env` file may be used
for development or supplied to Compose through `env_file` in production, but
must be excluded from version control. A committed
`.env.example` should document variable names with safe placeholder values.

## Required Runtime Values

The implementation should expose stable names for at least:

- Runtime environment name.
- Discord bot token (the library discovers the application identifier).
- Discord guild identifier used for initial server setup.
- Initial bot-access and manager role identifiers.
- Path to the SQLite database file.
- Community timezone.
- Log level.
- Wise Old Man connection settings or credentials if the selected API
  operations require them.

`WOM_GROUP_VERIFICATION_CODE` is required for group competition creation. It is
a runtime secret and is never stored in the settings table. It must match the
group selected by `WOM_GROUP_ID` at bootstrap or `/set-group` afterward.

Channel IDs, role IDs, event schedules and other community behavior belong in
application-managed configuration once the database is initialized.

The bootstrap names below are fixed. Other variable names should be documented in `.env.example` when the
configuration layer is implemented. The application must validate required
values at startup and fail with a clear error that does not reveal secrets.

## Initial Setup and Recovery

The initial configuration contract uses `DISCORD_BOT_TOKEN` for the secret,
`DISCORD_GUILD_ID` for the server, `DISCORD_BOT_ACCESS_ROLE_ID` for normal access,
`DISCORD_MANAGER_ROLE_ID` for management access, `COMMUNITY_TIMEZONE` for the
initial timezone and `SQLITE_PATH` for the database location. Role and server
IDs are configuration, not credentials. No manager passwords are required.

On an uninitialized database, validate these settings and verify the server
and roles through Discord before atomically storing the community configuration
in SQLite. Until setup succeeds, do not enable commands or scheduled work.
Failed validation leaves setup incomplete and provides an operator error.

After initialization, SQLite owns the server, role and timezone settings.
Bootstrap values do not overwrite them on restart. The token and database path
remain runtime inputs. Managers can update role settings through authorized,
validated commands; changing servers requires an explicit operator procedure.

If a deleted or misconfigured manager role prevents access, an operator stops
the bot and uses a local recovery command to replace the stored role ID. This
command must be provided during implementation and record the old/new IDs and
operator action in the audit history. On restart, validate the replacement
against Discord before enabling commands. Never grant broad access as a
fallback for invalid configuration.

## Filesystem Paths

Inside the production container, mutable application data is stored beneath a
single documented data directory mounted from persistent storage. The SQLite
database and any SQLite sidecar files remain within that directory.

Application code and container images are immutable. Logs are written to
standard output and standard error rather than relied upon as files inside the
container.

Development uses a separate local data path. Database paths must never default
to a location inside the source tree without being ignored by version control.

## Time

The application stores timestamps in UTC. Community schedules and calendar
boundaries are evaluated using the configured IANA timezone, initially
`Europe/Amsterdam` unless the community chooses another value.

The host and container clocks must use reliable time synchronization. Changing
the community timezone is a managed configuration change and must not rewrite
historical timestamps.

## Configuration Precedence

Bootstrap configuration is resolved in this order, from highest to lowest
precedence:

1. Explicit process environment variables.
2. Local `.env` values loaded without overriding existing process variables.
3. Safe application defaults for non-secret values.

Secrets have no built-in defaults. Application-managed community settings are
read from SQLite and are not silently overridden by unrelated environment
variables.

## Security

Production secrets must be readable only by the deployment mechanism and the
application process. Diagnostic output may state that a value is present or
invalid but must never print the value itself.

Production database files and backups contain community data and must be
protected with filesystem permissions appropriate to the production host.
