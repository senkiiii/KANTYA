import os
import secrets
from datetime import date

from flask import Flask, redirect, url_for

import auth
import backup_view
import database
import records
import reporting
import revision_views
from operating_calendar import BUNDLED_CALENDAR_END, load_calendar_extension


def load_local_env():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.isfile(path):
        return
    with open(path, encoding='utf-8-sig') as config_file:
        lines = config_file.readlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key.startswith('KANTYA_'):
            os.environ.setdefault(key, value)


load_local_env()
app = Flask(__name__)
app.secret_key = os.environ.get('KANTYA_SECRET') or secrets.token_hex(32)
if os.environ.get('KANTYA_ENV') == 'production' and (not os.environ.get('KANTYA_SECRET') or len(os.environ['KANTYA_SECRET']) < 32):
    raise RuntimeError('Set a random KANTYA_SECRET of at least 32 characters for production')
app.jinja_env.filters['day_name'] = lambda value: date.fromisoformat(value).strftime('%A')
app.config.update(
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('KANTYA_HTTPS') == '1',
    PERMANENT_SESSION_LIFETIME=auth.SESSION_IDLE,
    SESSION_REFRESH_EACH_REQUEST=False,
)
app.config['CONFIGURATION_FILE'] = os.path.join(app.root_path, '.env')
app.config['DATABASE'] = os.environ.get('KANTYA_DB', os.path.join(app.root_path, 'kantya.db'))
extension = load_calendar_extension(os.environ.get('KANTYA_CALENDAR_EXTENSION'), BUNDLED_CALENDAR_END)
app.config.update(
    CALENDAR_EXTENSION=extension,
    CALENDAR_END=max(extension, default=BUNDLED_CALENDAR_END),
)


@app.template_filter('percent')
def format_percent(value):
    return 'N/A' if value is None else f'{value:.2f}%'


@app.template_filter('number')
def format_number(value):
    return 'N/A' if value is None else f'{value:,.1f}'


app.get('/choose-role')(auth.choose_role)
app.route('/login', methods=['GET', 'POST'])(auth.login)
app.route('/signup', methods=['GET', 'POST'])(auth.signup)
app.route('/verify-registration', methods=['GET', 'POST'])(auth.verify_registration)
app.route('/approvals', methods=['GET', 'POST'])(auth.require_auth(manager=True)(auth.approvals))
app.route('/verify-otp', methods=['GET', 'POST'])(auth.verify_otp)
app.post('/resend-otp')(auth.resend_otp)
app.route('/forgot-password', methods=['GET', 'POST'])(auth.forgot_password)
app.route('/reset-password', methods=['GET', 'POST'])(auth.reset_password)
app.route('/change-password', methods=['GET', 'POST'])(auth.require_auth()(auth.change_password))
app.before_request(auth.enforce_password_change)
app.route('/backup-recovery', methods=['GET', 'POST'])(
    auth.require_auth(manager=True)(backup_view.backup_recovery))
app.get('/backup/download/<name>')(auth.require_auth(manager=True)(backup_view.download_backup))
app.post('/logout')(auth.require_auth()(auth.logout))
app.get('/')(auth.require_auth()(records.index))
app.get('/daily')(auth.require_auth()(lambda: redirect(url_for('index'))))
app.get('/records/<int:rid>')(auth.require_auth()(records.view_record))
app.route('/add', methods=['GET', 'POST'])(auth.require_auth()(records.add))
app.route('/records/<int:rid>/delete', methods=['GET', 'POST'])(auth.require_auth(manager=True)(records.delete_record))
app.route('/correct/<int:rid>', methods=['GET', 'POST'])(auth.require_auth()(records.correct))
app.route('/import', methods=['GET', 'POST'])(auth.require_auth()(records.import_csv))
app.get('/api/menu-options')(auth.require_auth()(records.menu_options))
app.route('/bulk', methods=['GET','POST'])(auth.require_auth()(records.bulk_entry))
app.get('/import/template')(auth.require_auth()(records.csv_template))
app.get('/import/errors/<token>')(auth.require_auth()(records.csv_import_errors))
app.get('/dashboard')(auth.require_auth()(revision_views.dashboard))
app.get('/waste')(auth.require_auth(manager=True)(reporting.waste))
app.get('/reports')(auth.require_auth(manager=True)(revision_views.reports))
app.get('/reports/download/<fmt>')(auth.require_auth(manager=True)(revision_views.report_export))
app.get('/forecast')(auth.require_auth()(revision_views.forecast))


with app.app_context():
    database.init()
    app.config['FORECAST_METHOD'] = reporting.model_metadata()['method']
    database.migrate()
    records.seed_synthetic()


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)
