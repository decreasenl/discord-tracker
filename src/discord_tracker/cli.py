"""Offline operator tools. Stop the application before recovery or restoration."""
import argparse
import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
from discord_tracker.storage.database import Database, now


def main():
    load_dotenv(override=False)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    recovery = sub.add_parser('recover-manager', help='Run only while the bot is stopped')
    recovery.add_argument('role_id', type=int)
    recovery.add_argument('--operator', required=True)
    backup = sub.add_parser('backup')
    backup.add_argument('destination', type=Path)
    args = parser.parse_args()
    path = Path(os.getenv('SQLITE_PATH', 'data/community.sqlite3'))
    if not path.is_file():
        parser.error('Existing database required')
    db = Database(path)
    try:
        if args.command == 'recover-manager':
            if args.role_id <= 0 or not db.settings():
                parser.error('Positive role ID and initialized database required')
            old = db.settings()['DISCORD_MANAGER_ROLE_ID']
            with db.connection:
                db.connection.execute("UPDATE settings SET value=? WHERE key='DISCORD_MANAGER_ROLE_ID'", (str(args.role_id),))
                db.connection.execute('INSERT INTO audit(at,actor,action,target,outcome) VALUES(?,?,?,?,?)',
                                      (now(), args.operator, 'recover-manager', str(args.role_id), f'replaced {old}'))
            print('Manager role updated. Restart the bot to validate it against Discord.')
        else:
            args.destination.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation prevents overwriting an existing backup or live database.
            with args.destination.open('xb'):
                pass
            with sqlite3.connect(args.destination) as target:
                db.connection.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Backup integrity check failed')
            print(f'Backup written to {args.destination}')
    finally:
        db.close()


if __name__ == '__main__':
    main()
