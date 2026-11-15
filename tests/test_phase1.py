"""KANTYA Phase 1 integration checks. Run: python -m unittest discover -s tests -p 'test_phase1.py' -v"""
import os, sys, tempfile, unittest, importlib, sqlite3
from pathlib import Path
from unittest.mock import patch
from werkzeug.security import check_password_hash, generate_password_hash
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from database import db as connect_db

class AuthenticationIntegrationTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {
            'KANTYA_DB': str(Path(cls.tmp.name) / 'auth_test.db'),
            'KANTYA_SECRET': 'test-secret-only-not-for-deployment-12345',
            'KANTYA_ENV': 'production',
        }):
            cls.module = importlib.import_module('app')
        cls.module.app.config.update(TESTING=True)
        cls.app = cls.module.app

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        import auth
        self.auth = auth
        self.client = self.app.test_client()
        self.mails = []
        self.mailpatch = patch.object(self.auth, 'mail_otp', side_effect=self.fake_email)
        self.mailpatch.start()
        with self.app.app_context(), connect_db() as c:
            c.execute('DELETE FROM password_resets')
            c.execute('DELETE FROM otp_challenges')
            c.execute('DELETE FROM registrations')
            c.execute('DELETE FROM login_attempts')
            c.execute('DELETE FROM otp_requests')
            c.execute('DELETE FROM web_sessions')
            c.execute('DELETE FROM users WHERE email IS NOT NULL')

    def tearDown(self):
        self.mailpatch.stop()

    def fake_email(self, to, code, purpose='login'):
        self.mails.append((to, code, purpose))

    def token(self):
        with self.client.session_transaction() as sess:
            return sess['csrf']

    def create_user(self, email, role='Staff', status='active', pw='Staff1!'):
        with self.app.app_context(), connect_db() as c:
            c.execute('INSERT INTO users(username,password,role,email,status) VALUES(?,?,?,?,?)', (email, generate_password_hash(pw), role, email, status))

    def test_password_policy_bounds(self):
        fn = self.auth.password_valid
        self.assertFalse(fn('Ab1!xy'))
        self.assertFalse(fn('password1234'))
        self.assertTrue(fn('quiet garden path'))
        self.assertTrue(fn('Ab1!12345678'))
        self.assertFalse(fn('Ab1!x'))
        self.assertTrue(fn('Ab1!123456789'))
        self.assertTrue(fn('Ab1!' + 'x' * 250))

    def test_signup_rejects_invalid_fields_without_creating_account(self):
        endpoint = '/signup?role=Staff'
        self.client.get(endpoint)
        valid = dict(csrf=self.token(), email='valid@example.com',
                     password='quiet garden path', confirm_password='quiet garden path')
        cases = (
            ({'email': 'missing-domain'}, b'Enter a valid email address.'),
            ({'password': 'short'}, b'Use at least 12 characters'),
            ({'confirm_password': 'different phrase'}, b'Passwords do not match.'),
        )
        for fields, error in cases:
            with self.subTest(fields=fields):
                self.assertIn(error, self.client.post(endpoint, data={**valid, **fields}).data)
        with self.app.app_context(), connect_db() as c:
            self.assertIsNone(c.execute("SELECT 1 FROM users WHERE email=?", (valid['email'],)).fetchone())

    def test_local_breached_password_list(self):
        password = 'a listed test passphrase'
        with tempfile.TemporaryDirectory() as temp:
            denylist = Path(temp) / 'password-hashes.txt'
            digest = self.auth.hashlib.sha1(password.encode()).hexdigest().upper()
            denylist.write_text(f'{digest}:42\n', encoding='ascii')
            with patch.dict(os.environ, {'KANTYA_PASSWORD_DENYLIST': str(denylist)}):
                self.assertFalse(self.auth.password_valid(password))
                self.assertTrue(self.auth.password_valid('a different test passphrase'))

    def signup_then_request_login_code(self, client, role, email):
        password = 'quiet garden path'
        client.get(f'/signup?role={role}')
        with client.session_transaction() as state:
            csrf = state['csrf']
        sent_before = len(self.mails)
        response = client.post(f'/signup?role={role}', data={
            'csrf': csrf, 'email': email, 'password': password, 'confirm_password': password,
        })
        self.assertEqual(response.location, f'/login?role={role}')
        self.assertEqual(len(self.mails), sent_before)
        client.get(response.location)
        with client.session_transaction() as state:
            csrf = state['csrf']
        response = client.post(f'/login?role={role}', data={
            'csrf': csrf, 'email': email, 'password': password,
        })
        self.assertEqual(response.location, '/verify-otp')
        self.assertEqual(len(self.mails), sent_before + 1)
        self.assertEqual((self.mails[-1][0], self.mails[-1][2]), (email, 'login'))
        self.assertEqual(client.get('/dashboard').location, '/choose-role')
        return self.mails[-1][1]

    def test_new_signup_clears_unknown_email_lock_and_prefills_login(self):
        email = 'newly-registered@example.com'
        login = '/login?role=Staff'
        self.client.get(login)
        for _ in range(5):
            self.client.post(login, data={'csrf': self.token(), 'email': email, 'password': 'wrong'})
        self.assertEqual(self.client.post(login, data={
            'csrf': self.token(), 'email': email, 'password': 'wrong',
        }).status_code, 429)
        password = 'A freshly registered passphrase!'
        response = self.client.post('/signup?role=Staff', data={
            'csrf': self.token(), 'email': email.upper(), 'password': password,
            'confirm_password': password,
        }, follow_redirects=True)
        self.assertIn(f'value="{email}"'.encode(), response.data)
        self.assertNotIn(password.encode(), response.data)
        response = self.client.post(login, data={
            'csrf': self.token(), 'email': email, 'password': password,
        })
        self.assertEqual(response.location, '/verify-otp')
        self.assertEqual(self.mails[-1][0], email)

    def test_staff_signup_then_login_otp(self):
        otp = self.signup_then_request_login_code(self.client, 'Staff', 'new@example.com')
        response = self.client.post('/verify-otp', data={'csrf': self.token(), 'otp': otp})
        self.assertEqual(response.location, '/dashboard')
        with self.client.session_transaction() as state:
            self.assertEqual(state['role'], 'Staff')
        with self.app.app_context(), connect_db() as c:
            self.assertEqual(c.execute("SELECT status FROM users WHERE email='new@example.com'").fetchone()[0], 'active')

    def test_first_manager_login_otp_and_later_approval(self):
        manager = 'first@example.com'
        self.create_user('staff@example.com')
        first_code = self.signup_then_request_login_code(self.client, 'Manager', manager)
        other = self.app.test_client()
        other_code = self.signup_then_request_login_code(other, 'Manager', 'other@example.com')

        response = self.client.post('/verify-otp', data={'csrf': self.token(), 'otp': first_code})
        self.assertEqual(response.location, '/dashboard')
        with other.session_transaction() as state:
            csrf = state['csrf']
        response = other.post('/verify-otp', data={'csrf': csrf, 'otp': other_code})
        self.assertEqual(response.location, '/login?role=Manager')
        self.assertEqual(other.get('/dashboard').location, '/choose-role')

        with self.app.app_context(), connect_db() as c:
            rows = c.execute("SELECT email,status FROM users WHERE role='Manager'").fetchall()
            self.assertEqual(dict(map(tuple, rows)), {manager: 'active', 'other@example.com': 'pending'})
            pending_id = c.execute("SELECT id FROM users WHERE email='other@example.com'").fetchone()[0]
        response = self.client.post('/approvals', data={
            'csrf': self.token(), 'user_id': str(pending_id), 'decision': 'active',
        })
        self.assertEqual(response.status_code, 302)
        with self.app.app_context(), connect_db() as c:
            self.assertEqual(c.execute('SELECT status FROM users WHERE id=?', (pending_id,)).fetchone()[0], 'active')

    def test_pending_first_manager_activates_and_signs_in_with_one_otp(self):
        manager = 'first@example.com'
        self.create_user(manager, 'Manager', 'pending', 'Valid1!')
        with self.app.app_context(), connect_db() as c:
            uid = c.execute('SELECT id FROM users WHERE email=?', (manager,)).fetchone()[0]
        self.client.get('/login?role=Manager')
        response = self.client.post('/login?role=Manager', data={
            'csrf': self.token(), 'email': manager, 'password': 'Valid1!',
        })
        self.assertEqual(response.location, '/verify-otp')
        self.assertEqual(len(self.mails), 1)
        self.assertEqual(self.mails[-1][2], 'login')
        otp = self.mails[-1][1]
        wrong = f'{(int(otp) + 1) % 1000000:06d}'
        response = self.client.post('/verify-otp', data={'csrf': self.token(), 'otp': wrong})
        self.assertEqual(response.status_code, 400)
        with self.app.app_context(), connect_db() as c:
            self.assertEqual(c.execute('SELECT status FROM users WHERE id=?', (uid,)).fetchone()[0], 'pending')
        response = self.client.post('/verify-otp', data={'csrf': self.token(), 'otp': otp})
        self.assertEqual(response.location, '/dashboard')
        with self.client.session_transaction() as state:
            self.assertEqual(state['uid'], uid)
        with self.app.app_context(), connect_db() as c:
            rows = c.execute('SELECT id,status FROM users WHERE email=?', (manager,)).fetchall()
            self.assertEqual(list(map(tuple, rows)), [(uid, 'active')])

    def test_manager_cannot_self_approve(self):
        self.create_user('approver@example.com', 'Manager')
        self.create_user('manager@example.com', 'Manager', 'pending')
        self.client.get('/login?role=Manager')
        resp = self.client.post('/login?role=Manager', data={'csrf': self.token(), 'email': 'manager@example.com', 'password': 'Staff1!', 'role': 'Manager'})
        self.assertEqual(resp.status_code, 403)
        self.assertIn(b'awaiting authorization', resp.data)
        self.assertEqual(len(self.mails), 0)

    def test_password_reset_invalidate_previous_session(self):
        self.create_user('reset@example.com')
        self.client.get('/forgot-password')
        resp = self.client.post('/forgot-password', data={'csrf': self.token(), 'email': 'reset@example.com'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.mails[-1][2], 'reset')
        resp = self.client.post('/reset-password', data={'csrf': self.token(), 'otp': self.mails[-1][1], 'password': 'another garden path', 'confirm_password': 'another garden path'})
        self.assertEqual(resp.location, '/login?role=Staff')
        self.client.get(resp.location)
        login = self.client.post(resp.location, data={
            'csrf': self.token(), 'email': 'reset@example.com', 'password': 'another garden path',
        })
        self.assertEqual(login.location, '/verify-otp')
        verified = self.client.post('/verify-otp', data={'csrf': self.token(), 'otp': self.mails[-1][1]})
        self.assertEqual(verified.location, '/dashboard')
        with self.app.app_context(), connect_db() as c:
            self.assertTrue(check_password_hash(c.execute("SELECT password FROM users WHERE email='reset@example.com'").fetchone()[0], 'another garden path'))
            self.assertEqual(c.execute("SELECT session_version FROM users WHERE email='reset@example.com'").fetchone()[0], 1)

    def test_recovery_email_only_and_codes_are_account_bound(self):
        self.create_user('inactive@example.com', status='unverified')
        self.create_user('first@example.com')
        self.create_user('second@example.com')
        page = self.client.get('/forgot-password')
        self.assertNotIn(b'name="method"', page.data)
        self.assertNotIn(b'SMS', page.data)

        def request_code(email):
            return self.client.post('/forgot-password', data={
                'csrf': self.token(), 'email': email,
            }, follow_redirects=True)

        for email in ('inactive@example.com', 'absent@example.com'):
            response = request_code(email)
            self.assertIn(b'If this email belongs to an active account', response.data)
            self.assertFalse(self.mails)
        with patch.object(self.auth.secrets, 'randbelow', side_effect=[123456, 654321]):
            request_code(' FIRST@example.com ')
            request_code('second@example.com')
        self.assertEqual(self.mails, [
            ('first@example.com', '123456', 'reset'), ('second@example.com', '654321', 'reset'),
        ])
        payload = {'csrf': self.token(), 'password': 'another garden path',
                   'confirm_password': 'another garden path'}
        self.assertEqual(self.client.post('/reset-password', data={**payload, 'otp': '123456'}).status_code, 400)
        with self.app.app_context(), connect_db() as c:
            c.execute("UPDATE password_resets SET expires_at=0 WHERE email='second@example.com'")
        self.assertEqual(self.client.post('/reset-password', data={**payload, 'otp': '654321'}).status_code, 400)
        with self.app.app_context(), connect_db() as c:
            c.execute("UPDATE password_resets SET expires_at=?, consumed=1 WHERE email='second@example.com'",
                      (int(self.auth.time.time()) + 60,))
        self.assertEqual(self.client.post('/reset-password', data={**payload, 'otp': '654321'}).status_code, 400)
        with self.app.app_context(), connect_db() as c:
            hashes = c.execute("SELECT password FROM users WHERE email IN ('first@example.com','second@example.com')").fetchall()
            self.assertTrue(all(check_password_hash(row['password'], 'Staff1!') for row in hashes))

    def test_bad_reset_code_is_rejected(self):
        self.create_user('reset@example.com')
        self.client.get('/forgot-password')
        self.client.post('/forgot-password', data={'csrf': self.token(), 'email': 'reset@example.com'})
        resp = self.client.post('/reset-password', data={'csrf': self.token(), 'otp': '000000' if self.mails[-1][1] != '000000' else '999999', 'password': 'another garden path', 'confirm_password': 'another garden path'})
        self.assertEqual(resp.status_code, 400)

    def test_role_mismatch_rejected(self):
        self.create_user('user@example.com', 'Staff')
        self.client.get('/login?role=Manager')
        resp = self.client.post('/login?role=Manager', data={'csrf': self.token(), 'email': 'user@example.com', 'password': 'Staff1!', 'role': 'Manager'})
        self.assertEqual(resp.status_code, 403)
        self.assertIn(b'password is correct', resp.data)

    def test_wrong_password_is_separate_from_pending_account(self):
        self.create_user('pending@example.com', 'Manager', 'pending', 'Valid1!')
        self.client.get('/login?role=Manager')
        response = self.client.post('/login?role=Manager', data={
            'csrf': self.token(), 'email': 'pending@example.com',
            'password': 'Wrong1!', 'role': 'Manager'
        })
        self.assertEqual(response.status_code, 401)
        self.assertIn(b'Incorrect email or password', response.data)
        self.assertNotIn(b'awaiting authorization', response.data)

    def test_email_delivery_failure_is_not_reported_as_wrong_password(self):
        self.create_user('working@example.com', 'Staff', 'active', 'Valid1!')
        self.client.get('/login?role=Staff')
        with patch.object(self.auth, 'mail_otp', side_effect=RuntimeError('test smtp')):
            response = self.client.post('/login?role=Staff', data={
                'csrf': self.token(), 'email': 'working@example.com',
                'password': 'Valid1!', 'role': 'Staff'
            })
        self.assertEqual(response.status_code, 503)
        self.assertIn(b'email and password were accepted', response.data)
        with self.client.session_transaction() as sess:
            self.assertNotIn('uid', sess)

    def begin_login(self):
        self.create_user('walkthrough@example.com')
        self.assertEqual(self.client.get('/choose-role').status_code, 200)
        response = self.client.post('/login?role=Staff', data={
            'csrf': self.token(), 'email': 'walkthrough@example.com', 'password': 'Staff1!'
        })
        self.assertTrue(response.location.endswith('/verify-otp'))
        self.assertEqual(self.client.get('/dashboard').status_code, 302)

    def test_otp_exact_expiry_and_attempt_limit(self):
        self.begin_login()
        code = self.mails[-1][1]
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(5):
            self.assertEqual(self.client.post('/verify-otp', data={'csrf': self.token(), 'otp': wrong}).status_code, 400)
        response = self.client.post('/verify-otp', data={'csrf': self.token(), 'otp': code})
        self.assertTrue(response.location.endswith('/choose-role'))
        self.assertEqual(self.client.get('/dashboard').status_code, 302)
        with self.app.app_context(), connect_db() as db:
            row = db.execute('SELECT expires_at,created_at,attempts FROM otp_challenges').fetchone()
            self.assertEqual(row['expires_at'] - row['created_at'], 300)
            self.assertEqual(row['attempts'], 5)

    def test_resend_policy_and_old_code(self):
        self.begin_login()
        with patch.dict(os.environ, {'KANTYA_OTP_RESEND_CAP': '0'}):
            self.assertEqual(self.client.post('/resend-otp', data={'csrf': self.token()}).status_code, 503)
        old = self.mails[-1][1]
        wrong = '000000' if old != '000000' else '111111'
        self.assertEqual(self.client.post('/verify-otp', data={'csrf': self.token(), 'otp': wrong}).status_code, 400)
        clock = int(self.auth.time.time())

        with patch.dict(os.environ, {'KANTYA_OTP_RESEND_COOLDOWN': '2', 'KANTYA_OTP_RESEND_CAP': '1', 'KANTYA_OTP_RESEND_WINDOW': '30'}):
            with patch.object(self.auth.time, 'time', return_value=clock):
                with self.app.app_context(), connect_db() as db:
                    db.execute('UPDATE otp_challenges SET expires_at=?', (clock + 300,))
                self.assertEqual(self.client.post('/resend-otp', data={'csrf': self.token()}).status_code, 302)
                self.assertEqual(self.client.post('/resend-otp', data={'csrf': self.token()}).status_code, 429)
            with patch.object(self.auth.time, 'time', return_value=clock + 3):
                self.assertEqual(self.client.post('/resend-otp', data={'csrf': self.token()}).status_code, 429)
                self.assertEqual(self.client.post('/verify-otp', data={'csrf': self.token(), 'otp': old}).status_code, 400)
            with self.app.app_context(), connect_db() as db:
                self.assertEqual(db.execute('SELECT attempts FROM otp_challenges').fetchone()[0], 2)

    def test_logout_replay_and_server_idle_timeout(self):
        self.begin_login()
        response = self.client.post('/verify-otp', data={'csrf': self.token(), 'otp': self.mails[-1][1]})
        self.assertTrue(response.location.endswith('/dashboard'))
        original_cookie = self.client.get_cookie('session').value
        self.assertEqual(self.client.get('/dashboard').status_code, 200)
        self.assertEqual(self.client.post('/logout', data={'csrf': self.token()}).status_code, 302)
        self.client.set_cookie('session', original_cookie)
        self.assertEqual(self.client.get('/dashboard').status_code, 302)
        with self.app.app_context(), connect_db() as db:
            uid = db.execute("SELECT id FROM users WHERE email='walkthrough@example.com'").fetchone()[0]
            db.execute("INSERT INTO web_sessions VALUES('expired-fixture',?,?)", (uid, int(self.auth.time.time()) - 900))
        with self.client.session_transaction() as session:
            session.update(uid=uid, role='Staff', login_token='expired-fixture', session_version=0)
        self.assertEqual(self.client.get('/change-password').status_code, 302)

    def test_manager_login_lock_then_otp_at_boundary(self):
        self.create_user('manager@example.com', 'Manager')
        self.client.get('/choose-role')
        endpoint = '/login?role=Manager'
        form = {'csrf': self.token(), 'email': 'manager@example.com', 'password': 'wrong'}
        clock = int(self.auth.time.time())
        with patch.object(self.auth.time, 'time', return_value=clock):
            for _ in range(5):
                self.assertEqual(self.client.post(endpoint, data=form).status_code, 401)
            form['password'] = 'Staff1!'
            self.assertEqual(self.client.post(endpoint, data=form).status_code, 429)
            self.assertFalse(self.mails)
        with patch.object(self.auth.time, 'time', return_value=clock + 900):
            response = self.client.post(endpoint, data=form)
            self.assertTrue(response.location.endswith('/verify-otp'))
            response = self.client.post('/verify-otp', data={'csrf': self.token(), 'otp': self.mails[-1][1]})
            self.assertTrue(response.location.endswith('/dashboard'))
            for path in ('/dashboard', '/forecast', '/dashboard?view=waste', '/reports'):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_invalid_csrf_rejected(self):
        self.client.get('/signup?role=Staff')
        resp = self.client.post('/signup?role=Staff', data={'csrf': 'bad', 'email': 'x@example.com', 'password': 'Ab1!xy', 'confirm_password': 'Ab1!xy'})
        self.assertEqual(resp.status_code, 400)
if __name__ == '__main__':
    unittest.main()

def _test_recovery_unregistered_address_does_not_claim_sent(self):
    self.client.get('/forgot-password')
    resp = self.client.post('/forgot-password', data={'csrf': self.token(), 'email': 'nobody@example.com', 'method': 'email'}, follow_redirects=True)
    self.assertEqual(resp.status_code, 200)
    self.assertIn(b'If this email belongs to an active account', resp.data)
    self.assertNotIn(b'a reset code has been sent', resp.data)
    self.assertEqual(len(self.mails), 0)

def _test_recovery_smtp_failure_does_not_create_code(self):
    self.create_user('resetfail@example.com')
    self.client.get('/forgot-password')
    with patch.object(self.auth, 'mail_otp', side_effect=RuntimeError('smtp test unavailable')):
        resp = self.client.post('/forgot-password', data={'csrf': self.token(), 'email': 'resetfail@example.com', 'method': 'email'}, follow_redirects=True)
    self.assertEqual(resp.status_code, 200)
    with self.app.app_context(), connect_db() as c:
        self.assertIsNone(c.execute("SELECT id FROM password_resets WHERE email='resetfail@example.com'").fetchone())
    self.assertIn(b'If this email belongs to an active account', resp.data)

def _test_sms_not_falsely_enabled(self):
    self.client.get('/forgot-password')
    resp = self.client.post('/forgot-password', data={'csrf': self.token(), 'email': 'a@example.com', 'method': 'sms'})
    self.assertEqual(resp.status_code, 400)
    self.assertIn(b'Use your registered email', resp.data)
AuthenticationIntegrationTests.test_recovery_unregistered_address_does_not_claim_sent = _test_recovery_unregistered_address_does_not_claim_sent
AuthenticationIntegrationTests.test_recovery_smtp_failure_does_not_create_code = _test_recovery_smtp_failure_does_not_create_code
AuthenticationIntegrationTests.test_sms_not_falsely_enabled = _test_sms_not_falsely_enabled
