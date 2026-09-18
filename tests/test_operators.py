import sqlite3
import sys
import pytest
from discord_tracker.cli import main
from discord_tracker.storage.database import Database


def test_backup_and_recovery(tmp_path, monkeypatch):
    path = tmp_path / 'source.sqlite3'
    backup = tmp_path / 'backup.sqlite3'
    monkeypatch.setenv('SQLITE_PATH', str(path))
    db = Database(path)
    db.seed({'DISCORD_GUILD_ID': '1', 'DISCORD_MANAGER_ROLE_ID': '3'})
    db.close()
    monkeypatch.setattr(sys, 'argv', ['tracker-admin', 'backup', str(backup)])
    main()
    with sqlite3.connect(backup) as restored:
        assert restored.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert dict(restored.execute('SELECT key,value FROM settings'))['DISCORD_MANAGER_ROLE_ID'] == '3'
    with pytest.raises(FileExistsError):
        main()
    monkeypatch.setattr(sys, 'argv', ['tracker-admin', 'recover-manager', '4', '--operator', 'tester'])
    main()
    db = Database(path)
    assert db.settings()['DISCORD_MANAGER_ROLE_ID'] == '4'
    row = db.connection.execute('SELECT actor,action,outcome FROM audit').fetchone()
    assert tuple(row) == ('tester', 'recover-manager', 'replaced 3')
    db.close()
