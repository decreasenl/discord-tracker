"""Durable competition workflow. Ambiguous external writes require reconciliation."""
import calendar
import json
import random
import re
import time
from datetime import datetime, UTC
from discord_tracker.storage.database import now

SKILLS = set('attack defence strength hitpoints ranged prayer magic cooking woodcutting fletching fishing firemaking crafting smithing mining herblore agility thieving slayer farming runecrafting hunter construction sailing'.split())


class Competitions:
    def __init__(self, db):
        self.db = db
        self.conn = db.connection
        # Never repeat a create operation whose response may have been lost.
        with self.conn:
            self.conn.execute("UPDATE events SET state='reconciling',error='Interrupted creation; checking Wise Old Man' WHERE state='creating'")
            for row in self.conn.execute("SELECT id FROM events WHERE state='posting'").fetchall():
                self.conn.execute('INSERT INTO alerts(created_at,message) VALUES(?,?)',
                                  (now(), f"Event #{row['id']}: poll posting was interrupted. Inspect the channel and use /events before retrying."))
            self.conn.execute("UPDATE events SET state='attention_required',error='Interrupted poll post; inspect Discord before retrying' WHERE state='posting'")
            for row in self.conn.execute("SELECT id FROM events WHERE deletion_state='pending'").fetchall():
                self.conn.execute('INSERT INTO alerts(created_at,message) VALUES(?,?)',
                                  (now(), f"Event #{row['id']}: deletion was interrupted. Local automation is cancelled. Repeat confirmed /event-cancel to check remote state."))
            self.conn.execute("UPDATE events SET deletion_state='unknown',error='Interrupted deletion; remote state needs verification' WHERE deletion_state='pending'")

    def get(self, event_id):
        row = self.conn.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
        if row is None:
            raise ValueError('Event not found')
        return row

    def configure(self, actor, kind, metrics, count, cooldown):
        pool = list(dict.fromkeys(m.strip().lower() for m in metrics.split(',') if m.strip()))
        if kind not in ('skill', 'boss') or not 2 <= count <= 10 or not 0 <= cooldown <= 120:
            raise ValueError('Use skill/boss, 2–10 poll choices and 0–120 cooldown months')
        if len(pool) < count or any(not re.fullmatch('[a-z][a-z0-9_]{1,79}', m) for m in pool):
            raise ValueError('Supply enough distinct Wise Old Man metric keys, separated by commas')
        if kind == 'skill' and any(m not in SKILLS for m in pool):
            raise ValueError('Unknown skill metric')
        if kind == 'boss' and any(m in SKILLS or m == 'overall' for m in pool):
            raise ValueError('Boss rotation must contain boss metric keys')
        self.db.setting(actor, f'ROTATION_{kind}', json.dumps({'pool': pool, 'count': count, 'cooldown': cooldown}))

    def select_options(self, kind, timestamp):
        raw = self.db.settings().get(f'ROTATION_{kind}')
        if raw is None:
            raise ValueError('Configure this rotation with /event-config first')
        config = json.loads(raw)
        eligible = []
        for metric in config['pool']:
            last = self.conn.execute('SELECT used_at FROM rotation WHERE kind=? AND metric=?', (kind, metric)).fetchone()
            if last:
                date = datetime.fromtimestamp(last[0], UTC)
                months = date.year * 12 + date.month - 1 + config['cooldown']
                year, month = divmod(months, 12)
                end = date.replace(year=year, month=month + 1, day=min(date.day, calendar.monthrange(year, month + 1)[1]))
                if end.timestamp() > timestamp:
                    continue
            eligible.append(metric)
        if len(eligible) < config['count']:
            raise ValueError('Too few eligible options remain after cooldown filtering')
        return random.SystemRandom().sample(eligible, config['count'])

    def schedule(self, actor, kind, channel_id, opens, closes, starts, ends, repeat=False):
        if kind not in ('skill', 'boss') or not time.time() <= opens < closes < starts < ends:
            raise ValueError('Times must be future timestamps ordered: poll open < close < start < end')
        if repeat and (starts - opens >= 7 * 86400 or ends - starts > 7 * 86400):
            raise ValueError('Weekly polls must precede their start by less than seven days; competitions last at most seven days')
        group = self.db.settings().get('WOM_GROUP_ID')
        if not group:
            raise ValueError('Configure a Wise Old Man group with /set-group first')
        if not self.db.settings().get(f'ROTATION_{kind}'):
            raise ValueError('Configure eligible metrics with /event-config first')
        with self.conn:
            if self.conn.execute("SELECT 1 FROM events WHERE kind=? AND state NOT IN ('completed','cancelled')", (kind,)).fetchone():
                raise ValueError('Resolve the existing event for this rotation first')
            event_id = self.conn.execute('''INSERT INTO events(kind,channel_id,group_id,options,opens,closes,starts,ends,repeat_weekly)
                VALUES(?,?,?,?,?,?,?,?,?)''', (kind, str(channel_id), int(group), '[]', opens, closes, starts, ends, int(repeat))).lastrowid
            self.conn.execute('INSERT INTO audit(at,actor,action,target,outcome) VALUES(?,?,?,?,?)',
                              (now(), str(actor), 'schedule-event', str(event_id), 'scheduled'))
        return event_id

    def vote(self, event_id, user_id, choice):
        event = self.get(event_id)
        if event['state'] != 'voting' or time.time() >= event['closes']:
            raise ValueError('This poll is not open')
        options = json.loads(event['options'])
        if not 1 <= choice <= len(options):
            raise ValueError('Choose one of the numbered poll options')
        with self.conn:
            self.conn.execute('INSERT INTO votes VALUES(?,?,?) ON CONFLICT(event_id,user_id) DO UPDATE SET option=excluded.option',
                              (event_id, str(user_id), options[choice - 1]))
        return options[choice - 1]

    def choose(self, event_id, actor='scheduler', override=None):
        event = self.get(event_id)
        if event['winner']:
            return event['winner']
        options = json.loads(event['options'])
        if override:
            if override not in options:
                raise ValueError('Winner must be one of the recorded options')
            winner = override
        else:
            counts = dict(self.conn.execute('SELECT option,COUNT(*) FROM votes WHERE event_id=? GROUP BY option', (event_id,)))
            if not counts:
                raise ValueError('No votes; manager must select a winner or cancel')
            best = max(counts.values())
            winner = random.SystemRandom().choice([option for option in options if counts.get(option, 0) == best])
        with self.conn:
            self.conn.execute("UPDATE events SET winner=?,state='selected',error=NULL WHERE id=?", (winner, event_id))
            self.conn.execute('INSERT INTO rotation VALUES(?,?,?) ON CONFLICT(kind,metric) DO UPDATE SET used_at=excluded.used_at',
                              (event['kind'], winner, time.time()))
            self.conn.execute('INSERT INTO audit(at,actor,action,target,outcome) VALUES(?,?,?,?,?)',
                              (now(), str(actor), 'event-winner', str(event_id), winner))
        return winner

    def state(self, event_id, state, error=None):
        with self.conn:
            self.conn.execute('UPDATE events SET state=?,error=? WHERE id=?', (state, error, event_id))

    def complete(self, event_id):
        with self.conn:
            self.conn.execute("UPDATE events SET state='completed' WHERE id=?", (event_id,))

    def enqueue_next(self, event_id):
        with self.conn:
            event = self.get(event_id)
            if event['repeat_weekly'] and not event['next_event_id']:
                next_id = self.conn.execute('''INSERT INTO events(kind,channel_id,group_id,options,opens,closes,starts,ends,repeat_weekly)
                    VALUES(?,?,?,?,?,?,?,?,1)''', (event['kind'], event['channel_id'], event['group_id'], '[]',
                    event['opens'] + 604800, event['closes'] + 604800, event['starts'] + 604800, event['ends'] + 604800)).lastrowid
                self.conn.execute('UPDATE events SET next_event_id=? WHERE id=?', (next_id, event_id))
