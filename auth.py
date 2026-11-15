"""Authentication, account administration, and session handling."""

import hashlib
import hmac
import os
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from functools import wraps
from pathlib import Path

from flask import current_app, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from database import db, record_backup_owner

LOCK_WINDOW = 900
OTP_TTL = 300
SESSION_IDLE = 900
OTP_MAX_ATTEMPTS = 5
PASSWORD_MIN_LENGTH = 6
PASSWORD_MAX_LENGTH = 30
PASSWORD_MESSAGE = 'Use 6–30 characters, including uppercase, lowercase, number and symbol.'
COMMON_PASSWORDS = {
    'password1234', 'password12345', 'password123!', 'qwerty123456',
    '123456789012', '1234567890123456', 'administrator', 'letmeinplease',
    'iloveyou1234', 'changeme1234', 'kantyapassword', 'kantya123456',
}


def require_auth(manager=False):

    def deco(f):

        @wraps(f)
        def wrapped(*a, **kw):
            if not session.get('uid'):
                return redirect(url_for('choose_role'))
            if manager and session.get('role') != 'Manager':
                return ('Forbidden', 403)
            if request.method == 'POST' and request.form.get('csrf') != session.get('csrf'):
                return ('Invalid form token', 400)
            return f(*a, **kw)
        return wrapped
    return deco


def reserve_otp_request(c, scope, now, cooldown, cap, window):
    c.execute('DELETE FROM otp_requests WHERE scope=? AND requested_at<=?', (scope, now - window))
    recent = c.execute('SELECT COUNT(*),MAX(requested_at) FROM otp_requests WHERE scope=?', (scope,)).fetchone()
    if recent[0] >= cap or (recent[1] is not None and now - recent[1] < cooldown):
        return False
    c.execute('INSERT INTO otp_requests VALUES(?,?)', (scope, now))
    return True


def resend_policy():
    try:
        cooldown = int(os.environ.get('KANTYA_OTP_RESEND_COOLDOWN', '60'))
        cap = int(os.environ.get('KANTYA_OTP_RESEND_CAP', '3'))
        window = int(os.environ.get('KANTYA_OTP_RESEND_WINDOW', '900'))
    except ValueError:
        return None
    return (cooldown, cap, window) if 0 < cooldown <= window and cap > 0 else None


def csrf_token():
    if not session.get('csrf'):
        session['csrf'] = secrets.token_hex(24)
    return session['csrf']


def password_valid(value):
    if not isinstance(value, str) or not PASSWORD_MIN_LENGTH <= len(value) <= PASSWORD_MAX_LENGTH:
        return False
    if not all((any(c.isupper() for c in value), any(c.islower() for c in value), any(c.isdigit() for c in value), any(not c.isalnum() for c in value))):
        return False
    candidate = value.strip().casefold()
    if not candidate or candidate in COMMON_PASSWORDS or len(set(candidate)) == 1:
        return False
    denylist = os.environ.get('KANTYA_PASSWORD_DENYLIST')
    if denylist:
        digest = hashlib.sha1(value.encode('utf-8')).hexdigest().upper()
        try:
            with Path(denylist).open(encoding='ascii') as entries:
                return not any(line.partition(':')[0].strip().upper() == digest for line in entries)
        except (OSError, UnicodeError):
            current_app.logger.error('Cannot read the configured password denylist.')
            return False
    return True


def mail_otp(recipient, code, purpose='login'):
    if purpose not in ('login', 'signup', 'reset'):
        raise ValueError('Invalid verification purpose')
    host = os.environ.get('KANTYA_SMTP_HOST')
    sender = os.environ.get('KANTYA_SMTP_FROM')
    if not host or not sender or (not recipient):
        raise RuntimeError('Email OTP is not configured. Ask your administrator to configure SMTP and account email.')
    username = os.environ.get('KANTYA_SMTP_USER')
    password = os.environ.get('KANTYA_SMTP_PASSWORD')
    if host.lower() == 'smtp.gmail.com':
        password = ''.join((password or '').split())
        if not username or not password:
            raise RuntimeError('Missing Gmail SMTP username or app password')
    elif bool(username) != bool(password):
        raise RuntimeError('Incomplete SMTP authentication configuration')
    msg = EmailMessage()
    msg['From'] = sender
    msg['To'] = recipient
    msg['Subject'] = 'KANTYA ' + ('registration' if purpose == 'signup' else 'password reset' if purpose == 'reset' else 'login') + ' verification code'
    msg.set_content(f'Your KANTYA verification code is {code}. It expires in {OTP_TTL // 60} minutes. Do not share it.')
    port = int(os.environ.get('KANTYA_SMTP_PORT', '587'))
    with smtplib.SMTP(host, port, timeout=10) as server:
        server.starttls(context=ssl.create_default_context())
        if username and password:
            server.login(username, password)
        server.send_message(msg)


