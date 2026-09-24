# Product Specification

## Purpose

This document defines the observable behavior of the application. It supports
the direction established in the leading product document and should not
override its principles.

Behavior that has not yet been decided is identified explicitly rather than
being implied by an implementation choice.

## Roles and Permissions

The application recognizes two broad categories of users:

- **Members** may view information made available to the community and manage
  their own account link where permitted.
- **Managers** may manage members, ranks, events and application settings.

Discord supplies the authenticated user identity. The application has no
separate login, passwords or user sessions. Linking an OSRS account is an
account association, not authentication.

Commands are restricted to one configured Discord server; direct messages and
other servers are rejected. Access uses two configurable Discord role IDs:

- The bot-access role permits normal commands.
- The manager role permits normal commands and management commands, even when
  the caller does not also hold the bot-access role.

Clan ranks do not grant manager access. Discord role names are display labels;
authorization uses role IDs. Every command and subsequent management
interaction checks the server and the caller's current roles. Missing or
unverifiable authorization denies access.

An operator maintains deployment, secrets, backups and recovery through host
access. This responsibility does not automatically grant Discord management
permissions, and managers do not receive host access through their bot role.

Material management actions must be recorded with the acting Discord user,
the action, its target and the time it occurred. This includes rank overrides,
member changes, event intervention and configuration changes.

## Members and Accounts

Each active clan member has a member record. A member record links the person's
Discord identity with the OSRS identity used for Wise Old Man statistics.

The application must support:

- Adding, viewing, updating and archiving members.
- Linking and correcting Discord and OSRS identities.
- Retaining historical activity and event results when a member is archived.
- Tracking the date from which clan membership duration is calculated.
- Manager-previewed bulk linking by matching delimiter-separated server-profile
  names from left to right to an exact configured Wise Old Man group member. It considers every
  non-bot server member independently of command-access roles. Ambiguous matches
  and existing links require manual resolution and are never overwritten.

Account ownership verification, support for multiple OSRS accounts per member
and name-change handling require product decisions before implementation. The
data model should not unnecessarily prevent these capabilities.

## Clan Ranks

Clan rank assignment is separate from leaderboard placement.

Automatic rules live in `config/ranks.yaml`. The regular ladder is Squire at
1 calendar month of clan membership, Striker at 3, Ninja at 6, Inquisitor at 9,
Expert at 12 and Paladin at 24. Promotion requires at least 1 total XP gained
within the previous completed calendar month. Boss kills are not considered.
The actual clan joining date is manager-maintained, never inferred from account
link time. Rank evaluation exposes the calculation, activity gate and decision.

A manager may override an automatically determined rank. An override takes
precedence over automatic evaluation until it is removed or, if an expiry was
provided, until it expires. The application must retain the calculated rank so
the effect of the override remains visible.

Rank rules and the precise evaluation schedule remain configurable. Changes to
rules must not silently remove active manual overrides.

Evaluation runs daily at 03:00 community time by default. Tenure is measured on
the evaluation date; the activity window is the preceding calendar month.
Inactivity retains the assigned rank, missing/unreliable data defers promotion,
and automation never demotes. Expiry/removal of an override retains the assigned
rank until an eligible promotion. Moderator, Captain, Lieutenant and Commander
are manual staff appointments outside the ladder and remain protected.
These are application ranks only, not automatic changes to Discord or OSRS.
See [RANKS.md](RANKS.md) for configuration, commands and evidence requirements.

## Activity and Monthly Leaderboard

Leaderboard scoring remains disabled until the community supplies its rules.
Automatic membership ranks use the agreed XP-only activity gate above; they do
not require or imply a leaderboard scoring formula.

The application obtains OSRS activity data through Wise Old Man and uses it to
produce a leaderboard for each calendar month.

Each leaderboard period has a defined start and end in the configured
community timezone. Completed periods are retained so previous results can be
viewed.

The initial scoring formula and the treatment of missing or incomplete Wise
Old Man data remain product decisions. The scoring system must be replaceable
and must retain enough detail to explain a member's score.

Clan rank must not be inferred directly from leaderboard position unless an
explicit rank rule says so.

## Discord Commands

Member-facing commands should include:

- `/stats` for current OSRS statistics.
- `/progress` for activity over a selected or default period.
- `/rank` for clan rank and the reason for it.
- `/leaderboard` for the current or a completed monthly leaderboard.
- `/member` for relevant member information.
- `/competition` for current or recent competition information.

Management commands must cover member, rank, event and settings management.
The final command names, parameters and response presentation belong to the
Discord interaction design and may evolve without changing the underlying
behavior in this document.

Responses containing management-only or sensitive information must only be
visible to authorized users. Commands should give a clear confirmation when a
change succeeds and a useful explanation when it cannot be performed.

