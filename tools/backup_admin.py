"""Offline operator commands. Authorization is enforced by filesystem permissions."""
import argparse
import io
import json
import logging
import os
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def check_database(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('SQLite foreign key check failed')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'users', 'records', 'forecast_runs', 'settings'} <= tables:
            raise ValueError('Backup does not contain a KANTYA database')


def create_backup(source, config, storage, cipher):
    if not storage.is_dir() or source.parent == storage or storage in source.parents:
        raise ValueError('Use an existing protected backup directory separate from the database directory')
    with tempfile.TemporaryDirectory() as temp:
        snapshot = Path(temp) / 'kantya.db'
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as live:
            with closing(sqlite3.connect(snapshot)) as target:
                live.backup(target)
        check_database(snapshot)
        now = datetime.now(timezone.utc)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot, 'kantya.db')
            archive.write(config, 'configuration.env')
            archive.writestr('manifest.json', json.dumps({'format': 1, 'recovery_point_utc': now.isoformat()}))
        payload = buffer.getvalue()
        encrypted = cipher.encrypt(payload)
        if cipher.decrypt(encrypted) != payload:
            raise ValueError('Encrypted backup verification failed')
        filename = 'kantya-' + now.strftime('%Y%m%dT%H%M%S%fZ') + '.backup'
        with tempfile.NamedTemporaryFile(dir=storage, delete=False) as pending:
            path = Path(pending.name)
        try:
            path.write_bytes(encrypted)
            if cipher.decrypt(path.read_bytes()) != payload:
                raise ValueError('Stored backup verification failed')
            path.replace(storage / filename)
        finally:
            path.unlink(missing_ok=True)
    return filename


def restore_backup(backup, destination, cipher):
    if not destination.is_dir() or any(destination.iterdir()):
        raise ValueError('Restore requires a new, empty, protected staging directory')
    payload = cipher.decrypt(backup.read_bytes())
    with tempfile.TemporaryDirectory(dir=destination) as temp:
        staging = Path(temp)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            if manifest['format'] != 1:
                raise ValueError('Unsupported backup format')
            datetime.fromisoformat(manifest['recovery_point_utc'])
            for name in ('kantya.db', 'configuration.env'):
                (staging / name).write_bytes(archive.read(name))
                (staging / name).chmod(0o600)
        check_database(staging / 'kantya.db')
        with closing(sqlite3.connect(staging / 'kantya.db')) as db, db:
            for table in ('web_sessions', 'otp_challenges', 'password_resets', 'registrations'):
                db.execute(f'DELETE FROM {table}')
        check_database(staging / 'kantya.db')
        (staging / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        for name in ('kantya.db', 'configuration.env', 'manifest.json'):
            (staging / name).replace(destination / name)
    return manifest['recovery_point_utc']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--key-file', required=True, type=Path)
    parser.add_argument('--log', required=True, type=Path)
    commands = parser.add_subparsers(dest='action', required=True)
    backup = commands.add_parser('backup')
    backup.add_argument('--db', required=True, type=Path)
    backup.add_argument('--config', required=True, type=Path)
    backup.add_argument('--storage', required=True, type=Path)
    restore = commands.add_parser('restore')
    restore.add_argument('--backup', required=True, type=Path)
    restore.add_argument('--stage', required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(filename=args.log, level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    logging.info('%s started', args.action)
    try:
        cipher = Fernet(args.key_file.read_bytes().strip())
        if args.action == 'backup':
            result = create_backup(args.db.resolve(), args.config.resolve(), args.storage.resolve(), cipher)
        else:
            result = restore_backup(args.backup.resolve(), args.stage.resolve(), cipher)
    except (InvalidToken, OSError, ValueError, sqlite3.Error, KeyError, zipfile.BadZipFile) as error:
        logging.error('%s failed (%s)', args.action, type(error).__name__)
        parser.exit(1, f'{args.action} failed ({type(error).__name__}). Check the operator log and configuration.\n')
    logging.info('%s completed: %s', args.action, result)
    print(result)


if __name__ == '__main__':
    main()