def otp_digest(uid, code):
    return hmac.new(current_app.secret_key.encode(), f'{uid}:{code}'.encode(), hashlib.sha256).hexdigest()


def valid_email(value):
    import re
    value = value.strip().lower()
    return value if len(value) <= 254 and re.fullmatch("[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\\.[A-Za-z]{2,}", value) else None


def manager_initialized(c):
    return c.execute("SELECT 1 FROM settings WHERE key='manager_initialized' AND value='yes'").fetchone() is not None


def has_active_manager(c):
    return c.execute(
        "SELECT 1 FROM users WHERE role='Manager' AND status='active' LIMIT 1"
    ).fetchone() is not None


def can_receive_login_otp(c, user):
    return user['status'] in ('active', 'unverified') or (
        user['status'] == 'pending' and user['role'] == 'Manager' and not manager_initialized(c)
    )


def choose_role():
    if session.get('uid'):
        return redirect('/dashboard')
    csrf_token()
    return render_template('choose_role.html')


def login():
    if session.get('uid'):
        return redirect('/dashboard')
    role = request.form.get('role', request.args.get('role', ''))
    if role not in ('Manager', 'Staff'):
        return redirect(url_for('choose_role'))
    token = csrf_token()
    login_identity = session.get('login_identity', {})
    email = login_identity.get('email', '') if login_identity.get('role') == role else ''
    if request.method == 'POST':
        if not hmac.compare_digest(str(request.form.get('csrf', '')), token):
            return ('Invalid form token', 400)
        email = valid_email(request.form.get('email', ''))
        password = request.form.get('password', '')
        now = int(time.time())
        key = email or 'invalid:' + hashlib.sha256((request.remote_addr or 'unknown').encode()).hexdigest()[:24]
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            attempt = c.execute('SELECT * FROM login_attempts WHERE username=?', (key,)).fetchone()
            if attempt and attempt['locked_until'] > now:
                flash('Too many attempts. Try again later.', 'error')
                return (render_template('login.html', selected_role=role, entered_email=email or ''), 429)
            u = c.execute('SELECT * FROM users WHERE lower(email)=?', (email,)).fetchone() if email else None
            # Check the password before showing account-specific status information.
            # This avoids calling a correct password 'incorrect' when the selected role
            # or approval state is what actually blocks the session.
            password_matches = bool(u and check_password_hash(u['password'], password))
            if not password_matches:
                failures = attempt['failures'] + 1 if attempt and now - attempt['first_attempt'] < LOCK_WINDOW else 1
                first = attempt['first_attempt'] if attempt and now - attempt['first_attempt'] < LOCK_WINDOW else now
                c.execute('INSERT INTO login_attempts(username,failures,first_attempt,locked_until) VALUES(?,?,?,?) ON CONFLICT(username) DO UPDATE SET failures=excluded.failures,first_attempt=excluded.first_attempt,locked_until=excluded.locked_until', (key, failures, first, now + LOCK_WINDOW if failures >= 5 else 0))
                flash('Incorrect email or password. Please try again.', 'error')
                return (render_template('login.html', selected_role=role, entered_email=email or ''), 401)
            if u['role'] != role:
                flash('Your password is correct, but this account is not authorized for the selected role. Go back and select the role assigned to your account.', 'error')
                return (render_template('login.html', selected_role=role, entered_email=email or ''), 403)
            if not can_receive_login_otp(c, u):
                if u['status'] == 'pending':
                    flash('Your account is awaiting authorization. Contact the Manager or system administrator.', 'error')
                else:
                    flash('Your account is not active. Contact the system administrator.', 'error')
                return render_template('login.html', selected_role=role, entered_email=email or ''), 403
            if not u['email']:
                flash('Your account has no registered email for login OTP. Contact the authorized administrator to enroll an email address.', 'error')
                return (render_template('login.html', selected_role=role, entered_email=email or ''), 403)
            c.execute('DELETE FROM login_attempts WHERE username=?', (key,))
            if not reserve_otp_request(c, 'login:' + email, now, 15, 5, 900):
                flash('Please wait before requesting another login code.', 'error')
                return (render_template('login.html', selected_role=role, entered_email=email or ''), 429)
            code = f'{secrets.randbelow(1000000):06d}'
            try:
                mail_otp(u['email'], code)
            except (RuntimeError, ValueError, OSError, smtplib.SMTPException) as exc:
                if isinstance(exc, smtplib.SMTPResponseException):
                    current_app.logger.warning('Login OTP SMTP failure: %s (code %d)', type(exc).__name__, exc.smtp_code)
                else:
                    current_app.logger.warning('Login OTP delivery failed: %s. Check SMTP configuration and connectivity.', type(exc).__name__)
                return render_template('login.html', selected_role=role,
                                       entered_email=email, otp_delivery_failed=True), 503
            c.execute('UPDATE otp_challenges SET consumed=1 WHERE user_id=?', (u['id'],))
            issued = int(time.time())
            challenge_id = c.execute('INSERT INTO otp_challenges(user_id,code_hash,expires_at,created_at) VALUES(?,?,?,?)', (u['id'], otp_digest(u['id'], code), issued + OTP_TTL, issued)).lastrowid
        session.clear()
        session['pending_uid'] = u['id']
        session['pending_role'] = u['role']
        session['pending_challenge'] = challenge_id
        session['csrf'] = secrets.token_hex(24)
        return redirect(url_for('verify_otp'))
    return render_template('login.html', selected_role=role, entered_email=email or '')


