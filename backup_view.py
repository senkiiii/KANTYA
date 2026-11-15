"""In-app backup and staged recovery for designated operators."""
import os
import csv
import io
import json
import hmac
import time
import re
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from contextlib import closing
from pathlib import Path
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken
from flask import abort, current_app, flash, redirect, render_template, request, session, url_for, send_file, Response

from database import db
from tools.backup_admin import create_backup, restore_backup

BACKUP_NAME = re.compile(r'kantya-[0-9]{8}T[0-9]{12,18}Z\.backup')
RESTORE_LOCK = threading.Lock()


def can_manage_backups():
    if session.get('role') != 'Manager' or not session.get('uid'):
        return False
    operators = {value.strip().casefold() for value in
                 os.environ.get('KANTYA_BACKUP_OPERATORS', '').split(',') if value.strip()}
    with closing(db()) as connection:
        row = connection.execute(
            """SELECT email, username, (SELECT value FROM settings WHERE key='backup_owner') AS owner
               FROM users WHERE id=? AND role='Manager' AND status='active'""",
            (session['uid'],),
        ).fetchone()
    return bool(row and row['email'] and (row['owner'] == row['username'] or row['email'].casefold() in operators))


def backup_configuration():
    source = Path(current_app.config['DATABASE']).resolve()
    configuration = Path(current_app.config['CONFIGURATION_FILE'])
    vault_path = os.environ.get('KANTYA_BACKUP_VAULT')
    key_path = os.environ.get('KANTYA_BACKUP_KEY_FILE')
    # Local development setup: initialize persistent, OS-protected storage on first use.
    # Production deployments must supply externally managed key and vault paths.
    if not vault_path and not key_path and os.environ.get('KANTYA_ENV') != 'production':
        instance=Path(current_app.instance_path).resolve()
        instance.mkdir(parents=True,exist_ok=True)
        try:instance.chmod(0o700)
        except OSError:pass
        default_vault=instance/'encrypted_backups'
        default_vault.mkdir(exist_ok=True)
        try:default_vault.chmod(0o700)
        except OSError:pass
        default_key=instance/'backup.key'
        if not default_key.exists():
            fd=os.open(str(default_key),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as writer:writer.write(Fernet.generate_key()+b'\n')
        vault_path=str(default_vault)
        key_path=str(default_key)
        if not configuration.exists():
            # A configuration snapshot must exist for integrity-preserving backup.
            configuration.touch(mode=0o600,exist_ok=True)
    vault = Path(vault_path).resolve() if vault_path else None
    key = Path(key_path).resolve() if key_path else None
    static = Path(current_app.static_folder).resolve()
    configured = bool(vault and key and vault.is_dir() and key.is_file()
                      and vault not in source.parents
                      and vault != static and static not in vault.parents
                      and key != static and static not in key.parents
                      and key != vault and vault not in key.parents)
    return source, configuration, vault, key, configured


def backup_panel():
    _, configuration, vault, _, configured = backup_configuration()
    history = sorted(
        (path.name for path in vault.iterdir()
         if BACKUP_NAME.fullmatch(path.name) and not path.is_symlink() and path.is_file()),
        reverse=True,
    ) if configured else []
    with closing(db()) as connection:
        deleted=[dict(r) for r in connection.execute('''
            SELECT d.id,d.record_id,d.deleted_at,d.reason,d.snapshot,u.username AS deleted_by_name
            FROM deleted_records d LEFT JOIN users u ON u.id=d.deleted_by
            ORDER BY d.id DESC LIMIT 100''')]
    for row in deleted:
        snapshot=json.loads(row.pop('snapshot'))
        row.update(item=snapshot.get('item'),day=snapshot.get('day'),period=snapshot.get('period'))
    return dict(configured=configured, history=history, can_create=configuration.is_file(),deleted=deleted)


def download_backup(name):
    if not can_manage_backups():abort(403)
    if not BACKUP_NAME.fullmatch(name):abort(404)
    _,_,vault,_,configured=backup_configuration()
    if not configured:abort(503)
    archive=vault/name
    if archive.is_symlink() or not archive.is_file():abort(404)
    return send_file(archive,as_attachment=True,download_name=name,mimetype='application/octet-stream')


def download_records_csv():
    """Manager-only CSV copy of current and soft-deleted food records.

    This is a records export, not an encrypted full-system database archive.
    """
    columns = ('status','record_id','day','item','period','prepared','sold','leftover',
               'waste','created_at','deleted_at','deletion_reason','deleted_by')
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(columns)
    with db() as c:
        for row in c.execute("SELECT id,day,item,period,prepared,sold,waste,created_at FROM records ORDER BY day,item,period,id"):
            writer.writerow(('active',row['id'],row['day'],row['item'],row['period'],
                             row['prepared'],row['sold'],row['prepared']-row['sold'],
                             row['waste'],row['created_at'],'','',''))
        for row in c.execute("SELECT d.record_id,d.snapshot,d.deleted_at,d.reason,u.email AS deleted_by_email FROM deleted_records d LEFT JOIN users u ON d.deleted_by=u.id ORDER BY d.id"):
            snapshot=json.loads(row['snapshot'])
            writer.writerow(('deleted',row['record_id'],snapshot['day'],snapshot['item'],
                             snapshot['period'],snapshot['prepared'],snapshot['sold'],
                             snapshot['prepared']-snapshot['sold'],snapshot['waste'],
                             snapshot.get('created_at',''),row['deleted_at'],
                             row['reason'],row['deleted_by_email'] or ''))
    return Response(('\ufeff'+output.getvalue()).encode('utf-8'),
                    mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition':'attachment; filename="KANTYA_Records_Backup.csv"',
                             'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff'})

