"""Application rank decisions and persistence. Never mutates external roles."""
import asyncio
import hashlib
import json
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from discord_tracker.rank_rules import previous_month
from discord_tracker.storage.database import now


def observed_xp(row, start, end):
    """Two valid observations inside the period can prove positive activity.

    Zero is only 'no recorded XP', not proof that an untracked player was idle.
    Never treat a missing/unranked baseline as newly earned experience.
    """
    try:
        first = datetime.fromisoformat(row['startDate'].replace('Z', '+00:00'))
        last = datetime.fromisoformat(row['endDate'].replace('Z', '+00:00'))
        if first.tzinfo is None or last.tzinfo is None or not start <= first < last < end:
            return None
        overall = [metric for metric in row['data'] if metric['metric'] == 'overall']
        if len(overall) != 1:
            return None
        values = overall[0]
        before, after, gained = (values[key] for key in ('start', 'end', 'gained'))
        if any(type(value) is not int or value < 0 for value in (before, after, gained)):
            return None
        return gained if after - before == gained else None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


class Ranks:
    def __init__(self, db, wom, rules):
        self.db, self.wom, self.rules = db, wom, rules
        self.lock = asyncio.Lock()

    def active_member(self, target):
        member = self.db.member(target)
        if not member or member['archived_at']:
            raise ValueError('An active linked membership is required')
        return member

    def state(self, target):
        row = self.db.connection.execute('SELECT * FROM member_ranks WHERE discord_id=?', (str(target),)).fetchone()
        return dict(row) if row else {'assigned': None, 'calculated': None, 'evaluated_at': None, 'explanation': None}

    def override(self, target):
        return self.db.connection.execute('SELECT * FROM rank_overrides WHERE discord_id=?', (str(target),)).fetchone()

    def joined(self, actor, target, value, reason, timezone):
        self.active_member(target)
        try:
            joined = date.fromisoformat(value)
        except ValueError:
            raise ValueError('Clan joining date must be YYYY-MM-DD') from None
        if value != joined.isoformat() or joined > datetime.now(ZoneInfo(timezone)).date():
            raise ValueError('Clan joining date must be YYYY-MM-DD and cannot be in the future')
        if not reason.strip() or len(reason) > 500:
            raise ValueError('A reason of 1-500 characters is required')
        with self.db.connection:
            old = self.db.member(target)['joined_on']
            self.db.connection.execute('UPDATE members SET joined_on=? WHERE discord_id=?', (value, str(target)))
            self.db.connection.execute('INSERT INTO audit(at,actor,action,target,outcome) VALUES(?,?,?,?,?)',
                                       (now(), str(actor), 'member-joined', str(target), f'{old} -> {value}: {reason}'))

    def set_override(self, actor, target, rank, reason, expiry=None):
        self.active_member(target)
        rank, reason = rank.strip(), reason.strip()
        if not 0 < len(rank) <= 80 or not 0 < len(reason) <= 500:
            raise ValueError('Provide a rank up to 80 characters and a reason up to 500 characters')
        if expiry is not None and expiry <= datetime.now(UTC).timestamp():
            raise ValueError('Override expiry must be in the future')
        with self.db.connection:
            old = self.state(target)['assigned']
            self.db.connection.execute('''INSERT INTO rank_overrides VALUES(?,?,?,?,?) ON CONFLICT(discord_id)
                DO UPDATE SET rank=excluded.rank,reason=excluded.reason,actor=excluded.actor,expires_at=excluded.expires_at''',
                (str(target), rank, reason, str(actor), expiry))
            self.db.connection.execute('''INSERT INTO member_ranks(discord_id,assigned) VALUES(?,?)
                ON CONFLICT(discord_id) DO UPDATE SET assigned=excluded.assigned''', (str(target), rank))
            self.db.connection.execute('INSERT INTO audit(at,actor,action,target,outcome) VALUES(?,?,?,?,?)',
                                      (now(), str(actor), 'rank-set', str(target), f'{old} -> {rank}: {reason}; expiry={expiry}'))

    def clear_override(self, actor, target, reason):
        self.active_member(target)
        if not reason.strip() or len(reason) > 500:
            raise ValueError('A reason of 1-500 characters is required')
        with self.db.connection:
            self.db.connection.execute('DELETE FROM rank_overrides WHERE discord_id=?', (str(target),))
            self.db.connection.execute('INSERT INTO audit(at,actor,action,target,outcome) VALUES(?,?,?,?,?)',
                                      (now(), str(actor), 'rank-clear', str(target), reason.strip()))

    def decide(self, member, row, instant, timezone, *, preview=False):
        start, end = previous_month(instant, timezone)
        today = instant.astimezone(ZoneInfo(timezone)).date()
        current = self.state(member['discord_id'])['assigned']
        override = self.override(member['discord_id'])
        joined = date.fromisoformat(member['joined_on']) if member['joined_on'] else None
        calculated = self.rules.calculated(joined, today) if joined else None
        xp = observed_xp(row, start, end)
        assigned = current
        positions = {name.casefold(): index for index, (name, _) in enumerate(self.rules.ranks)}
        if not self.rules.enabled and not preview:
            reason = 'Automatic ranking disabled in config/ranks.yaml'
        elif override and (override['expires_at'] is None or override['expires_at'] > instant.timestamp()):
            assigned = override['rank']
            reason = 'Active manual override takes precedence'
        elif current and current.casefold() not in positions:
            reason = 'Protected staff or unrecognized manual rank; manager action required'
        elif joined is None:
            reason = 'Deferred: clan joining date is missing; use /member-joined'
        elif xp is None:
            reason = 'Deferred: valid monthly XP observations unavailable'
        elif xp < self.rules.minimum_gain:
            reason = 'Unchanged: insufficient recorded XP in the previous calendar month'
        elif calculated is None:
            reason = 'Unchanged: first membership milestone not reached'
        elif current and positions[current.casefold()] >= positions[calculated.casefold()]:
            reason = 'Unchanged: already at or above the calculated rank; no automatic demotions'
        else:
            assigned = calculated
            reason = 'Promoted: membership milestone reached and monthly XP activity confirmed'
        return {'discord_id': member['discord_id'], 'player_id': member['player_id'],
                'display_name': member['display_name'], 'username': member['username'],
                'joined_on': member['joined_on'], 'evaluated_on': today.isoformat(),
                'timezone': timezone, 'period_start': start.isoformat(), 'period_end': end.isoformat(),
                'rules_hash': self.rules.digest, 'rules': json.loads(self.rules.document),
                'xp_gained': xp, 'observation': row, 'override': dict(override) if override else None,
                'previous': current, 'assigned': assigned, 'calculated': calculated, 'reason': reason}

    async def evaluate(self, actor='scheduler', target=None, *, preview=False, instant=None):
        if self.lock.locked():
            raise ValueError('A rank evaluation is already running; try again shortly')
        async with self.lock:
            instant = instant or datetime.now(UTC)
            settings = self.db.settings()
            timezone = settings.get('COMMUNITY_TIMEZONE', 'Europe/Amsterdam')
            if target is not None:
                self.active_member(target)
            group = settings.get('WOM_GROUP_ID')
            needs_activity = self.rules.enabled or preview
            if needs_activity and not group:
                raise ValueError('Configure the Wise Old Man group with /set-group before evaluating ranks')
            start, end = previous_month(instant, timezone)
            gains = await self.wom.group_gains(int(group), start, end) if needs_activity else {}
            latest = self.db.settings()
            if latest.get('WOM_GROUP_ID') != group or latest.get('COMMUNITY_TIMEZONE', 'Europe/Amsterdam') != timezone:
                raise ValueError('Group or timezone changed during evaluation; retry with the new settings')
            # Read members and overrides AFTER network I/O: commands may change them while awaiting WOM.
            members = [self.active_member(target)] if target is not None else list(self.db.connection.execute(
                'SELECT * FROM members WHERE archived_at IS NULL'))
            results = []
            for member in members:
                raw = gains.get(member['player_id'])
                # Persist only the evidence needed for XP, not unrelated upstream profile data.
                row = None
                if isinstance(raw, dict) and isinstance(raw.get('data'), list):
                    row = {key: raw.get(key) for key in ('startDate', 'endDate')}
                    row['data'] = [m for m in raw['data'] if isinstance(m, dict) and m.get('metric') == 'overall']
                result = self.decide(member, row, instant, timezone, preview=preview)
                results.append(result)
            if not preview:
                # Apply the entire batch and its report atomically. No successful
                # report can describe only a partially committed run.
                with self.db.connection:
                    for result in results:
                        self.persist(actor, result)
                    report = {'at': instant.isoformat(), 'period_start': start.isoformat(),
                              'period_end': end.isoformat(), 'rules_hash': self.rules.digest,
                              'results': results}
                    self.db.connection.execute('INSERT INTO rank_runs(at,actor,changed,report) VALUES(?,?,?,?)',
                        (now(), str(actor), sum(r['previous'] != r['assigned'] for r in results), json.dumps(report)))
            return results

    def last_run(self, last_upgrade=False):
        query = "SELECT * FROM rank_runs WHERE actor='scheduler'"
        if last_upgrade:
            query += ' AND changed > 0'
        row = self.db.connection.execute(query + ' ORDER BY id DESC LIMIT 1').fetchone()
        if row is None:
            raise ValueError('No recorded automatic run with upgrades yet' if last_upgrade else 'No automatic run summary recorded yet')
        return row['id'], json.loads(row['report'])

    def persist(self, actor, result):
        """Called inside evaluate's batch transaction; must not commit individually."""
        details = json.dumps(result, sort_keys=True)
        fingerprint = hashlib.sha256(details.encode()).hexdigest()
        self.db.connection.execute('''INSERT OR IGNORE INTO rank_evaluations
            (at,actor,discord_id,fingerprint,details) VALUES(?,?,?,?,?)''',
            (now(), str(actor), result['discord_id'], fingerprint, details))
        self.db.connection.execute('''INSERT INTO member_ranks
            (discord_id,assigned,calculated,evaluated_at,explanation) VALUES(?,?,?,?,?)
            ON CONFLICT(discord_id) DO UPDATE SET assigned=excluded.assigned,
            calculated=excluded.calculated,evaluated_at=excluded.evaluated_at,explanation=excluded.explanation''',
            (result['discord_id'], result['assigned'], result['calculated'], now(), result['reason']))
        if result['previous'] != result['assigned']:
            self.db.connection.execute('INSERT INTO audit(at,actor,action,target,outcome) VALUES(?,?,?,?,?)',
                (now(), str(actor), 'rank-automatic', result['discord_id'],
                 f"{result['previous']} -> {result['assigned']}; {result['reason']}; rules={self.rules.digest}"))