def signup():
    if session.get('uid'):
        return redirect('/dashboard')
    role = request.form.get('role', request.args.get('role', ''))
    if role not in ('Manager', 'Staff'):
        return redirect(url_for('choose_role'))
    token = csrf_token()
    if request.method == 'POST':
        if not hmac.compare_digest(str(request.form.get('csrf', '')), token):
            return ('Invalid form token', 400)
        email = valid_email(request.form.get('email', ''))
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if not email:
            flash('Enter a valid email address.', 'error')
        elif not password_valid(password):
            flash(PASSWORD_MESSAGE, 'error')
        elif password != confirm:
            flash('Passwords do not match.', 'error')
        else:
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                if c.execute('SELECT 1 FROM users WHERE lower(email)=?', (email,)).fetchone():
                    flash('This email already has an account. Sign in under your original role with your existing password.', 'error')
                    return redirect(url_for('login', role=role))
                c.execute(
                    'INSERT INTO users(username,password,role,email,must_change_password,status) '
                    "VALUES(?,?,?,?,0,'unverified')",
                    ('user_' + secrets.token_hex(8), generate_password_hash(password), role, email),
                )
                c.execute('DELETE FROM registrations WHERE email=?', (email,))
                # Attempts for a previously unknown email must not lock a new account.
                c.execute('DELETE FROM login_attempts WHERE username=?', (email,))
            session.clear()
            session['login_identity'] = {'role': role, 'email': email}
            flash('Account created. Log in to receive your email verification code.')
            return redirect(url_for('login', role=role))
    with db() as c:
        approval_required = role == 'Staff' or (role == 'Manager' and manager_initialized(c))
    return render_template('signup.html', selected_role=role, approval_required=approval_required)


def verify_registration():
    # Old registration links now lead to the role selector.
    return redirect(url_for('choose_role'))