def backup_recovery():
    if not can_manage_backups():abort(403)
    # The panel opens over Food Records, retaining the original workspace.
    if request.method=='GET':
        from records import record_panel
        return record_panel('backup_panel.html',backup=backup_panel(),r=None)
    if not hmac.compare_digest(str(request.form.get('csrf', '')), str(session.get('csrf', ''))):
        abort(400)
    action=request.form.get('action')
    if action not in ('create','upload','verify','restore','restore_record','download_csv'):abort(400)
    if action=='download_csv':
        return download_records_csv()
    if action=='restore_record':
        try:
            record_id=int(request.form.get('deleted_id',''))
            if request.form.get('confirm')!='yes':
                raise ValueError('Confirm the record restoration first.')
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                deleted=c.execute('SELECT * FROM deleted_records WHERE id=?',(record_id,)).fetchone()
                if deleted is None:abort(404)
                row=json.loads(deleted['snapshot'])
                # Do not overwrite active records, and preserve the original record ID.
                if c.execute('SELECT 1 FROM records WHERE id=? OR (item=? AND day=? AND period=?)',
                             (row['id'],row['item'],row['day'],row['period'])).fetchone():
                    raise ValueError('Restoration blocked: an active record with the same ID or menu entry exists.')
                cols=('id','item','day','period','prepared','sold','waste','creator','created_at','source')
                c.execute('INSERT INTO records('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')',
                          tuple(row[k] for k in cols))
                for correction in json.loads(deleted['corrections']):
                    fields=('record_id','manager_id','old_values','new_values','reason','corrected_at')
                    c.execute('INSERT INTO corrections('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',
                              tuple(correction[k] for k in fields))
                c.execute('DELETE FROM deleted_records WHERE id=?',(record_id,))
                c.execute('''CREATE TABLE IF NOT EXISTS record_recovery_audit (
                    id INTEGER PRIMARY KEY,record_id INTEGER,deleted_snapshot_id INTEGER,
                    restored_by INTEGER,restored_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
                c.execute('INSERT INTO record_recovery_audit(record_id,deleted_snapshot_id,restored_by) VALUES(?,?,?)',
                          (row['id'],record_id,session['uid']))
            flash('Food record restored successfully.')
        except (ValueError,KeyError,sqlite3.Error,TypeError) as exc:
            current_app.logger.warning('Record restoration failed: %s',type(exc).__name__)
            flash(str(exc) if isinstance(exc,ValueError) else 'Unable to restore record. Check database integrity.', 'error')
        return redirect(url_for('backup_recovery'),code=303)
    name=request.form.get('backup','')
    source,configuration,vault,key,configured=backup_configuration()
    if not configured or (action=='create' and not configuration.is_file()):
        flash('Backup requires a configured protected vault, encryption key and configuration file.', 'error')
        return redirect(url_for('index'),code=303)
    if action in ('verify','restore') and (not BACKUP_NAME.fullmatch(name) or request.form.get('confirm')!='yes'):
        abort(400)
    if action=='restore' and request.form.get('typed_confirmation','')!='RESTORE':
        flash('Type RESTORE exactly to authorize full database replacement.', 'error')
        return redirect(url_for('index'),code=303)
    if not RESTORE_LOCK.acquire(blocking=False):
        flash('Another restoration is in progress.', 'error')
        return redirect(url_for('index'),code=303)
    stage=None
    try:
        cipher=Fernet(key.read_bytes().strip())
        if action=='upload':
            uploaded=request.files.get('backup_file')
            if not uploaded or not uploaded.filename.lower().endswith('.backup'):
                raise ValueError('Select an encrypted KANTYA .backup archive')
            payload=uploaded.read()
            if not payload or len(payload)>5*1024*1024:
                raise ValueError('Invalid or oversized backup file')
            safe_name='kantya-'+datetime.utcnow().strftime('%Y%m%dT%H%M%S%fZ')+'.backup'
            stage=Path(tempfile.mkdtemp(prefix='kantya-upload-',dir=vault))
            pending=stage/safe_name
            pending.write_bytes(payload)
            verification=stage/'verified';verification.mkdir()
            restore_backup(pending,verification,cipher)
            pending.replace(vault/safe_name)
            flash('Encrypted backup uploaded and verified. Select it from Backup History to restore.')
            name=safe_name
        elif action=='create':
            name=create_backup(source,configuration,vault,cipher)
            current_app.logger.info('Encrypted backup created by Manager %s: %s',session.get('uid'),name)
            return send_file(vault/name, as_attachment=True, download_name=name,
                             mimetype='application/octet-stream')
        else:
            archive=vault/name
            if archive.is_symlink() or not archive.is_file():abort(404)
            stage=Path(tempfile.mkdtemp(prefix='kantya-restore-',dir=vault))
            restored_time=restore_backup(archive,stage,cipher)
            with closing(sqlite3.connect((stage/'kantya.db').as_uri()+'?mode=ro',uri=True)) as verify:
                users=verify.execute('SELECT COUNT(*) FROM users').fetchone()[0]
                records=verify.execute('SELECT COUNT(*) FROM records').fetchone()[0]
            if action=='verify':
                flash(f'Backup verified (UTC recovery point: {restored_time}; {users} users, {records} records). The active database was not changed.')
            else:
                # Guaranteed rollback anchor before any live database modification.
                safety_name=create_backup(source,configuration,vault,cipher)
                # SQLite backup API handles consistent DB replacement; do not copy a live DB file.
                with closing(sqlite3.connect(stage/'kantya.db')) as recovered:
                    with closing(sqlite3.connect(source,timeout=15)) as active:
                        recovered.backup(active)
                # Sign-out is forced because staged restore invalidates all stored sessions.
                session.clear()
                flash(f'Full database restored from {name}; pre-restore encrypted safety backup: {safety_name}. Sign in again.')
                current_app.logger.warning('Manager completed full database restore: %s; safety=%s',name,safety_name)
                return redirect(url_for('choose_role'),code=303)
        current_app.logger.info('Backup action %s completed by %s: %s',action,session.get('uid'),name)
    except (InvalidToken,OSError,ValueError,sqlite3.Error,KeyError,zipfile.BadZipFile) as exc:
        current_app.logger.error('Backup %s failed (%s)',action,type(exc).__name__)
        flash('Backup action failed. Current data was not intentionally replaced; verify database integrity before retrying.', 'error')
    finally:
        if stage:shutil.rmtree(stage,ignore_errors=True)
        RESTORE_LOCK.release()
    return redirect(url_for('index'),code=303)
