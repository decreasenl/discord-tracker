# Architecture

## Purpose

This document describes the intended technical structure of the application.
It implements the behavior in `SPECIFICATION.md` while following the direction
in the leading product document.

The architecture should remain simple enough for one community while keeping
external integrations and evolving rules replaceable.

## System Context

The application is a single Python service with persistent storage and
background scheduling. It is packaged and run with Docker.

It integrates with:

- **Discord API** for slash commands, permissions, polls, announcements and
  manager interactions.
- **Wise Old Man API** for OSRS identities, statistics, groups and
  competitions.
- **SQLite** as the authoritative store for application-owned state,
  configuration, history and audit records.

Discord is the primary user interface. Wise Old Man is the source of OSRS data
and the host for competitions, but neither external system replaces the
application’s own state.

## Application Boundaries

Discord interactions arrive through an outbound Gateway connection; responses
and integration calls use outbound HTTP. The initial service exposes no public
HTTP API and requires no public inbound port or separate login service.
Manager diagnostics and manual refreshes call the same application services as
scheduled work. Authorization is checked before these services are invoked.

The application should be separated into the following logical areas without
requiring independently deployed services:

- **Discord interface:** commands, interactions, messages and permission
  checks.
- **Member management:** member lifecycle and Discord/OSRS identity links.
- **Ranks:** rank rules, evaluation, explanations and overrides.
- **Activity:** Wise Old Man snapshots, progress and leaderboard calculation.
- **Competitions:** scheduling, polls, rotation, winner selection and external
  competition creation.
- **Configuration:** validated community settings.
- **Audit:** append-only records of important manager and automated actions.
- **Integrations:** isolated clients for Discord and Wise Old Man.

Business rules should not depend directly on Discord message objects or Wise
Old Man response formats. Integration clients translate external data into
application-owned models.

## Persistence

SQLite is the persistence engine. It fits the single-community, single-service
scope while providing relational constraints and transactional updates for
members, identities, ranks, events and audit records.

The persistent model should cover at least:

- Members and linked external identities.
- Rank definitions, evaluations and manual overrides.
- Activity snapshots and leaderboard periods/results.
- Event definitions, polls, candidates, votes or final poll totals, and event
  state.
- Cooldown history.
- Community configuration.
- Scheduled-job execution records.
- Audit records.

Database migrations must be versioned and run as an explicit deployment step
or a controlled startup step.

The SQLite database is stored on a persistent Docker volume in production and
must never live only in the container's writable layer. The data-access and
migration libraries remain implementation decisions.

The application should use one database-writing process. SQLite write-ahead
logging and a busy timeout should be enabled to support concurrent application
tasks safely, but SQLite is not intended to coordinate multiple application
replicas.

## External Integrations

Discord and Wise Old Man are accessed through isolated adapters. Their
responsibilities, data ownership and operational contracts are defined in
[INTEGRATIONS.md](INTEGRATIONS.md).

## Scheduling and State Machines

Scheduled work runs inside the application initially; a separate worker system
is not required unless operational experience justifies it.

Jobs must use persistent execution records and stable operation keys so a
restart or retry cannot create duplicate polls, snapshots or competitions.

Competition state should progress through explicit states such as:

`scheduled -> voting -> winner_selected -> competition_created -> active -> completed`

Exceptional terminal or holding states include `cancelled` and
`attention_required`. State transitions must be validated and recorded.

Time calculations use the configured community timezone. Persisted timestamps
should be stored in UTC, with the relevant timezone or period boundary retained
where needed for reproducibility.

## Configurable Rules

Rank and leaderboard rules should be expressed behind stable interfaces. The
initial implementation may use ordinary Python strategies configured by stored
parameters; a general-purpose rules language is not required.

Every calculated result should include an explanation or component breakdown.
Rule versions or sufficient input data must be retained when needed to explain
historical results after configuration changes.

Manual overrides are separate records layered over calculated values. They
must not destroy the underlying calculation.

## Reliability

Operations that call external APIs should distinguish between:

- Validation or permission failures, which are not retried.
- Temporary network, server or rate-limit failures, which may be retried with
  backoff.
- Ambiguous outcomes, which must be reconciled before repeating a create
  request.

Errors should include enough context for a manager or operator to act without
exposing tokens or other secrets. Health checks should distinguish application
availability from degraded external integrations.

## Security and Privacy

Discord and Wise Old Man credentials are supplied as runtime secrets and must
not be committed to the repository or stored in ordinary configuration tables.

The application should request only the permissions it needs. Management
authorization is enforced server-side for every operation and never inferred
only from whether a command is visible in Discord.

Stored personal data should be limited to what is needed for community
management. Logs and audit entries must avoid credentials and unnecessary
personal information.

## Deployment and Operations

Runtime configuration is defined in [ENVIRONMENT.md](ENVIRONMENT.md).
Production deployment, persistence, backups and recovery are defined in
[PRODUCTION.md](PRODUCTION.md).

## Initial Implementation Decisions Still Required

The first milestone uses `discord.py`, a direct `httpx` Wise Old Man adapter,
Python's `sqlite3` module and transactional schema versions via `PRAGMA
user_version`. Runtime dependencies are pinned in `requirements.lock` and the
Docker image uses Python 3.12. An in-process asyncio worker checks persisted
event state every 15 seconds and pauses ambiguous external writes for manager
review. Polls and competitions survive restarts through SQLite state.

Code is organized under `src/discord_tracker`: `bot/` owns Discord commands and
authorization, `services/` owns member operations, `integrations/` owns Wise Old
Man HTTP calls, and `storage/` owns SQLite. `config.py` handles configuration;
`__main__.py` owns lifecycle and `cli.py` owns operator tools. Tests live under
`tests/`. Additional feature modules will be created when implemented.

Remaining choices:

- Local-wall-clock recurrence across daylight-saving transitions (the current
  weekly schedule uses fixed 168-hour intervals).
- Production host and backup destination.
- Observability and alert delivery.

These choices should be recorded when made, preferably as short architecture
decision records when the trade-off is meaningful.
