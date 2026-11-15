"""Provision email for an existing KANTYA account without logging or displaying secrets.
Usage: python configure_account_email.py USERNAME EMAIL
"""
import os, sqlite3, sys
from pathlib import Path
from email.utils import parseaddr
if len(sys.argv) != 3:
    print('Usage: python configure_account_email.py USERNAME EMAIL')
    raise SystemExit(2)
username, email = (sys.argv[1], sys.argv[2].strip().lower())
if parseaddr(email)[1] != email or '@' not in email or len(email) > 254:
    raise SystemExit('Invalid email address')
db_path = os.environ.get('KANTYA_DB', str(Path(__file__).resolve().parents[1] / 'kantya.db'))
with sqlite3.connect(db_path) as c:
    if 'email' not in {r[1] for r in c.execute('PRAGMA table_info(users)')}:
        raise SystemExit('First launch the revised KANTYA app once to apply its database migration')
    result = c.execute('UPDATE users SET email=?,must_change_password=1 WHERE username=?', (email, username))
    if result.rowcount != 1:
        raise SystemExit('Account not found')
print('Email configured; password update will be required after OTP login.')
