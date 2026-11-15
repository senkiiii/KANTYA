"""Local-only account authorization helper. Run from the KANTYA folder.
Requires operating-system access to the KANTYA database file.
"""
import os, sys, sqlite3
from pathlib import Path
DB = Path(os.environ.get('KANTYA_DB', str(Path(__file__).resolve().parents[1] / 'kantya.db')))

def main():
    if len(sys.argv) == 2 and sys.argv[1] == 'list':
        with sqlite3.connect(DB) as db:
            print('Email | Role | Status')
            for email, role, status in db.execute('SELECT email,role,status FROM users WHERE email IS NOT NULL ORDER BY role,email'):
                print(f'{email} | {role} | {status}')
        return
    if len(sys.argv) == 3 and sys.argv[1] == 'approve-manager':
        email = sys.argv[2].strip().lower()
        if not email or '@' not in email:
            raise SystemExit('Enter a valid registered email.')
        print("Authorize Manager access ONLY after confirming this person's identity and permission.")
        confirm = input('Type APPROVE to authorize this Manager: ')
        if confirm != 'APPROVE':
            raise SystemExit('No changes made.')
        with sqlite3.connect(DB) as db:
            result = db.execute("UPDATE users SET status='active' WHERE lower(email)=? AND role='Manager' AND status='pending'", (email,))
            if result.rowcount != 1:
                raise SystemExit('No pending verified Manager found; no changes made.')
            db.commit()
        print('Manager account authorized. Log in with Manager role, password and email OTP.')
        return
    raise SystemExit('Usage: python account_admin.py list  OR  python account_admin.py approve-manager EMAIL')
if __name__ == '__main__':
    main()
