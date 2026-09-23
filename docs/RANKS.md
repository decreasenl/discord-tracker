# Automatic clan ranks

## Rules and ownership

The source of truth is [`config/ranks.yaml`](../config/ranks.yaml), not `.env`
and not a SQLite rules table. Edit the file and restart the bot. Invalid policy
files stop startup with a validation error; there is no silent fallback.
`enabled: false` pauses automatic assignment. `/rank-rules` shows the loaded
policy and its content hash. Manual overrides still work while disabled.

| Rank | Calendar months of clan membership |
| --- | ---: |
| Squire | 1 |
| Striker | 3 |
| Ninja | 6 |
| Inquisitor | 9 |
| Expert | 12 |
| Paladin | 24 |

These are **application ranks**, visible through `/rank`. No Discord roles,
Wise Old Man group roles or in-game clan ranks are changed. External role
synchronization would need a separate mapping and implementation.

SQLite stores actual clan joining dates, assigned and calculated ranks,
manual overrides, evaluation evidence, scheduler progress and audit history.
Each evaluation retains its policy content/hash, period boundaries, timezone,
joining date, player ID, XP observations and explanation.

## Membership and activity are separate

Managers record a member's actual clan joining date with `/member-joined`.
Neither Discord server joining time, WOM group joining time nor account link
time is substituted automatically. Existing members without this date are
deferred. Correct dates explicitly after a departure/rejoin if membership
duration should restart; restoring a record does not reset tenure by itself.

The calculated rank is the highest milestone reached **on the evaluation's
local date**. Calendar months are not 30-day durations. For example, January 31
plus one month is February's final day. Members can skip directly to the rank
their full tenure qualifies for. Below one month, no regular rank is assigned.

The activity gate is at least **1 total XP gained in the previous completed
calendar month**, not a rolling 30-day window and not boss kills. Boundaries use
the configured community timezone, including daylight-saving changes. During
September, the gate checks August 1 at 00:00 up to September 1 at 00:00
(exclusive). Tenure still advances during September.

The evaluator fetches the configured WOM group's bulk gains and matches stable
player IDs to active local members. It requires two distinct, valid observations
inside the activity window, nonnegative total-XP values and a consistent gain.
A positive observed difference within that month proves activity even if WOM
did not observe every day. Missing players, missing baselines, out-of-window
observations, unranked values and inconsistent gains defer the decision.
Zero recorded XP leaves the assigned rank unchanged; it is not proof that a
player never played. Incomplete tracking can miss real activity.

This feature reads WOM history; it does **not** create historical snapshots or
automatically refresh every player. Ensure members are in the configured WOM
group and that their XP is tracked regularly. Today's refresh cannot reconstruct
a missing previous-month baseline. Manual `/refresh` requests remain available.

## Schedule and overrides

The default schedule is daily at **03:00 in the community timezone**. Change
`daily_at` in the policy file to adjust it. A successful daily run is persisted;
restart after the scheduled time catches up that day's evaluation. There is no
replay of every missed historical month. Startup before 03:00 waits until 03:00.
Transient failures retry hourly and notify the configured alert channel.

- Active manual overrides take precedence. Evaluation retains the underlying
  membership-based calculation without changing the override.
- Clearing or expiring an override does not immediately remove the assigned
  rank. The next eligible promotion can update it.
- No automatic demotions occur, including after an override, joining-date
  correction or policy change. Managers use `/rank-set` for intentional demotion.
- Moderator, Captain, Lieutenant and Commander are protected and cannot appear
  in the automatic ladder. All other unrecognized assigned labels are also
  held for manager review.
- Record staff appointments using `/rank-set`; the bot does not infer them from
  Discord role names. Even after clearing/expiring a staff override, the assigned
  staff rank stays protected. To return someone to regular progression, explicitly
  `/rank-set` their regular rank, then `/rank-clear` that hold.
- Clan rank never grants permission to manage the bot.

Changing rule content or timezone permits a fresh daily evaluation. Repeated
evaluation does not duplicate a rank promotion. If a member is archived or
relinked during the API request, evaluation uses their current record afterward.

## Production rollout

### Dry runs and moderator review

- `/rank-dry-run` previews all active linked members, even with `enabled: false`.
  It uses the real evaluation rules and fresh WOM data but writes no ranks,
  evaluation history, run reports or scheduler progress. Normal command auditing
  still applies. The command does not pause an independently enabled scheduler.
- `/rank-summary` displays the last successfully committed automatic run.
- `/rank-summary last_upgrade:true` displays the last automatic run containing
  rank changes, even when a later daily run changed nobody.
- Use `page:2`, etc., to review additional members. Changes appear first, followed
  by deferred and unchanged/held members. Each entry includes the Discord name
  and ID, OSRS name, previous/new/calculated ranks, joining date, XP and reason.
  The header includes totals, evaluation time, activity period and policy hash.
- Replies are manager-only and private. Saved summaries survive restarts and
  retain names/evidence as they were at evaluation time; they do not re-fetch WOM.
  Each dry-run page request performs a fresh preview, so results may change if
  members or upstream data change between requests.

Schema version 7 stores each applied batch and its report atomically. Failed
batches roll back rank updates and do not replace the last successful summary.
Manual evaluations and dry runs do not replace the last automatic summary.
No summaries are reconstructed for runs predating this migration; the command
reports that none is available until an automatic evaluation completes.
Alert-channel notifications point managers to `/rank-summary` for review.

### Rollout steps

1. Back up SQLite before the version-6 migration. Existing manual rank labels
   are preserved as assigned ranks, including expired overrides.
2. For a preview-only rollout, set `enabled: false` and rebuild/recreate with
   `docker compose up -d --build` while recording joining dates and staff overrides.
   The scheduler stays paused. `/rank-preview` still fetches activity and shows
   what would happen if automation were enabled, without changing ranks.
3. Configure the actual WOM group using `/set-group` if not already configured.
4. Set each active member's joining date, for example:
   `/member-joined member:@Example joined_on:2024-06-15 reason:Verified clan record`.
5. Use `/rank-preview member:@Example` to verify decisions before enabling.
   Then set `enabled: true` and recreate with `docker compose up -d --force-recreate`.
   The scheduled worker is now enabled; active members may be evaluated as soon
   as the bot starts after 03:00.
6. Use `/rank-evaluate member:@Example` for an immediate application or omit
   `member` to evaluate everyone. Ongoing daily evaluations need no admin action.
7. Configure `/set-alert-channel` and inspect `/audit` and service logs. Scheduled
   changes/deferred-member counts go to the alert channel. Members inspect their
   assigned rank with `/rank`; no promotion DMs are sent.

All command replies are private. `/rank-preview` writes no rank state or
evaluation history (normal command auditing still applies). Manual evaluations
do not consume the daily scheduled run. `/rank` shows the last evaluation; it
does not fetch fresh XP. Use `/rank-preview` for a current explanation.

Compose mounts `config/ranks.yaml` read-only at `/app/config/ranks.yaml`; the image
also includes the default file. Keep the host file backed up alongside SQLite.
No new environment variables or secrets are needed. Run local commands from
the repository root so the relative configuration path resolves correctly.

The upstream contract is documented in the
[Wise Old Man group bulk gains API](https://docs.wiseoldman.net/api/groups/group-endpoints#get-group-bulk-gains).