## Manager Diagnostics and Manual Retrieval

Managers can test each integration independently, inspect recent failures and
retrieve an individual member's linked Discord and OSRS data. Results are
private to the requesting manager or an authorized management channel.

Discord diagnostics report connection status, configured server access and
channel/permission checks. Wise Old Man diagnostics report reachability and
configured group or player lookup results. Tests are read-only and must not
create competitions, polls or test announcements automatically.

Managers may request an individual member's OSRS data refresh. The application
acknowledges the request, processes it asynchronously and reports pending,
completed or failed status, including retrieval and upstream update times when
available. Repeated requests are rate-limited and concurrent requests for the
same member are coalesced. A retrieval of cached data is distinguished from a
request to update upstream data.

Managers may retry supported failed operations through the same validation and
duplicate-prevention rules used by automation. Diagnostics, refresh requests
and retries record the actor, target, time and outcome. Responses and audit
records must not expose secrets or unrestricted raw API payloads.

## Scheduled Competitions

Anyone with the bot-access or manager role can vote, without an OSRS account
link. Each Discord user has one vote per event and may change it before closing.
The implemented poll uses a numbered message and `/vote`, with authorization on
every vote. See [SETUP.md](SETUP.md) for current commands and limitations.


Skill of the Week and Boss of the Week follow the same general lifecycle:

1. At the configured time, determine the eligible options.
2. Randomly select the configured number of distinct options.
3. Create a poll in the configured Discord channel.
4. Accept votes for the configured voting duration.
5. Select the option with the most votes.
6. If the highest result is tied, randomly select only from the tied options.
7. Create the corresponding Wise Old Man competition.
8. Announce the result and competition details in Discord.
9. Mark the event as active and later completed or cancelled.

Random choices must be recorded so managers can audit what was selected and
why.

Only configured options may be selected. An option in its cooldown period is
not eligible for random selection. Boss of the Week has a default cooldown of
12 months; all cooldowns and poll sizes are configurable.

A cooldown begins when an option wins a poll. Cancelling an event does not
remove or change that cooldown automatically; a manager may explicitly adjust
the rotation when appropriate.

## Competition Exceptions

Automation must fail safely and leave managers in control:

- If too few options are eligible, no poll is created and managers are
  notified with the reason.
- If a poll receives no votes, no winner is inferred; managers are prompted to
  choose, rerun or cancel it.
- If the Discord poll is missing or inaccessible, the event is paused for
  manager intervention.
- If Wise Old Man competition creation fails, the chosen result is retained
  and creation is retried according to operational policy. Repeated failure is
  reported to managers.
- Processing the same scheduled action more than once must not create duplicate
  polls or competitions.

Managers may create, cancel, reschedule or override an event. An override must
record the manager, reason and previous state.

## Configuration

The following are configurable without changing application code:

- Community name and timezone.
- Discord channels, bot-access role and manager role.
- Member ranks and rank rules.
- Leaderboard and scoring rules once those rules are defined.
- Event schedules and voting duration.
- Poll option count.
- Eligible skills and bosses.
- Event cooldowns.
- Wise Old Man group and competition settings.

Configuration changes are validated before being applied. Invalid changes must
leave the previous valid configuration in place.

## Reliability and Recovery

Managers configure an operational alert channel and may request a test alert.
Background errors include the affected event and recovery instructions. Alerts
persist until delivered, with backoff during outages and operator-log fallback.
Commands return private success, failure or partial-completion messages.

Uncertain competition creation triggers a bounded, read-only reconciliation
search. Only a unique exact match is attached automatically; ambiguity requires
manager review. No create request is blindly repeated.

Cancellation defaults to local automation only. External competition deletion
requires an explicit request and confirmation of its recorded competition ID.
Local cancellation remains effective even if external deletion fails. The bot
records and reports the separate outcomes, and supports checking whether a
previous deletion already succeeded before retrying cleanup.

The application persists member, rank, leaderboard, configuration and event
state across restarts.

Scheduled work must recover after downtime without duplicating external
effects. External API failures should use bounded retries and must eventually
surface an actionable error to managers. The application must respect Discord
and Wise Old Man rate limits.

Backups must cover application-owned persistent data. Discord and Wise Old Man
remain external systems and are not treated as the sole record for application
decisions or audit history.

## Open Product Decisions

The following intentionally remain undecided:

- The leaderboard scoring formula.
- Whether members may self-link accounts and how ownership is verified.
- Whether a member may have multiple OSRS accounts.
- The precise treatment of OSRS name changes.
- The initial schedules, voting durations and poll sizes.

These decisions should be added here when agreed rather than embedded only in
code or configuration defaults.
