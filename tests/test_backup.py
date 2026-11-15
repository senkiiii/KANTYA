"""Backup tests use only disposable SQLite files and synthetic configuration."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from tools.backup_admin import create_backup, restore_backup


class BackupTests(unittest.TestCase):
    def test_roundtrip_and_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_dir = root / 'live'
            store = root / 'vault'
            destination = root / 'restore'
            source_dir.mkdir()
            store.mkdir()
            destination.mkdir()
            source = source_dir / 'kantya.db'
            config = source_dir / '.env'
            config.write_text('KANTYA_ENV=test\n')
            with sqlite3.connect(source) as db:
                for name in ('users', 'records', 'forecast_runs', 'settings',
                             'web_sessions', 'otp_challenges', 'password_resets', 'registrations'):
                    db.execute(f'CREATE TABLE {name}(value TEXT)')
                    db.execute(f'INSERT INTO {name} VALUES(?)', (name,))
            cipher = Fernet(Fernet.generate_key())
            backup = store / create_backup(source, config, store, cipher)
            with sqlite3.connect(source) as db:
                db.execute('DELETE FROM records')
            with self.assertRaises(InvalidToken):
                restore_backup(backup, destination, Fernet(Fernet.generate_key()))
            corrupted = store / 'corrupted.backup'
            corrupted.write_bytes(backup.read_bytes()[:-1])
            with self.assertRaises(InvalidToken):
                restore_backup(corrupted, destination, cipher)
            self.assertEqual(list(destination.iterdir()), [])
            restore_backup(backup, destination, cipher)
            self.assertEqual((destination / 'configuration.env').read_text(), config.read_text())
            with sqlite3.connect(destination / 'kantya.db') as recovered:
                for name in ('users', 'records', 'forecast_runs', 'settings'):
                    self.assertEqual(recovered.execute(f'SELECT value FROM {name}').fetchone(), (name,))
                self.assertIsNone(recovered.execute('SELECT value FROM web_sessions').fetchone())
            with sqlite3.connect(source) as db:
                self.assertIsNone(db.execute('SELECT value FROM records').fetchone())
            with self.assertRaises(ValueError):
                create_backup(source, config, root / 'missing', cipher)
            with self.assertRaises(ValueError):
                restore_backup(backup, destination, cipher)
