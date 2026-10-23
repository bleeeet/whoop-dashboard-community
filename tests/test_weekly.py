from datetime import datetime, timezone
import pathlib
import tempfile
import unittest
from unittest.mock import patch
import weekly


class WeeklyTests(unittest.TestCase):
    def test_calendar_window_and_timezone(self):
        a,b=weekly.window(datetime(2026,10,25,23,tzinfo=timezone.utc))
        self.assertEqual((str(a),str(b)),('2026-10-19','2026-10-26'))
        a,b=weekly.window(datetime(2026,10,25,12,tzinfo=timezone.utc))
        self.assertEqual((str(a),str(b)),('2026-10-12','2026-10-19'))

    def test_naive_now_rejected(self):
        with self.assertRaises(ValueError):weekly.window(datetime(2026,10,23))

    def test_empty_no_zero_no_outside_data(self):
        with tempfile.TemporaryDirectory() as d:
            text=weekly.generate(d,datetime(2026,10,23,tzinfo=timezone.utc))
        self.assertIn('0/7 天',text)
        self.assertIn('暂无数据',text)
        self.assertIn('2026-10-12—2026-10-18',text)
        self.assertNotIn('0.0%',text)

    def test_synthetic_week_no_personal_identifiers(self):
        fixture=pathlib.Path(__file__).resolve().parents[1]/'fixtures/multi'
        text=weekly.generate(fixture,datetime(2026,9,1,tzinfo=timezone.utc))
        self.assertIn('2026-08-24—2026-08-30',text)
        self.assertNotIn('demo@example.com',text)
        self.assertNotIn('Demo User',text)
        self.assertNotIn('10000001',text)
        self.assertIn('无需 DeepSeek',text)

    def test_incomplete_cycle_strain_excluded_and_missing_not_zero(self):
        rows=[{'date':'2026-10-13','cycle':{'end':None,'score':{'strain':19}},'recovery':{'score':{'recovery_score':70}}},
              {'date':'2026-10-14','cycle':{'end':'done','score':{'strain':10}}}]
        with patch.object(weekly.report,'load',return_value={'workouts':[]}),patch.object(weekly.report,'build_days',return_value=rows):
            text=weekly.generate('unused',datetime(2026,10,23,tzinfo=timezone.utc))
        self.assertIn('| 平均完成日负荷 | 10.0 | 1/7 |',text)
        self.assertIn('| 平均恢复分 | 70.0% | 1/7 |',text)


if __name__=='__main__':unittest.main()