def approvals():
    """Manager-only user administration, confirmation and audit support."""
    if request.method == 'POST':
        uid = request.form.get('user_id', '')
        decision = request.form.get('decision', '')
        reason = request.form.get('reason', '').strip()
        if not uid.isdecimal() or len(uid) > 18 or decision not in ('active','rejected','deactivated','reactivated'):
            return ('Invalid request', 400)
        if request.form.get('confirm') != 'yes':
            flash('Review and confirm the account action.', 'error')
            return redirect(url_for('reports', tab='users'))
        if decision in ('rejected','deactivated') and not 1 <= len(reason) <= 1000:
            flash('A reason is required (up to 1,000 characters).', 'error')
            return redirect(url_for('reports', tab='users'))
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            current = c.execute('SELECT * FROM users WHERE id=?', (int(uid),)).fetchone()
            if not current or int(uid) == session.get('uid'):
                flash('This account action is not permitted.', 'error')
                return redirect(url_for('reports', tab='users'))
            status = {'active':'active','rejected':'rejected','deactivated':'inactive','reactivated':'active'}[decision]
            valid = ((decision in ('active','rejected') and current['status']=='pending') or
                     (decision=='deactivated' and current['status']=='active') or
                     (decision=='reactivated' and current['status']=='inactive'))
            if current['role']=='Manager' and decision=='deactivated':
                others = c.execute("SELECT COUNT(*) FROM users WHERE role='Manager' AND status='active' AND id<>?",(int(uid),)).fetchone()[0]
                valid = valid and bool(others)
            if valid:
                c.execute('UPDATE users SET status=?, session_version=session_version+1 WHERE id=?',(status,int(uid)))
                c.execute('DELETE FROM web_sessions WHERE user_id=?',(int(uid),))
                c.execute('INSERT INTO account_audit(user_id,actor_id,action,reason) VALUES(?,?,?,?)', (int(uid),session['uid'],decision,reason))
            else:
                flash('This request is not eligible for the selected action.', 'error')
                return redirect(url_for('reports', tab='users'))
        flash('Account action saved successfully.')
        return redirect(url_for('reports', tab='users'))
    return redirect(url_for('reports', tab='users'))


def verify_otp():
    uid = session.get('pending_uid')
    challenge_id = session.get('pending_challenge')
    if not uid or not challenge_id:
        return redirect(url_for('choose_role'))
    if request.method == 'POST':
        if not hmac.compare_digest(str(request.form.get('csrf', '')), csrf_token()):
            return ('Invalid form token', 400)
        code = request.form.get('otp', '').strip()
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            challenge = c.execute('SELECT * FROM otp_challenges WHERE id=? AND user_id=? AND consumed=0', (challenge_id, uid)).fetchone()
            if not challenge or challenge['expires_at'] <= int(time.time()) or challenge['attempts'] >= OTP_MAX_ATTEMPTS:
                session.clear()
                flash('Verification expired or unavailable. Sign in again.', 'error')
                return redirect(url_for('choose_role'))
            c.execute('UPDATE otp_challenges SET attempts=attempts+1 WHERE id=?', (challenge['id'],))
            if not (len(code) == 6 and code.isdigit() and hmac.compare_digest(challenge['code_hash'], otp_digest(uid, code))):
                flash('Invalid verification code.', 'error')
                return (render_template('verify_otp.html', resend_enabled=bool(resend_policy())), 400)
            c.execute('UPDATE otp_challenges SET consumed=1 WHERE id=?', (challenge['id'],))
            user = c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
            if not user or not can_receive_login_otp(c, user):
                session.clear()
                flash('This account cannot sign in. Manager accounts awaiting approval must be authorized first.')
                return redirect(url_for('choose_role'))
            if user['status'] != 'active':
                if user['role'] == 'Manager' and manager_initialized(c):
                    c.execute("UPDATE users SET status='pending' WHERE id=?", (uid,))
                    session.clear()
                    flash('Email verified. An active Manager must approve your account before you can sign in.')
                    return redirect(url_for('login', role='Manager'))
                if user['role'] == 'Staff':
                    c.execute("UPDATE users SET status='pending' WHERE id=?", (uid,))
                    session.clear()
                    flash('Email verified. Wait for Manager approval before signing in.')
                    return redirect(url_for('login', role='Staff'))
                c.execute("UPDATE users SET status='active' WHERE id=?", (uid,))
            if user['role'] == 'Manager':
                c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('manager_initialized','yes')")
                record_backup_owner(c)
            token = secrets.token_hex(32)
            c.execute('DELETE FROM web_sessions WHERE last_seen<=?', (int(time.time()) - SESSION_IDLE,))
            c.execute('INSERT INTO web_sessions VALUES(?,?,?)', (token, uid, int(time.time())))
        session.clear()
        session.update(login_token=token, uid=user['id'], role=user['role'], username=user['username'], session_version=user['session_version'], csrf=secrets.token_hex(24))
        session.permanent = True
        if user['must_change_password']:
            return redirect(url_for('change_password'))
        return redirect('/dashboard')
    return render_template('verify_otp.html', resend_enabled=bool(resend_policy()))


