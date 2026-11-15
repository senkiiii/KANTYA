"""Reconciliation and permission checks using a disposable database copy."""
import csv
from datetime import date
import importlib.util
import json
import math
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
import unittest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMP = tempfile.TemporaryDirectory()
DB = Path(TEMP.name) / 'test.db'
from unittest.mock import patch
spec = importlib.util.spec_from_file_location('kantya_system_fixture', ROOT / 'app.py')
module = importlib.util.module_from_spec(spec)
with patch.dict(os.environ, {'KANTYA_DB': str(DB), 'KANTYA_ENV': 'test', 'KANTYA_SEED_DEMO': '0'}):
    spec.loader.exec_module(module)
from database import db as connect_db
from operating_calendar import ITEMS, is_operating_day
from records import scoped, valid_day
from reporting import signature

with module.app.app_context(), connect_db() as db:
    db.executemany('INSERT INTO users(id,username,password,role) VALUES(?,?,?,?)',
                   [(1, 'manager', 'unused', 'Manager'), (2, 'staff', 'unused', 'Staff')])
from analysis import summary, workspace

class SystemTests(unittest.TestCase):

    def setUp(self):
        context = module.app.app_context()
        context.push()
        self.addCleanup(context.pop)
        self.client = module.app.test_client()
        self.login_fixture(1, 'Manager')

    def login_fixture(self, uid, role):
        token = secrets.token_hex(32)
        with connect_db() as db:
            db.execute('INSERT INTO web_sessions VALUES(?,?,?)', (token, uid, int(time.time())))
        with self.client.session_transaction() as session:
            session.update(uid=uid, role=role, username=role.lower(), csrf='test-token',
                           session_version=0, login_token=token)

    def test_backup_recovery_screen_permissions_and_restore(self):
        from contextlib import closing
        import sqlite3

        from cryptography.fernet import Fernet
        from database import record_backup_owner

        endpoint = '/backup-recovery'
        email = 'backup-operator@example.com'
        self.assertEqual(self.client.get(endpoint).status_code, 403)
        with connect_db() as connection:
            connection.execute("UPDATE users SET email=? WHERE id=1", (email,))
            record_backup_owner(connection)
            connection.execute("INSERT INTO users(id,username,password,role,email,status) VALUES(99,'later-manager','test','Manager','later@example.com','active')")
            record_backup_owner(connection)
            account_count = connection.execute('SELECT COUNT(*) FROM users').fetchone()[0]
            record_count = connection.execute('SELECT COUNT(*) FROM records').fetchone()[0]

        def reset_operator_email():
            with module.app.app_context(), connect_db() as connection:
                connection.execute("DELETE FROM settings WHERE key='backup_owner'")
                connection.execute("DELETE FROM web_sessions WHERE user_id=99")
                connection.execute("DELETE FROM users WHERE id=99")
                connection.execute('UPDATE users SET email=NULL WHERE id=1')

        self.addCleanup(reset_operator_email)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            key = root / 'key'
            key.write_bytes(Fernet.generate_key())
            vault = root / 'vault'
            vault.mkdir()
            configuration = root / '.env'
            configuration.write_text('KANTYA_ENV=test\n')
            settings = {'KANTYA_BACKUP_OPERATORS': '', 'KANTYA_BACKUP_VAULT': str(vault),
                        'KANTYA_BACKUP_KEY_FILE': str(key)}
            with patch.dict(os.environ, settings), patch.dict(
                module.app.config, {'CONFIGURATION_FILE': str(configuration)}
            ):
                form = {'csrf': 'test-token', 'action': 'create'}
                self.login_fixture(2, 'Staff')
                self.assertEqual(self.client.post(endpoint, data=form).status_code, 403)
                self.assertNotIn(b'/backup-recovery', self.client.get('/dashboard').data)
                self.login_fixture(99, 'Manager')
                self.assertNotIn(b'id="reports-backup"', self.client.get('/reports?backup=open').data)
                self.assertEqual(self.client.post(endpoint, data=form).status_code, 403)
                self.login_fixture(1, 'Manager')
                self.assertNotIn(b'/backup-recovery', self.client.get('/dashboard').data)
                self.assertIn(b'Backup &amp; Recovery', self.client.get('/reports').data)
                panel = self.client.get(endpoint, follow_redirects=True)
                self.assertIn(b'id="reports-backup"', panel.data)
                self.assertIn(b'Create Backup', panel.data)
                self.assertEqual(self.client.post(endpoint, data={**form, 'csrf': 'bad'}).status_code, 400)
                self.assertEqual(list(vault.iterdir()), [])
                created = self.client.post(endpoint, data=form, follow_redirects=True)
                self.assertIn(b'Encrypted backup created and verified', created.data)
                archive, = vault.glob('*.backup')
                self.assertIn(archive.name.encode(), created.data)
                restore = {**form, 'action': 'restore', 'backup': archive.name}
                self.assertEqual(self.client.post(endpoint, data=restore).status_code, 400)
                self.assertEqual(self.client.post(
                    endpoint, data={**restore, 'backup': '../' + archive.name, 'confirm': 'yes'}
                ).status_code, 400)
                restored = self.client.post(endpoint, data={**restore, 'confirm': 'yes'}, follow_redirects=True)
                self.assertIn(b'Staged restore verified', restored.data)
                stage, = vault.glob('restore-*')
                with closing(sqlite3.connect(stage / 'kantya.db')) as connection:
                    self.assertEqual(connection.execute('SELECT COUNT(*) FROM users').fetchone()[0], account_count)
                    self.assertEqual(connection.execute('SELECT COUNT(*) FROM records').fetchone()[0], record_count)
                    self.assertEqual(connection.execute('SELECT COUNT(*) FROM web_sessions').fetchone()[0], 0)
                self.assertEqual(self.client.get('/dashboard').status_code, 200)
                self.assertEqual((stage / 'configuration.env').read_bytes(), configuration.read_bytes())

    def test_data_calendar_reconciliation(self):
        with open(ROOT / 'data/KANTYA_Main_Dataset_v1.csv') as f:
            raw = list(csv.DictReader(f))
        included = [r for r in raw if is_operating_day(r['record_date'])]
        self.assertEqual(len(raw), 3393)
        self.assertEqual(len(included), 2431)
        self.assertEqual(len({r['record_date'] for r in included}), 187)
        with module.app.test_request_context('/dashboard'):
            rows, filters, coverage = scoped()
        self.assertEqual(len(rows), len(included))
        reference = {(r['food_item_name'], r['record_date'], r['selling_period']): r for r in included}
        for r in rows:
            source = reference[r['item'], r['day'], r['period']]
            self.assertEqual(r['prepared'], int(source['quantity_prepared']))
            self.assertEqual(r['sold'], int(source['quantity_sold']))
            self.assertEqual(r['waste'], int(source['actual_waste']))
        self.assertEqual(coverage['missing'], 0)

    def test_ratio_of_sums_and_daily_grain(self):
        rows = [dict(item='A', period='Lunch', day='2026-09-01', prepared=10, sold=10, waste=0), dict(item='A', period='Snack', day='2026-09-01', prepared=90, sold=0, waste=10), dict(item='A', period='Lunch', day='2026-09-02', prepared=0, sold=0, waste=0)]
        s = summary(rows)
        self.assertEqual(s['sell_rate'], 10)
        self.assertEqual(s['mean_daily'], 5)
        self.assertEqual(s['eligible'], 2)
        self.assertEqual(s['full_sale'], 1)
        self.assertIsNone(summary(rows[-1:])['sell_rate'])
        self.assertIsNone(summary([])['mean_daily'])

    def test_views_filters_and_empty_states(self):
        for path in ['/', '/dashboard', '/dashboard?view=patterns', '/dashboard?view=waste', '/reports', '/forecast', '/dashboard?item=Tuna+Sandwich&view=overview', '/dashboard?item=Tuna+Sandwich&view=patterns', '/dashboard?item=Tuna+Sandwich&view=waste', '/dashboard?item=Chicken+Adobo&period=Breakfast', '/dashboard?start=2026-06-01&end=2026-06-30', '/dashboard?start=2026-12-01&end=2026-12-10', '/?item=Tuna+Sandwich&page=2', '/forecast?item=&period=', '/forecast?item=Tuna+Sandwich&period=Breakfast']:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            self.assertNotIn(b'Traceback', r.data)
            self.assertNotIn(b'built-in method', r.data)
        r = self.client.get('/dashboard?item=Chicken+Adobo&period=Breakfast')
        self.assertIn(b'No records in this scope', r.data)
        self.assertNotIn(b'>nan<', r.data.lower())

    def test_invalid_scope_rejected(self):
        for path in ['/dashboard?item=Unknown', '/dashboard?view=Unknown', '/dashboard?grain=Unknown', '/dashboard?period=Dinner', '/dashboard?start=oops', '/dashboard?start=2026-09-30&end=2026-01-01', '/?page=oops', '/?page=0', '/?sort=bogus', '/?q=' + 'x'*101]:
            self.assertEqual(self.client.get(path).status_code, 400, path)

    def test_page_summaries_reconcile(self):
        with module.app.test_request_context('/dashboard?item=Tuna+Sandwich&start=2026-08-01&end=2026-09-30'):
            rows, filters, _ = scoped()
            data = workspace(rows, filters, ITEMS, is_operating_day)
        self.assertEqual(data['focus']['sold'], sum((r['sold'] for r in rows)))
        self.assertEqual(sum((s['sold'] for s in data['series'])), data['focus']['sold'])
        self.assertEqual(sum((s['waste'] for s in data['series'])), data['focus']['waste'])
        self.assertGreater(len(rows), 50)
        for endpoint in ['/dashboard', '/reports', '/']:
            html = self.client.get(endpoint + '?item=Tuna+Sandwich&start=2026-08-01&end=2026-09-30').data.decode()
            self.assertIn(str(data['focus']['sold']), html.replace(',', ''))

    def test_staff_permissions_and_csrf(self):
        self.login_fixture(2, 'Staff')
        for path in ['/reports', '/import', '/correct/1', '/waste', '/dashboard?view=waste', '/forecast']:
            self.assertEqual(self.client.get(path).status_code, 403)
        self.assertEqual(self.client.post('/forecast', data={'csrf': 'test-token'}).status_code, 403)
        html = self.client.get('/').data
        self.assertNotIn(b'>Correct</a>', html)
        self.assertNotIn(b'Import CSV', html)
        self.assertEqual(self.client.post('/add', data={}).status_code, 400)

    def test_blocked_forecast_does_not_write(self):
        with connect_db() as c:
            before = c.execute('SELECT count(*) FROM forecast_runs').fetchone()[0]
        r = self.client.post('/forecast', data={'csrf': 'test-token'})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'calendar ends', r.data)
        self.assertIn(b'Confirm the missing operating dates', r.data)
        self.assertIn(b'class="chart-date-picker"', r.data)
        self.assertIn(b'class="x-tick"', r.data)
        self.assertNotIn(b'<title>Recorded sales', r.data)
        with connect_db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM forecast_runs').fetchone()[0], before)

    def test_manager_can_delete_and_archive_record(self):
        with connect_db() as c:
            sample = c.execute("SELECT day FROM records WHERE item='Chicken Adobo' LIMIT 1").fetchone()
            record = c.execute(
                "INSERT INTO records(item,day,period,prepared,sold,waste,creator,source) "
                "VALUES('Chicken Adobo',?,'Breakfast',20,10,1,2,'test')", (sample['day'],)
            ).lastrowid
            c.execute(
                "INSERT INTO corrections(record_id,manager_id,old_values,new_values,reason) "
                "VALUES(?,1,'[19,10,1]','[20,10,1]','Count corrected')", (record,)
            )
        def cleanup():
            with connect_db() as c:
                c.execute('DELETE FROM deleted_records WHERE record_id=?', (record,))
                c.execute('DELETE FROM corrections WHERE record_id=?', (record,))
                c.execute('DELETE FROM records WHERE id=?', (record,))
        self.addCleanup(cleanup)
        path = f'/records/{record}/delete'
        self.login_fixture(2, 'Staff')
        self.assertEqual(self.client.post(path, data={'csrf': 'test-token', 'reason': 'Duplicate'}).status_code, 403)
        self.login_fixture(1, 'Manager')
        self.assertIn(b'Delete record', self.client.get(f'/records/{record}').data)
        self.assertIn(b'Enter a deletion reason', self.client.post(path, data={'csrf': 'test-token'}).data)
        self.assertEqual(self.client.post(path, data={'csrf': 'wrong', 'reason': 'Duplicate'}).status_code, 400)
        result = self.client.post(path, data={'csrf': 'test-token', 'reason': 'Duplicate'})
        self.assertEqual(result.status_code, 303)
        self.assertEqual(self.client.get(f'/records/{record}').status_code, 404)
        with connect_db() as c:
            archive = c.execute('SELECT * FROM deleted_records WHERE record_id=?', (record,)).fetchone()
            self.assertEqual(archive['deleted_by'], 1)
            self.assertEqual(archive['reason'], 'Duplicate')
            self.assertEqual(json.loads(archive['snapshot'])['sold'], 10)
            self.assertEqual(json.loads(archive['corrections'])[0]['reason'], 'Count corrected')
            self.assertIsNone(c.execute('SELECT id FROM corrections WHERE record_id=?', (record,)).fetchone())

    def test_correction_rejection_preserves_record(self):
        with connect_db() as c:
            r = dict(c.execute('SELECT * FROM records LIMIT 1').fetchone())
        response = self.client.post('/correct/' + str(r['id']), data={'csrf': 'test-token', 'prepared': '1', 'sold': '2', 'waste': '0', 'reason': 'test'})
        self.assertEqual(response.status_code, 200)
        with connect_db() as c:
            after = dict(c.execute('SELECT * FROM records WHERE id=?', (r['id'],)).fetchone())
        self.assertEqual(r, after)

    def test_staff_adds_and_corrects_own_record(self):
        self.login_fixture(2, 'Staff')
        values = dict(csrf='test-token', day='2026-09-01', item='Tuna Sandwich',
                      period='Lunch', prepared='100', sold='80', waste='0')
        self.assertEqual(self.client.post('/add', data={**values, 'leftover': '99'}).status_code, 200)
        response = self.client.post('/add', data=values)
        self.assertEqual(response.status_code, 302)
        with connect_db() as db:
            record = db.execute("SELECT id FROM records WHERE creator=2 AND source='manual'").fetchone()[0]
        try:
            self.assertIn(b'Tuna Sandwich', self.client.get('/?q=Tuna&sort=oldest').data)
            path = f'/correct/{record}'
            self.assertEqual(self.client.post(path, data={**values, 'sold': '90', 'reason': 'Test edit'}).status_code, 302)
            self.assertEqual(self.client.post(path, data={**values, 'sold': '120', 'reason': 'Invalid edit'}).status_code, 200)
            with connect_db() as db:
                self.assertEqual(db.execute('SELECT prepared-sold FROM records WHERE id=?', (record,)).fetchone()[0], 10)
        finally:
            with connect_db() as db:
                db.execute('DELETE FROM corrections WHERE record_id=?', (record,))
                db.execute('DELETE FROM records WHERE id=?', (record,))

    def test_record_panel_validates_and_links_existing_record(self):
        with connect_db() as db:
            row = db.execute("SELECT * FROM records WHERE creator IS NULL LIMIT 1").fetchone()
        self.login_fixture(2, 'Staff')
        self.assertEqual(self.client.get(f'/correct/{row["id"]}').status_code, 403)
        details = self.client.get(f'/records/{row["id"]}', headers={'X-Kantya-Panel': '1'})
        self.assertIn(b'Record Details', details.data)
        self.assertNotIn(b'Correct record', details.data)
        payload = dict(csrf='test-token', item=row['item'], day=row['day'], period=row['period'],
                       prepared='40', sold='41', waste='0')
        valid = {**payload, 'sold': '30'}
        cases = (
            ({'item': 'Unknown'}, b'Select a valid food item.'),
            ({'period': 'Dinner'}, b'Select a valid selling period.'),
            ({'day': '2026-02-30'}, b'Use a valid YYYY-MM-DD record date.'),
            ({'prepared': '-1'}, b'Quantities must be non-negative whole numbers.'),
            ({'sold': '1.5'}, b'Quantities must be non-negative whole numbers.'),
            ({'waste': '11'}, b'Actual Waste cannot exceed Leftover.'),
            ({'leftover': '10'}, b'Leftover is calculated by KANTYA.'),
        )
        for fields, message in cases:
            with self.subTest(fields=fields):
                invalid = self.client.post('/add', data={**valid, **fields})
                self.assertIn(message, invalid.data)
        error = self.client.post('/add?back=/?page%3D2', data=payload, headers={'X-Kantya-Panel': '1'})
        self.assertIn(b'Sold cannot exceed Prepared.', error.data)
        self.assertIn(b'value="41"', error.data)
        duplicate = self.client.post('/add', data={**payload, 'sold': '40'})
        self.assertIn(b'View existing record', duplicate.data)

    def test_dashboard_roles_and_removed_daily_page(self):
        self.assertEqual(self.client.get('/daily').location, '/')
        manager = self.client.get('/dashboard').data
        self.assertIn(b'Preparation Review', manager)
        self.assertNotIn(b'View Supporting Records', manager)
        waste = self.client.get('/dashboard?view=waste').data
        self.assertIn(b'Records With and Without Waste', waste)
        self.assertIn(b'1,317', waste)
        self.assertIn(b'1,114', waste)
        self.login_fixture(2, 'Staff')
        staff = self.client.get('/dashboard').data
        self.assertIn(b'Add food record', staff)
        self.assertNotIn(b'Preparation Review', staff)
        self.assertEqual(self.client.get('/approvals').status_code, 403)

    def test_approval_decisions_and_existing_accounts(self):
        from werkzeug.security import generate_password_hash
        with connect_db() as db:
            uid = db.execute("INSERT INTO users(username,password,role,email,status) VALUES(?,?,?,?,?)",
                             ('applicant', generate_password_hash('Testing only'), 'Manager',
                              'applicant@example.test', 'pending')).lastrowid
        try:
            self.assertIn(b'applicant@example.test', self.client.get('/approvals').data)
            result = self.client.post('/approvals', data=dict(csrf='test-token', user_id=str(uid), decision='rejected'),
                                      follow_redirects=True)
            self.assertIn(b'Manager request rejected.', result.data)
            self.assertIn(b'Existing Accounts', result.data)
            repeated = self.client.post('/approvals', data=dict(csrf='test-token', user_id=str(uid), decision='active'),
                                        follow_redirects=True)
            self.assertIn(b'This request is no longer pending.', repeated.data)
            with connect_db() as db:
                self.assertEqual(db.execute('SELECT status FROM users WHERE id=?',(uid,)).fetchone()[0], 'rejected')
        finally:
            with connect_db() as db:
                db.execute('DELETE FROM users WHERE id=?', (uid,))

    def test_dashboard_latest_and_comparable_unit_totals(self):
        from dashboard_view import dashboard_view
        rows = [
            dict(item='Arroz Caldo', period='Breakfast', day='2026-09-28', prepared=25, sold=20, waste=1),
            dict(item='Arroz Caldo', period='Breakfast', day='2026-09-29', prepared=30, sold=25, waste=1),
            dict(item='Chicken Adobo', period='Lunch', day='2026-09-29', prepared=30, sold=27, waste=0),
        ]
        filters = dict(item='', start='2026-09-28')
        result = dashboard_view(rows, filters, date(2026, 9, 30))
        self.assertEqual(result['day'], result['yesterday'])
        self.assertFalse(result['comparable'])
        self.assertEqual([(r['unit'], r['sold'], r['delta']) for r in result['units']],
                         [('bowls',25,None),('servings',27,None)])
        comparable = dashboard_view(rows[:2], filters, date(2026, 10, 2))
        self.assertEqual(comparable['day'], '2026-09-29')
        self.assertTrue(comparable['comparable'])
        self.assertEqual(comparable['units'][0]['delta'], 5)

    def test_demo_forecast_does_not_write(self):
        with connect_db() as c:
            before = c.execute('SELECT count(*) FROM forecast_runs').fetchone()[0]
        r = self.client.post('/forecast', data={'csrf': 'test-token', 'mode': 'demo'})
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Demo forecast.', r.data)
        self.assertIn(b'Five-date sales estimates', r.data)
        with connect_db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM forecast_runs').fetchone()[0], before)

    def test_forecast_generates_using_confirmed_calendar_fixture(self):
        from datetime import timedelta
        first = date.fromisoformat(module.app.config['CALENDAR_END']) + timedelta(days=1)
        days = [first + timedelta(days=n) for n in range(12)]
        extension = {d.isoformat(): d.weekday() < 5 for d in days}
        with patch.dict(module.app.config, CALENDAR_EXTENSION=extension, CALENDAR_END=days[-1].isoformat()):
            response = self.client.post('/forecast', data={'csrf': 'test-token'})
            self.assertEqual(response.status_code, 302)
            page = self.client.get(response.location)
            self.assertIn(b'Forecast Summary', page.data)
            self.assertIn(b'forecast-region', page.data)
            self.assertIn(b'Five-date sales estimates', page.data)
        try:
            with connect_db() as db:
                results = json.loads(db.execute('SELECT results FROM forecast_runs ORDER BY id DESC LIMIT 1').fetchone()[0])
            self.assertEqual(len({row['day'] for row in results}), 5)
        finally:
            with connect_db() as db:
                db.execute('DELETE FROM forecast_runs')

    def test_no_closed_date_entry(self):
        for d in ['2026-06-15', '2026-09-18', '2026-10-08']:
            with self.assertRaises(ValueError):
                valid_day(d)

    def test_valid_correction_has_audit_and_invalidates_signature(self):
        with connect_db() as c:
            r = dict(c.execute('SELECT * FROM records ORDER BY id LIMIT 1').fetchone())
            before = c.execute('SELECT count(*) FROM corrections').fetchone()[0]
        old_signature = signature([r])
        response = self.client.post('/correct/' + str(r['id']), data={'csrf': 'test-token', 'prepared': str(r['prepared'] + 1), 'sold': str(r['sold']), 'waste': str(r['waste']), 'reason': 'Verification fixture'})
        self.assertEqual(response.status_code, 302)
        with connect_db() as c:
            revised = dict(c.execute('SELECT * FROM records WHERE id=?', (r['id'],)).fetchone())
            self.assertEqual(c.execute('SELECT count(*) FROM corrections').fetchone()[0], before + 1)
            self.assertNotEqual(signature([revised]), old_signature)
            c.execute('UPDATE records SET prepared=? WHERE id=?', (r['prepared'], r['id']))
            c.execute("DELETE FROM corrections WHERE reason='Verification fixture'")

    def test_import_late_invalid_row_is_atomic(self):
        import io
        text = 'food_item_name,record_date,selling_period,quantity_prepared,quantity_sold,leftover,actual_waste\nChicken Adobo,2026-09-01,Breakfast,10,8,2,1\nChicken Adobo,2026-09-02,Breakfast,10,12,-2,0\n'
        with connect_db() as c:
            before = c.execute('SELECT count(*) FROM records').fetchone()[0]
        response = self.client.post('/import', data={'csrf': 'test-token', 'file': (io.BytesIO(text.encode()), 'fixture.csv')})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Import cancelled', response.data)
        with connect_db() as c:
            self.assertEqual(c.execute('SELECT count(*) FROM records').fetchone()[0], before)

    def test_notebook_recipe_fits_same_item_history(self):
        from forecasting import predict
        with connect_db() as c:
            y = [r[0] for r in c.execute("SELECT sold FROM records WHERE item='Chicken Adobo' AND period='Lunch' ORDER BY day")]
        p = predict(module.app.config['FORECAST_METHOD'], y, 5)
        self.assertEqual(len(p), 5)
        self.assertTrue(all((math.isfinite(float(v)) and v >= 0 for v in p)))

    def test_unauthenticated_redirect(self):
        c = module.app.test_client()
        for path in ['/dashboard', '/reports', '/forecast', '/']:
            self.assertEqual(c.get(path).status_code, 302)
if __name__ == '__main__':
    unittest.main(verbosity=2)
