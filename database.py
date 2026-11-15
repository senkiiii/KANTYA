"""SQLite connections, schema initialization, and migrations."""

import os
import sqlite3

from flask import current_app
from werkzeug.security import generate_password_hash


def db():
    c = sqlite3.connect(current_app.config['DATABASE'])
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c


def init():
    with db() as c:
        c.executescript("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('Manager','Staff')));\nCREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY,item TEXT NOT NULL,day TEXT NOT NULL,period TEXT NOT NULL CHECK(period IN ('Breakfast','Lunch','Snack')),prepared INTEGER NOT NULL CHECK(prepared>=0),sold INTEGER NOT NULL CHECK(sold>=0 AND sold<=prepared),waste INTEGER NOT NULL CHECK(waste>=0 AND waste<=prepared-sold),creator INTEGER REFERENCES users(id),created_at TEXT DEFAULT CURRENT_TIMESTAMP,source TEXT NOT NULL,UNIQUE(item,day,period));\nCREATE TABLE IF NOT EXISTS corrections(id INTEGER PRIMARY KEY,record_id INTEGER REFERENCES records(id),manager_id INTEGER REFERENCES users(id),old_values TEXT,new_values TEXT,reason TEXT NOT NULL,corrected_at TEXT DEFAULT CURRENT_TIMESTAMP);")
        columns = {r['name'] for r in c.execute('PRAGMA table_info(users)')}
        if 'email' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN email TEXT')
        if 'must_change_password' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0')
        if 'status' not in columns:
            c.execute("ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
        if 'session_version' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email_lower ON users(lower(email)) WHERE email IS NOT NULL')
        c.execute("CREATE TABLE IF NOT EXISTS registrations(id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL, requested_role TEXT NOT NULL CHECK(requested_role IN ('Manager','Staff')), code_hash TEXT NOT NULL, expires_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)")
        c.execute('CREATE TABLE IF NOT EXISTS login_attempts(username TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0, first_attempt INTEGER NOT NULL DEFAULT 0, locked_until INTEGER NOT NULL DEFAULT 0)')
        c.execute('CREATE TABLE IF NOT EXISTS password_resets(id INTEGER PRIMARY KEY, email TEXT NOT NULL COLLATE NOCASE, code_hash TEXT NOT NULL, expires_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, consumed INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS otp_challenges(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), code_hash TEXT NOT NULL, expires_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, consumed INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)')
        if os.environ.get('KANTYA_SEED_DEMO') == '1' and os.environ.get('KANTYA_ENV') != 'production':
            for name, role, pw in [('manager', 'Manager', 'Manager123!'), ('staff', 'Staff', 'Staff123!')]:
                c.execute('INSERT OR IGNORE INTO users(username,password,role,must_change_password) VALUES(?,?,?,1)', (name, generate_password_hash(pw), role))
        c.execute('CREATE TABLE IF NOT EXISTS web_sessions(token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, last_seen INTEGER NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS otp_requests(scope TEXT NOT NULL, requested_at INTEGER NOT NULL)')
        c.execute('CREATE INDEX IF NOT EXISTS ix_otp_requests ON otp_requests(scope, requested_at)')


def record_backup_owner(connection):
    connection.execute("""
        INSERT OR IGNORE INTO settings(key, value)
        SELECT 'backup_owner', username FROM users
        WHERE role='Manager' AND status='active' AND trim(email) <> ''
        ORDER BY id LIMIT 1
    """)


def migrate():
    with db() as c:
        c.executescript('CREATE TABLE IF NOT EXISTS forecast_runs(id INTEGER PRIMARY KEY,cutoff TEXT,generated_at TEXT,manager_id INTEGER,method TEXT,signature TEXT,results TEXT);\nCREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);')
        c.execute("""CREATE TABLE IF NOT EXISTS deleted_records (
            id INTEGER PRIMARY KEY,
            record_id INTEGER NOT NULL,
            snapshot TEXT NOT NULL,
            corrections TEXT NOT NULL,
            deleted_by INTEGER NOT NULL REFERENCES users(id),
            reason TEXT NOT NULL,
            deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute('CREATE INDEX IF NOT EXISTS ix_deleted_records_record_id ON deleted_records(record_id)')

        c.execute("CREATE TABLE IF NOT EXISTS account_audit(id INTEGER PRIMARY KEY,user_id INTEGER,actor_id INTEGER,action TEXT NOT NULL,reason TEXT,created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        if c.execute("SELECT 1 FROM users WHERE role='Manager' AND status='active' LIMIT 1").fetchone():
            c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('manager_initialized','yes')")
        c.execute('CREATE TABLE IF NOT EXISTS import_staging(token TEXT PRIMARY KEY,user_id INTEGER NOT NULL,payload TEXT NOT NULL,expires_at INTEGER NOT NULL)')
        record_backup_owner(c)
