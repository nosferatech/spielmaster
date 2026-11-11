"""Billing: prorate an annual amount from a start date."""
from datetime import date

MONTH_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def is_leap(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def days_in_month(year, month):
    return MONTH_DAYS[month - 1]


def prorate(annual_amount, start_date):
    """Charge for the part of the year from start_date to Dec 31 inclusive."""
    year_len = 366 if is_leap(start_date.year) else 365
    remaining = days_in_month(start_date.year, start_date.month) - start_date.day + 1
    for m in range(start_date.month + 1, 13):
        remaining += days_in_month(start_date.year, m)
    return annual_amount * remaining / year_len
