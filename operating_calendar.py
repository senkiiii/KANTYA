"""Food item catalog and operating-calendar rules."""

import csv
from datetime import date, timedelta

from flask import current_app

from pathlib import Path
DATA_DIR = Path(__file__).resolve().parent / 'data'
def read_menu():
    with (DATA_DIR / 'FIXED_WEEKLY_MENU.csv').open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))
MENU = read_menu()
ITEMS = {r['food_item_name']: r['unit'] for r in MENU}
SCHEDULE = {(r['weekday'],r['selling_period'],r['food_item_name']) for r in MENU}
def menu_allowed(day,period,item):
    d = day if isinstance(day,date) else date.fromisoformat(day)
    return (d.strftime('%A'),period,item) in SCHEDULE

HOLIDAYS = set('2025-10-31 2025-12-08 2025-12-24 2025-12-25 2025-12-30 2025-12-31 2026-01-01 2026-02-17 2026-03-20 2026-04-02 2026-04-03 2026-04-09 2026-05-01 2026-05-27 2026-06-12 2026-08-21 2026-08-31 2026-09-18'.split())
BUNDLED_CALENDAR_END = '2027-04-11'
BUNDLED_CALENDAR_START = '2024-11-12'
with (DATA_DIR/'OPERATING_CALENDAR.csv').open(encoding='utf-8-sig',newline='') as _calendar_file:
    SIM_CALENDAR = {row['date']: row['is_open']=='1' for row in csv.DictReader(_calendar_file)}
SEMESTER_BREAKS = (('2025-12-16', '2026-01-11'), ('2026-05-19', '2026-07-19'))


def load_calendar_extension(path, previous_end):
    if not path:
        return {}
    expected = date.fromisoformat(previous_end) + timedelta(days=1)
    extension = {}
    with open(path, encoding='utf-8-sig', newline='') as file:
        for row in csv.DictReader(file):
            day, is_open = row.get('date'), row.get('is_open')
            if day != expected.isoformat() or is_open not in ('0', '1'):
                raise ValueError('Calendar extension must have contiguous dates and is_open values of 0 or 1.')
            extension[day] = is_open == '1'
            expected += timedelta(days=1)
    if not extension:
        raise ValueError('Calendar extension contains no approved dates.')
    return extension


def is_operating_day(value):
    d = value if isinstance(value, date) else date.fromisoformat(value)
    s = d.isoformat()
    if s in SIM_CALENDAR:
        return SIM_CALENDAR[s]
    if s in current_app.config["CALENDAR_EXTENSION"]:
        return current_app.config["CALENDAR_EXTENSION"][s]
    return d.weekday() < 5 and s not in HOLIDAYS and (not any((start <= s <= end for start, end in SEMESTER_BREAKS)))


def closed_reason(value):
    d = value if isinstance(value, date) else date.fromisoformat(value)
    s = d.isoformat()
    if s in SIM_CALENDAR:
        return None if SIM_CALENDAR[s] else 'Closed in simulated operating calendar'
    if s in current_app.config["CALENDAR_EXTENSION"]:
        return None if current_app.config["CALENDAR_EXTENSION"][s] else 'Closed in approved calendar'
    if d.weekday() >= 5:
        return 'Weekend'
    if any((a <= s <= b for a, b in SEMESTER_BREAKS)):
        return 'Semester break'
    if s in HOLIDAYS:
        return 'Holiday'
    return None
