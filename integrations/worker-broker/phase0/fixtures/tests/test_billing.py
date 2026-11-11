import pytest
from datetime import date

from acme.billing import prorate, days_in_month
from acme.audit import LOG


def test_prorate_half_year():
    # Jul 2 2025 (non-leap) through Dec 31: 183 days
    r = prorate(365.0, date(2025, 7, 2))
    assert r == pytest.approx(183.0)


def test_days_in_month_plain():
    assert days_in_month(2025, 1) == 31
    assert days_in_month(2025, 4) == 30


def test_prorate_leap_february():
    # Feb 28 2024 through Dec 31 2024: 2 (Feb 28,29) + 306 = 308 of 366 days
    r = prorate(366.0, date(2024, 2, 28))
    assert r == pytest.approx(308.0)


def test_audit_saw_inventory_activity():
    # Depends on other test files having exercised inventory first.
    assert len(LOG) >= 1
