"""Check exploratory-chart data against simple independent arithmetic cases."""
from pathlib import Path
import sys, unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from basic_charts import pie, histogram, item_scatter, series_scatter

class BasicChartsTests(unittest.TestCase):

    def test_pie_parts_and_zero_total(self):
        g = pie([('Sold', 80), ('Waste', 5), ('Remaining', 15)], 'Prepared', 'Piece')
        self.assertEqual(g['total'], 100)
        self.assertEqual(sum((e['share'] for e in g['entries'])), 100)
        self.assertEqual([e['value'] for e in g['entries']], [80, 5, 15])
        self.assertIsNone(pie([('Sold', 0)], 'Prepared', 'Piece'))

    def test_histogram_conserves_observation_count_and_endpoints(self):
        g = histogram([0, 0, 1, 10, 100], '%', percent=True)
        self.assertEqual(sum((e['count'] for e in g['entries'])), 5)
        self.assertEqual(g['entries'][0]['count'], 3)
        self.assertEqual(g['entries'][-1]['count'], 1)
        g = histogram([7] * 20, 'Piece')
        self.assertEqual(sum((e['count'] for e in g['entries'])), 20)

    def test_scatter_uses_record_grain_with_common_equality_scale(self):
        rows = [dict(item='A', day=f'2026-09-{i + 1:02}', period='Lunch', prepared=20 + i, sold=10 + i, waste=2) for i in range(10)]
        g = item_scatter(rows, 'Piece')
        self.assertEqual(g['n'], 10)
        self.assertEqual(g['maxx'], g['maxy'])
        self.assertEqual(g['points'][0]['xvalue'], 20)
        self.assertEqual(g['points'][0]['yvalue'], 10)
        self.assertIsNone(item_scatter(rows[:3], 'Piece'))

    def test_series_scatter_ratio_is_per_series(self):
        rows = [dict(item=f'A{i}', day='2026-09-01', period='Lunch', prepared=100, sold=80 + i, waste=2) for i in range(8)]
        g = series_scatter(rows)
        self.assertEqual(g['n'], 8)
        self.assertEqual(g['points'][0]['xvalue'], 80)
        self.assertEqual(g['points'][0]['yvalue'], 2)
        self.assertTrue(g['focused_x'])
if __name__ == '__main__':
    unittest.main(verbosity=2)
