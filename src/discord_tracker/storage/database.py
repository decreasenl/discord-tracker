import json
import sqlite3
from datetime import datetime, UTC
from pathlib import Path


def now():
    return datetime.now(UTC).isoformat()


class Database:
    """Small transactions on a single event-loop thread; no network calls in transactions."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version > 6:
            self.connection.close()
            raise ValueError("Database schema is newer than this application")
        if version == 0:
            self.connection.executescript('''
                BEGIN;
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE members (
                    discord_id TEXT PRIMARY KEY, display_name TEXT NOT NULL,
                    player_id INTEGER UNIQUE NOT NULL, username TEXT NOT NULL,
                    linked_at TEXT NOT NULL, retrieved_at TEXT NOT NULL,
                    snapshot TEXT NOT NULL);
                CREATE TABLE audit (
                    id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL,
                    action TEXT NOT NULL, target TEXT NOT NULL, outcome TEXT NOT NULL);
                PRAGMA user_version=1;
                COMMIT;
            ''')

        if version < 2:
            self.connection.executescript('''
                BEGIN;
                ALTER TABLE members ADD COLUMN archived_at TEXT;
                CREATE TABLE member_history (
                    id INTEGER PRIMARY KEY, discord_id TEXT NOT NULL,
                    at TEXT NOT NULL, record TEXT NOT NULL);
                PRAGMA user_version=2;
                COMMIT;
            ''')
        if version < 3:
            self.connection.executescript('''
                BEGIN;
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, channel_id TEXT NOT NULL,
                    group_id INTEGER NOT NULL, options TEXT NOT NULL,
                    opens REAL NOT NULL, closes REAL NOT NULL, starts REAL NOT NULL, ends REAL NOT NULL,
                    state TEXT NOT NULL DEFAULT 'scheduled', winner TEXT, message_id TEXT,
                    competition_id INTEGER, error TEXT, repeat_weekly INTEGER NOT NULL DEFAULT 0,
                    next_event_id INTEGER);
                CREATE TABLE votes (
                    event_id INTEGER NOT NULL REFERENCES events(id), user_id TEXT NOT NULL,
                    option TEXT NOT NULL, PRIMARY KEY(event_id,user_id));
                CREATE TABLE rotation (kind TEXT NOT NULL, metric TEXT NOT NULL, used_at REAL NOT NULL,
                    PRIMARY KEY(kind,metric));
                PRAGMA user_version=3;
                COMMIT;
            ''')

        if version < 4:
            self.connection.executescript('''
                BEGIN;
                CREATE TABLE rank_overrides (
                    discord_id TEXT PRIMARY KEY REFERENCES members(discord_id),
                    rank TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL, expires_at REAL);
                PRAGMA user_version=4;
                COMMIT;
            ''')

        if version < 5:
            self.connection.executescript('''
                BEGIN;
                ALTER TABLE events ADD COLUMN request_title TEXT;
                ALTER TABLE events ADD COLUMN reconcile_attempts INTEGER NOT NULL DEFAULT 0;
                ALTER TABLE events ADD COLUMN reconcile_after REAL NOT NULL DEFAULT 0;
                ALTER TABLE events ADD COLUMN deletion_state TEXT;
                CREATE TABLE alerts (
                    id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
                    message TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0, delivered_at TEXT);
                PRAGMA user_version=5;
                COMMIT;
            ''')

        if version < 6:
            self.connection.executescript('''
                BEGIN;
                ALTER TABLE members ADD COLUMN joined_on TEXT;
                CREATE TABLE member_ranks (
                    discord_id TEXT PRIMARY KEY REFERENCES members(discord_id),
                    assigned TEXT, calculated TEXT, evaluated_at TEXT, explanation TEXT);
                INSERT INTO member_ranks(discord_id,assigned)
                    SELECT discord_id,rank FROM rank_overrides;
                CREATE TABLE rank_evaluations (
                    id INTEGER PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL,
                    discord_id TEXT NOT NULL REFERENCES members(discord_id),
                    fingerprint TEXT NOT NULL UNIQUE, details TEXT NOT NULL);
                PRAGMA user_version=6;
                COMMIT;
            ''')

    def settings(self):
        return dict(self.connection.execute("SELECT key, value FROM settings"))

    def seed(self, settings):
        with self.connection:
            if not self.settings():
                self.connection.executemany("INSERT INTO settings VALUES (?, ?)", settings.items())

    def audit(self, actor, action, target, outcome):
        with self.connection:
            self.connection.execute("INSERT INTO audit(at,actor,action,target,outcome) VALUES(?,?,?,?,?)",
                                    (now(), str(actor), action, str(target), outcome))

    def member(self, discord_id):
        return self.connection.execute("SELECT * FROM members WHERE discord_id=?", (str(discord_id),)).fetchone()

    def display_name(self, discord_id, name):
        with self.connection:
            self.connection.execute('UPDATE members SET display_name=? WHERE discord_id=?', (name, str(discord_id)))

    def link(self, actor, discord_id, name, player):
        existing = self.member(discord_id)
        if existing and existing["player_id"] != player["id"]:
            raise ValueError("Member already linked to another player; use /relink to correct the link")
        try:
            with self.connection:
                if existing and existing['archived_at']:
                    raise ValueError('Member is archived; restore them before linking')
                self.connection.execute('''INSERT INTO members
                    (discord_id,display_name,player_id,username,linked_at,retrieved_at,snapshot) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(discord_id) DO UPDATE SET display_name=excluded.display_name,
                    username=excluded.username, retrieved_at=excluded.retrieved_at, snapshot=excluded.snapshot''',
                    (str(discord_id), name, player["id"], player["username"], now(), now(), json.dumps(player)))
                self.connection.execute("INSERT INTO audit(at,actor,action,target,outcome) VALUES(?,?,?,?,?)",
                                        (now(), str(actor), "link", str(discord_id), "completed"))
        except sqlite3.IntegrityError:
            raise ValueError("That OSRS player is already linked to another Discord member") from None

    def snapshot(self, discord_id, player):
        with self.connection:
            result = self.connection.execute("UPDATE members SET username=?,snapshot=?,retrieved_at=? WHERE discord_id=? AND player_id=? AND archived_at IS NULL",
                                    (player["username"], json.dumps(player), now(), str(discord_id), player["id"]))
            if not result.rowcount:
                raise ValueError('Member changed while fetching data; retry the operation')

    def change_member(self, actor, target, action, reason, player=None):
        if not reason.strip():
            raise ValueError('A reason is required')
        record = self.member(target)
        if record is None:
            raise ValueError('Member not found')
        if action not in ('archive', 'restore', 'relink'):
            raise ValueError('Unknown member operation')
        if action == 'relink' and record['archived_at']:
            raise ValueError('Restore this member before relinking')
        try:
            with self.connection:
                self.connection.execute('INSERT INTO member_history(discord_id,at,record) VALUES(?,?,?)',
                                        (str(target), now(), json.dumps(dict(record))))
                if action == 'relink':
                    self.connection.execute('UPDATE members SET player_id=?,username=?,snapshot=?,retrieved_at=? WHERE discord_id=?',
                                            (player['id'], player['username'], json.dumps(player), now(), str(target)))
                else:
                    self.connection.execute('UPDATE members SET archived_at=? WHERE discord_id=?',
                                            (now() if action == 'archive' else None, str(target)))
                self.connection.execute('INSERT INTO audit(at,actor,action,target,outcome) VALUES(?,?,?,?,?)',
                                        (now(), str(actor), action, str(target), reason.strip()[:500]))
        except sqlite3.IntegrityError:
            raise ValueError('That OSRS player is already linked to another member') from None

    def setting(self, actor, key, value):
        with self.connection:
            old = self.settings().get(key)
            self.connection.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))
            self.connection.execute('INSERT INTO audit(at,actor,action,target,outcome) VALUES(?,?,?,?,?)',
                                    (now(), str(actor), 'setting', key, json.dumps({'old': old, 'new': value})))

    def close(self):
        self.connection.close()
