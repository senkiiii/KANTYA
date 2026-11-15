"""Independent arithmetic cases for dashboard comparisons, scales and aggregation."""
from datetime import date, timedelta
from pathlib import Path
import sys, unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard_analysis import comparison, outcomes, heatmap, distribution, trend, dot_chart, workspace
OPERATING = lambda d: (d if isinstance(d, date) else date.fromisoformat(d)).weekday() < 5
UNITS = {'A': 'Piece', 'B': 'Bowl'}
FILTERS = dict(start='2026-09-01', end='2026-09-14', item='A', period='', view='overview', grain='auto')
DATES = ['2026-09-01', '2026-09-02', '2026-09-03', '2026-09-04', '2026-09-07', '2026-09-08', '2026-09-09', '2026-09-10', '2026-09-11', '2026-09-14']

def row(day, sold, period='Lunch', item='A', prepared=50, waste=2):
    return dict(day=day, item=item, period=period, prepared=prepared, sold=sold, waste=waste)

class DashboardTests(unittest.TestCase):

    def test_comparison_uses_daily_sum_and_percentage_points(self):
        rows = [row(d, 10 if i < 5 else 20, p, waste=2 if i < 5 else 4) for i, d in enumerate(DATES) for p in ['Lunch', 'Snack']]
        c = comparison(rows, FILTERS, UNITS, OPERATING, {('A', 'Lunch'), ('A', 'Snack')})['rows'][0]
        self.assertEqual(c['prior']['mean_daily'], 20)
        self.assertEqual(c['recent']['mean_daily'], 40)
        self.assertEqual(c['change'], 100)
        self.assertEqual(c['waste_delta'], 4)
        self.assertTrue(c['complete'])

    def test_missing_entire_period_withholds_comparison(self):
        rows = [row(d, 10) for d in DATES]
        c = comparison(rows, FILTERS, UNITS, OPERATING, {('A', 'Lunch'), ('A', 'Snack')})['rows'][0]
        self.assertFalse(c['complete'])
        self.assertIsNone(c['change'])
        self.assertIsNone(c['waste_delta'])

    def test_zero_prior_and_short_scope(self):
        rows = [row(d, 0 if i < 5 else 10) for i, d in enumerate(DATES)]
        c = comparison(rows, FILTERS, UNITS, OPERATING)['rows'][0]
        self.assertIsNone(c['change'])
        f = dict(FILTERS, end='2026-09-04')
        self.assertFalse(comparison(rows, f, UNITS, OPERATING)['available'])

    def test_outcomes_partition_preparation(self):
        g = outcomes([('A', [row(DATES[0], 35, prepared=50, waste=5)])])['entries'][0]
        self.assertEqual(g['sold_rate'] + g['waste_rate'] + g['unknown_rate'], 100)
        self.assertEqual(g['unknown'], 10)
        self.assertIsNone(outcomes([('A', [row(DATES[0], 0, prepared=0, waste=0)])]))

    def test_weekly_mean_is_not_weekly_sum(self):
        rows = [row('2026-09-01', 10), row('2026-09-02', 30), row('2026-09-08', 40)]
        g = trend(rows, ['sold'], dict(FILTERS, grain='weekly'), OPERATING, 'Piece')
        vals = [p['value'] for p in g['paths'][0]['points']]
        self.assertEqual(vals, [20, 40])
        self.assertIn('2 observed dates / 4 operating dates', g['context']['2026-08-31'])

    def test_heatmap_ratio_uses_totals_and_missing_is_blank(self):
        rows = [row('2026-09-01', 9, prepared=10, waste=1), row('2026-09-08', 80, prepared=100, waste=10)]
        g = heatmap(rows, FILTERS, UNITS, 'weekday_waste')
        cells = g['rows'][0]['cells']
        self.assertIsNone(cells[0]['value'])
        self.assertEqual(cells[1]['value'], 10)
        self.assertEqual(cells[1]['dates'], 2)

    def test_distribution_and_honest_dot_domains(self):
        rows = [row(DATES[i], v) for i, v in enumerate([0, 10, 20, 30, 40])]
        q = distribution(rows, 'Piece')['entries'][0]
        self.assertEqual((q['q1'], q['median'], q['q3']), (10, 20, 30))
        g = dot_chart([dict(value=91), dict(value=92)], 'Rate', focused=True)
        self.assertEqual((g['low'], g['high']), (90, 93))
        g = dot_chart([dict(value=3)], 'Rate')
        self.assertEqual(g['low'], 0)

    def test_portfolio_median_is_not_quantity_pool(self):
        rows = [row(DATES[0], 9, prepared=10, waste=1), row(DATES[0], 50, item='B', prepared=100, waste=10)]
        data = workspace(rows, dict(FILTERS, item=''), UNITS, OPERATING)
        self.assertEqual(data['portfolio']['median_sell'], 70)
        self.assertIn('pie', [p['graph']['kind'] for p in data['charts']])

    def test_relative_variation_and_weekday_index(self):
        rows = [row('2026-09-07', 0), row('2026-09-08', 20)]
        data = workspace(rows, dict(FILTERS, item='', view='patterns'), UNITS, OPERATING)
        cv = next((p['graph'] for p in data['charts'] if 'Relative variation' in p['title']))
        self.assertEqual(cv['entries'][0]['value'], 100)
        indexed = heatmap(rows, dict(FILTERS, item=''), UNITS)
        self.assertEqual(indexed['rows'][0]['cells'][0]['value'], 0)
        self.assertEqual(indexed['rows'][0]['cells'][1]['value'], 200)
        self.assertNotEqual(indexed['rows'][0]['cells'][0]['color'], indexed['rows'][0]['cells'][1]['color'])
if __name__ == '__main__':
    unittest.main(verbosity=2)