def resend_otp():
    uid = session.get('pending_uid')
    challenge_id = session.get('pending_challenge')
    if not uid or not challenge_id:
        return redirect(url_for('choose_role'))
    if not hmac.compare_digest(request.form.get('csrf', ''), csrf_token()):
        return ('Invalid form token', 400)
    policy = resend_policy()
    if not policy:
        return ('OTP resend settings have not been configured.', 503)
    now = int(time.time())
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        challenge = c.execute(
            "SELECT o.*,u.email,u.status,u.role FROM otp_challenges o JOIN users u ON u.id=o.user_id "
            "WHERE o.id=? AND o.user_id=? AND o.consumed=0",
            (challenge_id, uid)).fetchone()
        if (not challenge or not can_receive_login_otp(c, challenge)
                or challenge['expires_at'] <= now or challenge['attempts'] >= OTP_MAX_ATTEMPTS):
            return ('Sign in again to request a fresh login code.', 400)
        if not reserve_otp_request(c, 'resend:' + challenge['email'], now, *policy):
            flash('Resend limit reached. Try again later.', 'error')
            return (render_template('verify_otp.html', resend_enabled=True), 429)
        code = f'{secrets.randbelow(1000000):06d}'
        while hmac.compare_digest(challenge['code_hash'], otp_digest(uid, code)):
            code = f'{secrets.randbelow(1000000):06d}'
        try:
            mail_otp(challenge['email'], code)
        except (RuntimeError, ValueError, OSError, smtplib.SMTPException) as exc:
            current_app.logger.warning('OTP resend failed: %s', type(exc).__name__)
            flash('The new code could not be sent. Try again later.', 'error')
            return (render_template('verify_otp.html', resend_enabled=True), 503)
        c.execute('UPDATE otp_challenges SET code_hash=?,expires_at=? WHERE id=?',
                  (otp_digest(uid, code), int(time.time()) + OTP_TTL, challenge_id))
    flash('A new login code was accepted for sending.')
    return redirect(url_for('verify_otp'))


def forgot_password():
    csrf_token()
    if request.method == 'POST':
        if not hmac.compare_digest(str(request.form.get('csrf', '')), csrf_token()):
            return ('Invalid form token', 400)
        if request.form.get('method', 'email') != 'email':
            flash('Enter an email address to request a reset code.', 'error')
            return (render_template('forgot_password.html'), 400)
        email = valid_email(request.form.get('email', ''))
        if not email:
            flash('Enter a valid email address.', 'error')
            return (render_template('forgot_password.html'), 400)
        now = int(time.time())
        reset_id = None
        delivery_failed = False
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            # Deliver a verification challenge to the supplied email, without first
            # requiring a matching registered account. An unmatched address can
            # verify mailbox ownership but cannot reset another user's password.
            allowed = reserve_otp_request(c, 'reset:' + email, now, 60, 5, 900)
            if allowed:
                code = f'{secrets.randbelow(1000000):06d}'
                try:
                    mail_otp(email, code, 'reset')
                except (RuntimeError, ValueError, OSError, smtplib.SMTPException) as exc:
                    delivery_failed = True
                    current_app.logger.error('Password recovery mail failed (%s); check SMTP setup.', type(exc).__name__)
                else:
                    c.execute('UPDATE password_resets SET consumed=1 WHERE email=?', (email,))
                    reset_id = c.execute(
                        'INSERT INTO password_resets(email,code_hash,expires_at,created_at) VALUES(?,?,?,?)',
                        (email, otp_digest('reset:' + email, code), now + OTP_TTL, now),
                    ).lastrowid
        if delivery_failed:
            # Do not open an OTP form for a code that was never sent.
            flash('Unable to send a reset code. Please contact the system administrator to check the email service, then try again.', 'error')
            return render_template('forgot_password.html'), 503
        if reset_id is None:
            # Do not expose whether the submitted account exists or is temporarily rate-limited.
            flash('Please wait one minute before requesting another code. Check your inbox or Spam folder.', 'info')
            return render_template('forgot_password.html')
        session.clear()
        session['recovery_email'] = email
        session['recovery_challenge'] = reset_id
        session['csrf'] = secrets.token_hex(24)
        flash('Verification code sent to the entered email address. Check your inbox or Spam folder; it expires in five minutes.')
        return redirect(url_for('reset_password'))
    return render_template('forgot_password.html')


