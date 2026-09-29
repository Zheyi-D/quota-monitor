"""Chronological order for MM/DD/YYYY quota dates."""

import unittest

from quota_monitor.core import sort_quota_dates


class SortQuotaDatesTest(unittest.TestCase):
    def test_year_boundary_is_chronological(self):
        dates = ["01/15/2026", "12/01/2025", "01/02/2026"]
        self.assertEqual(
            sort_quota_dates(dates),
            ["12/01/2025", "01/02/2026", "01/15/2026"],
        )

    def test_same_year_orders_month_then_day(self):
        self.assertEqual(
            sort_quota_dates(["03/01/2026", "01/31/2026", "02/01/2026"]),
            ["01/31/2026", "02/01/2026", "03/01/2026"],
        )


if __name__ == "__main__":
    unittest.main()
