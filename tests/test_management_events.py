import json
import time
import pytest
from discord_tracker.storage.database import Database
from discord_tracker.services.competitions import Competitions


def test_member_lifecycle_and_settings(tmp_path):
    db = Database(tmp_path / 'test.sqlite3')
    db.link(1, 2, 'Member', {'id': 7, 'username': 'first'})
    db.change_member(1, 2, 'archive', 'left clan')
    assert db.member(2)['archived_at']
    with pytest.raises(ValueError):
        db.snapshot(2, {'id': 7, 'username': 'first'})
    db.change_member(1, 2, 'restore', 'returned')
    db.change_member(1, 2, 'relink', 'corrected account', {'id': 8, 'username': 'second'})
    assert db.member(2)['player_id'] == 8
    assert db.connection.execute('SELECT count(*) FROM member_history').fetchone()[0] == 3
    db.setting(1, 'COMMUNITY_TIMEZONE', 'UTC')
    db.close()
    db = Database(tmp_path / 'test.sqlite3')
    assert db.settings()['COMMUNITY_TIMEZONE'] == 'UTC'
    assert db.connection.execute('PRAGMA user_version').fetchone()[0] == 5
    db.close()


def test_upgrade_preserves_v1_members(tmp_path):
    import sqlite3
    path = tmp_path / 'old.sqlite3'
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE members(discord_id TEXT PRIMARY KEY,display_name TEXT NOT NULL,
                player_id INTEGER UNIQUE NOT NULL,username TEXT NOT NULL,linked_at TEXT NOT NULL,
                retrieved_at TEXT NOT NULL,snapshot TEXT NOT NULL);
            CREATE TABLE audit(id INTEGER PRIMARY KEY,at TEXT,actor TEXT,action TEXT,target TEXT,outcome TEXT);
            INSERT INTO members VALUES('1','Name',7,'player','original-date','fetch-date','{}');
            PRAGMA user_version=1;
        ''')
    db = Database(path)
    assert db.member(1)['linked_at'] == 'original-date'
    assert db.member(1)['archived_at'] is None
    assert db.connection.execute('PRAGMA user_version').fetchone()[0] == 5
    db.close()


@pytest.mark.asyncio
async def test_worker_creates_once_and_repeats_once(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from discord_tracker.jobs.competitions import CompetitionWorker
    db = Database(tmp_path / 'worker.sqlite3')
    db.seed({'WOM_GROUP_ID': '1'})
    service = Competitions(db)
    service.configure(1, 'skill', 'attack,defence,strength', 2, 12)
    start = time.time() + 100
    event_id = service.schedule(1, 'skill', 99, start, start+100, start+200, start+300, True)
    message = SimpleNamespace(id=100, edit=AsyncMock())
    channel = SimpleNamespace(send=AsyncMock(return_value=message), fetch_message=AsyncMock(return_value=message))
    wom = SimpleNamespace(request=AsyncMock(return_value={'competition': {'id': 55}, 'verificationCode': 'secret'}))
    bot = SimpleNamespace(db=db, competitions=service, wom=wom, get_channel=lambda _: channel)
    worker = CompetitionWorker(bot)
    monkeypatch.setenv('WOM_GROUP_VERIFICATION_CODE', 'secret')
    monkeypatch.setattr(time, 'time', lambda: start + 1)
    await worker.tick(event_id)
    assert service.get(event_id)['state'] == 'voting'
    service.vote(event_id, 1, 1)
    monkeypatch.setattr(time, 'time', lambda: start + 101)
    await worker.tick(event_id)
    await worker.tick(event_id)
    assert service.get(event_id)['competition_id'] == 55
    await worker.tick(event_id)
    await worker.tick(event_id)
    assert wom.request.await_count == 1
    assert channel.send.await_count == 1
    assert db.connection.execute('SELECT count(*) FROM events').fetchone()[0] == 2
    assert service.get(event_id)['state'] == 'waiting'
    monkeypatch.setattr(time, 'time', lambda: start + 301)
    await worker.tick(event_id)
    assert service.get(event_id)['state'] == 'completed'
    db.close()


def test_votes_cooldown_and_restart_recovery(tmp_path):
    db = Database(tmp_path / 'test.sqlite3')
    db.seed({'WOM_GROUP_ID': '1'})
    service = Competitions(db)
    service.configure(1, 'skill', 'attack,defence,strength', 2, 12)
    start = time.time() + 100
    event_id = service.schedule(1, 'skill', 99, start, start+100, start+200, start+300, True)
    with db.connection:
        db.connection.execute("UPDATE events SET state='voting',options=? WHERE id=?", (json.dumps(['attack', 'defence']), event_id))
    service.vote(event_id, 1, 1)
    service.vote(event_id, 1, 2)
    service.vote(event_id, 2, 2)
    assert db.connection.execute('SELECT count(*) FROM votes').fetchone()[0] == 2
    assert service.choose(event_id) == 'defence'
    assert 'defence' not in service.select_options('skill', start)
    service.enqueue_next(event_id)
    service.enqueue_next(event_id)
    assert db.connection.execute('SELECT count(*) FROM events').fetchone()[0] == 2
    service.state(event_id, 'creating')
    restored = Competitions(db)
    assert restored.get(event_id)['state'] == 'reconciling'
    with pytest.raises(ValueError):
        restored.vote(event_id, 3, 1)
    db.close()
