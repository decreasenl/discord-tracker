# Integrations

## Purpose

This document explains why the application connects to external systems, how
those connections are used and which system owns each kind of information.
Endpoint-level details should be verified against the current official API
documentation during implementation.

The initial application integrates only with Discord and Wise Old Man. Direct
integration with the RuneScape Hiscores is out of scope.

Automatic ranks read `GET /groups/:id/bulk-gained` with explicit UTC `startDate`
and `endDate` for the previous community calendar month. This endpoint returns
the full group without pagination; records are matched to linked players by ID.
Only valid `overall` XP observations qualify for the activity gate. There are no
upstream rank writes or automatic snapshot refreshes in this workflow. See
[RANKS.md](RANKS.md) and the
[official group gains contract](https://docs.wiseoldman.net/api/groups/group-endpoints#get-group-bulk-gains).

## Ownership of Information

| Information | Authoritative system |
| --- | --- |
| Discord users, roles, channels and messages | Discord |
| OSRS player statistics and Wise Old Man competition results | Wise Old Man |
| Clan membership used by the application | Application |
| Discord-to-OSRS account links | Application |
| Clan ranks, overrides and rank history | Application |
| Poll and event workflow state | Application |
| Configuration and audit history | Application |

External data may be cached locally for reliability and historical reporting,
but a cache does not transfer ownership to the application. Application-owned
decisions must be persisted and must not be reconstructed solely from Discord
messages or current Wise Old Man responses.

## Discord

### Why We Connect

Discord is the application's primary interface because it is where members and
managers already interact. It provides identities, role membership, slash
commands, polls and community notifications.

### How We Connect

The application runs a Discord bot authenticated with a bot token supplied as
a production secret. It connects through a supported Python Discord library
and uses Discord application commands and events.

Interactions are received over an outbound persistent Discord Gateway
WebSocket connection. Command registration, responses and message operations
use outbound requests to Discord's HTTP API. No public interaction endpoint,
application HTTP API or separate login service is required. The bot must remain
running to receive Gateway events.

This transport follows Discord's [interaction delivery documentation](https://docs.discord.com/developers/interactions/overview)
and [interaction response documentation](https://docs.discord.com/developers/interactions/receiving-and-responding).

The integration is responsible for:

- Registering and handling slash commands.
- Reading stable user, guild, role, channel and message identifiers.
- Checking the configured server and bot-access or manager role on every
  command, with manager access required for privileged actions.
- Creating polls and publishing event or competition announcements.
- Associating Discord interactions and messages with application event state.
- Returning confirmations and actionable errors to users.

Discord names and display names are not stable identifiers and must not be used
as database keys. The application stores numeric Discord IDs where a durable
reference is required.

### User Identity and Display Names

The Discord user ID is the permanent application identifier for a Discord
user. Usernames, global display names and server nicknames may change and are
presentation data only.

When displaying a person, the application uses the first available value in
this order:

1. Server nickname.
2. Global display name.
3. Discord username.

Names cached for presentation should be refreshed when the application receives
current member information from Discord. A name change must not create a new
member or break an existing account link.

A Discord username, display name or server nickname must never be treated as an
OSRS account name. Discord users and OSRS accounts are linked explicitly and
stored using their respective stable identifiers where the external system
provides one.

### Permissions

The bot should request only the gateway intents and guild permissions required
by implemented features. Command visibility may improve the user experience,
but authorization for management actions must always be checked by the
application when a command is executed. The manager role grants normal access
as well as privileged access and is independent of clan rank. Direct messages
and commands from other servers are rejected. Discord provides user identity;
the application has no separate user login. See
[SPECIFICATION.md](SPECIFICATION.md#roles-and-permissions) for the access rules.

### Manager Diagnostics

Managers may inspect connection status and validate configured server, channel
and bot permissions. These checks return private, sanitized results and do not
post test content. They cannot prove message delivery without an explicit send
operation. A Discord outage is also reported through operator logs because
Discord commands may be unavailable.

### Failure Behavior

Discord calls use explicit timeouts and respect rate-limit responses. Temporary
failures may be retried with bounded backoff. Create operations must use stored
state to prevent a retry from producing duplicate polls or announcements.

If a required channel, role or message no longer exists, the affected workflow
enters an attention-required state and managers receive an actionable error in
an available management channel or log.

## Wise Old Man

### Why We Connect

Wise Old Man is the application's OSRS data and competition service. It avoids
reimplementing player tracking and competition calculation while providing the
statistics and competition capabilities needed by the community.

### How We Connect

The application uses a dedicated Wise Old Man HTTP adapter implemented with
`httpx`. Business logic consumes validated player records and
does not depend on raw Wise Old Man response objects.

The integration is responsible for:

- Looking up players and retaining stable external identifiers when available.
- Requesting player updates or retrieving current player data when required.
- Retrieving activity and progress used by member views and leaderboards.
- Reading relevant group information.
- Creating Skill of the Week and Boss of the Week competitions.
- Retrieving competition status and results.
- Translating API errors, unavailable data and rate limits into application
  outcomes.

### Data Freshness

Managers may run a read-only connectivity/group/player check or explicitly
request a linked member's data refresh. These use the same adapter, rate limits
and error handling as scheduled operations. The adapter distinguishes fetching
existing data from requesting an upstream update; a successful read does not
prove that competition creation is authorized. Operation status and sanitized
failures are exposed through the manager tools defined in the specification.

Every locally stored Wise Old Man snapshot records when it was retrieved and,
where supplied, when the upstream data was updated. User-facing results should
make stale or unavailable data apparent when it could affect interpretation.

The application must not assume that requesting an update immediately produces
fresh statistics. Scheduling should allow for upstream processing and should
reconcile the result before calculating time-sensitive outcomes.

### Competition Creation

Before creating a competition, the application persists the selected option,
schedule and a stable local operation identifier. After creation, it stores the
Wise Old Man competition identifier and URL.

If the response is ambiguous, the application attempts to reconcile the
operation before trying another create request. This prevents duplicate Wise
Old Man competitions after timeouts or restarts.

### Failure Behavior

Requests use explicit timeouts, bounded retries and rate-limit-aware backoff.
Invalid players, invalid competition parameters and authorization failures are
reported without automatic retry. A prolonged outage must not discard chosen
poll winners or corrupt application-owned event state.

## Credentials and Secrets

Integration credentials are supplied through the runtime environment and are
never committed to source control, written to ordinary configuration records or
included in logs. Their required names and handling are defined in
[ENVIRONMENT.md](ENVIRONMENT.md).

## Adding an Integration

A new external integration requires a documented product need. Its addition
must define:

- Why the existing systems cannot provide the capability.
- Which data and operations it supplies.
- Which system is authoritative when data overlaps.
- Required credentials and permissions.
- Rate limits, retry rules and degraded behavior.
- What information is persisted and how it is reconciled.