def reset_password():
    csrf_token()
    email = session.get('recovery_email')
    if not email:
        return redirect(url_for('forgot_password'))
    if request.method == 'POST':
        if not hmac.compare_digest(str(request.form.get('csrf', '')), csrf_token()):
            return ('Invalid form token', 400)
        code = request.form.get('otp', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if not password_valid(password):
            flash(PASSWORD_MESSAGE, 'error')
            return (render_template('reset_password.html'), 400)
        if password != confirm:
            flash('Passwords do not match.', 'error')
            return (render_template('reset_password.html'), 400)
        now = int(time.time())
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            entry = c.execute('SELECT * FROM password_resets WHERE id=? AND email=? AND consumed=0', (session.get('recovery_challenge'), email)).fetchone()
            if not entry or entry['expires_at'] <= now or entry['attempts'] >= OTP_MAX_ATTEMPTS:
                flash('There is no valid reset code. Please request another code and check your email.')
                return (render_template('reset_password.html'), 400)
            c.execute('UPDATE password_resets SET attempts=attempts+1 WHERE id=?', (entry['id'],))
            if not (len(code) == 6 and code.isdigit() and hmac.compare_digest(entry['code_hash'], otp_digest('reset:' + email, code))):
                flash('Incorrect verification code. Please check the latest email; five incorrect attempts invalidate a code.', 'error')
                return (render_template('reset_password.html'), 400)
            user = c.execute("SELECT id,password,role FROM users WHERE lower(email)=? AND status='active'", (email,)).fetchone()
            if not user:
                flash('Email verification succeeded, but no eligible KANTYA account can be reset for this address.', 'error')
                return render_template('reset_password.html'), 400
            if check_password_hash(user['password'], password):
                flash('Choose a password different from your current password.')
                return (render_template('reset_password.html'), 400)
            c.execute('UPDATE users SET password=?,must_change_password=0,session_version=session_version+1 WHERE id=?', (generate_password_hash(password), user['id']))
            c.execute('DELETE FROM web_sessions WHERE user_id=?', (user['id'],))
            c.execute('UPDATE password_resets SET consumed=1 WHERE email=?', (email,))
            c.execute('UPDATE otp_challenges SET consumed=1 WHERE user_id=?', (user['id'],))
            c.execute('DELETE FROM login_attempts WHERE username=?', (email,))
        session.clear()
        session['login_identity'] = {'email': email, 'role': user['role']}
        flash('Password reset successfully. Log in with your new password.')
        return redirect(url_for('login', role=user['role']))
    return render_template('reset_password.html')


def change_password():
    if request.method == 'POST':
        old = request.form.get('current_password', '')
        new = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        with db() as c:
            user = c.execute('SELECT password FROM users WHERE id=?', (session['uid'],)).fetchone()
            if not user or not check_password_hash(user['password'], old):
                flash('Incorrect current password.', 'error')
            elif new != confirm:
                flash('New passwords do not match.')
            elif not password_valid(new):
                flash(PASSWORD_MESSAGE, 'error')
            elif check_password_hash(user['password'], new):
                flash('Choose a different password.')
            else:
                c.execute('UPDATE users SET password=?,must_change_password=0,session_version=session_version+1 WHERE id=?', (generate_password_hash(new), session['uid']))
                session.clear()
                flash('Password updated. Please sign in again.')
                return redirect(url_for('choose_role'))
    return render_template('change_password.html')


def enforce_password_change():
    uid = session.get('uid')
    if not uid or request.endpoint == 'static':
        return
    now = int(time.time())
    with db() as c:
        row = c.execute('SELECT must_change_password,status,session_version,role FROM users WHERE id=?', (uid,)).fetchone()
        if (not row or row['status'] != 'active' or row['role'] != session.get('role')
                or row['session_version'] != session.get('session_version')):
            session.clear()
            return redirect(url_for('choose_role'))
        renewed = c.execute('UPDATE web_sessions SET last_seen=? WHERE token=? AND user_id=? AND last_seen>?', (now, session.get('login_token'), uid, now - SESSION_IDLE))
        if renewed.rowcount != 1:
            session.clear()
            return redirect(url_for('choose_role'))
    session.modified = True
    if row['must_change_password'] and request.endpoint not in ('change_password', 'logout'):
        return redirect(url_for('change_password'))


def logout():
    with db() as c:
        c.execute('DELETE FROM web_sessions WHERE token=?', (session.get('login_token'),))
    session.clear()
    return redirect('/choose-role')
