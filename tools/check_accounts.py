"""Read-only local account-state check. Never prints addresses, passwords or OTPs."""
import os
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = Path(os.environ.get('KANTYA_DB', str(root / 'kantya.db')))
if not path.exists():
    print('No application database found at:', path)
else:
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as connection:
        for table in ('users', 'registrations'):
            if table == 'users':
                print('Users by role/status:', connection.execute(
                    'SELECT role, status, COUNT(*) FROM users GROUP BY role, status ORDER BY role, status'
                ).fetchall())
            else:
                print('Pending email registrations:', connection.execute('SELECT COUNT(*) FROM registrations').fetchone()[0])
