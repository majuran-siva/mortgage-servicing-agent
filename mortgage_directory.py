"""Mock mortgage servicing directory used by the live voice agent.

This stands in for a lender's mortgage servicing system. All borrowers,
addresses, and accounts are fictional. The records line up with the demo
prompts in examples.py.
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

try:
    from .payment_math import PERIODS_PER_YEAR, level_payment, payment_for_frequency
except ImportError:
    from payment_math import PERIODS_PER_YEAR, level_payment, payment_for_frequency

_RAW_RECORDS: list[dict[str, Any]] = [
    {
        "mortgage_number": "MTG-40117",
        "borrowers": ["Maya Singh", "Arjun Singh"],
        "property_postal_code": "M4C 1B5",
        "property_city": "Toronto, ON",
        "product": "5-year fixed closed",
        "rate": 0.0479,
        "compounding": "semi_annual",
        "term_start": "2024-03-01",
        "maturity_date": "2029-03-01",
        "original_principal": 480000,
        "balance": 412300.00,
        "remaining_amortization_months": 264,
        "payment_frequency": "monthly",
        "payment_day": "1st of the month",
        "status": "active",
        "missed_payments_12m": 0,
        "prepayment_lump_sum_pct": 0.15,
        "payment_increase_pct": 0.15,
        "prepaid_this_year": 0,
        "skip_payment_allowed": True,
        "property_tax_in_payment": True,
        "contact_changed_days_ago": 400,
        "notes": ["Property tax is collected with each payment and paid to the City of Toronto."],
    },
    {
        "mortgage_number": "MTG-52290",
        "borrowers": ["Jordan Lee"],
        "property_postal_code": "T2N 1N4",
        "property_city": "Calgary, AB",
        "product": "5-year variable closed",
        "rate": 0.0405,
        "compounding": "monthly",
        "term_start": "2025-06-15",
        "maturity_date": "2030-06-15",
        "original_principal": 520000,
        "balance": 503850.00,
        "remaining_amortization_months": 336,
        "payment_frequency": "bi_weekly",
        "payment_day": "Every second Friday",
        "status": "active",
        "missed_payments_12m": 0,
        "prepayment_lump_sum_pct": 0.20,
        "payment_increase_pct": 0.20,
        "prepaid_this_year": 5000,
        "skip_payment_allowed": True,
        "property_tax_in_payment": False,
        "contact_changed_days_ago": 210,
        "notes": ["Variable rate: prime minus 0.90%. Payments are fixed; amortization moves with prime."],
    },
    {
        "mortgage_number": "MTG-61845",
        "borrowers": ["Priya Shah"],
        "property_postal_code": "V5K 0A1",
        "property_city": "Vancouver, BC",
        "product": "3-year fixed closed",
        "rate": 0.0529,
        "compounding": "semi_annual",
        "term_start": "2024-11-01",
        "maturity_date": "2027-11-01",
        "original_principal": 600000,
        "balance": 548900.00,
        "remaining_amortization_months": 276,
        "payment_frequency": "monthly",
        "payment_day": "15th of the month",
        "status": "active",
        "missed_payments_12m": 0,
        "prepayment_lump_sum_pct": 0.10,
        "payment_increase_pct": 0.10,
        "prepaid_this_year": 52000,
        "skip_payment_allowed": False,
        "property_tax_in_payment": False,
        "contact_changed_days_ago": 700,
        "notes": ["$52,000 of the $60,000 annual lump-sum allowance has been used this anniversary year."],
    },
    {
        "mortgage_number": "MTG-70032",
        "borrowers": ["Sam Rivera"],
        "property_postal_code": "B3H 4R2",
        "property_city": "Halifax, NS",
        "product": "5-year fixed closed",
        "rate": 0.0549,
        "compounding": "semi_annual",
        "term_start": "2023-09-01",
        "maturity_date": "2028-09-01",
        "original_principal": 350000,
        "balance": 321400.00,
        "remaining_amortization_months": 288,
        "payment_frequency": "monthly",
        "payment_day": "1st of the month",
        "status": "in_arrears",
        "missed_payments_12m": 2,
        "prepayment_lump_sum_pct": 0.15,
        "payment_increase_pct": 0.15,
        "prepaid_this_year": 0,
        "skip_payment_allowed": True,
        "property_tax_in_payment": True,
        "contact_changed_days_ago": 900,
        "notes": ["Two missed payments (August and September 2026). Account is in arrears."],
    },
    {
        "mortgage_number": "MTG-88410",
        "borrowers": ["Alex Chen"],
        "property_postal_code": "K1N 6N5",
        "property_city": "Ottawa, ON",
        "product": "5-year fixed closed",
        "rate": 0.0459,
        "compounding": "semi_annual",
        "term_start": "2025-02-01",
        "maturity_date": "2030-02-01",
        "original_principal": 390000,
        "balance": 377200.00,
        "remaining_amortization_months": 288,
        "payment_frequency": "accelerated_bi_weekly",
        "payment_day": "Every second Thursday",
        "status": "active",
        "missed_payments_12m": 0,
        "prepayment_lump_sum_pct": 0.15,
        "payment_increase_pct": 0.15,
        "prepaid_this_year": 0,
        "skip_payment_allowed": True,
        "property_tax_in_payment": False,
        "contact_changed_days_ago": 3,
        "notes": ["Email and phone on file were changed 3 days ago."],
    },
    {
        "mortgage_number": "MTG-55408",
        "borrowers": ["Daniel Okafor"],
        "property_postal_code": "L5B 3C2",
        "property_city": "Mississauga, ON",
        "product": "5-year fixed closed",
        "rate": 0.0439,
        "compounding": "semi_annual",
        "term_start": "2025-04-01",
        "maturity_date": "2030-04-01",
        "original_principal": 450000,
        "balance": 431250.00,
        "remaining_amortization_months": 300,
        "payment_frequency": "monthly",
        "payment_day": "1st of the month",
        "status": "active",
        "missed_payments_12m": 0,
        "prepayment_lump_sum_pct": 0.15,
        "payment_increase_pct": 0.15,
        "prepaid_this_year": 0,
        "skip_payment_allowed": True,
        "property_tax_in_payment": False,
        "contact_changed_days_ago": 500,
        "home_insurance_expiry": "2026-09-30",
        "notes": ["Home insurance on file expires 2026-09-30. An updated declaration page is needed."],
    },
    {
        "mortgage_number": "MTG-93006",
        "borrowers": ["Chris Park", "Dana Park"],
        "property_postal_code": "H2X 1Y4",
        "property_city": "Montréal, QC",
        "product": "5-year fixed closed",
        "rate": 0.0239,
        "compounding": "semi_annual",
        "term_start": "2021-12-01",
        "maturity_date": "2026-12-01",
        "original_principal": 410000,
        "balance": 338700.00,
        "remaining_amortization_months": 180,
        "payment_frequency": "monthly",
        "payment_day": "1st of the month",
        "status": "active",
        "missed_payments_12m": 0,
        "prepayment_lump_sum_pct": 0.15,
        "payment_increase_pct": 0.15,
        "prepaid_this_year": 0,
        "skip_payment_allowed": True,
        "property_tax_in_payment": True,
        "contact_changed_days_ago": 1200,
        "notes": ["Term matures 2026-12-01. Renewal offer has not been sent yet."],
    },
]


def _key(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _with_derived_fields(record: dict[str, Any]) -> dict[str, Any]:
    years = record["remaining_amortization_months"] / 12
    monthly = level_payment(record["balance"], record["rate"], years, 12, record["compounding"])
    payment = payment_for_frequency(
        monthly, record["payment_frequency"], record["balance"], record["rate"], years, record["compounding"]
    )
    allowance = record["original_principal"] * record["prepayment_lump_sum_pct"]
    return {
        **record,
        "payment_amount": math.ceil(payment * 100) / 100,
        "payments_per_year": PERIODS_PER_YEAR[record["payment_frequency"]],
        "annual_prepayment_allowance": round(allowance, 2),
        "prepayment_remaining_this_year": round(max(0.0, allowance - record["prepaid_this_year"]), 2),
        "max_payment_after_increase": round(payment * (1 + record["payment_increase_pct"]), 2),
    }


MORTGAGE_RECORDS: dict[str, dict[str, Any]] = {
    _key(raw["mortgage_number"]): _with_derived_fields(raw) for raw in _RAW_RECORDS
}


def normalize_mortgage_number(value: str) -> str:
    """Collapse spacing, punctuation, and letter O versus zero confusion in the digits."""

    text = _key(value)
    match = re.match(r"^([A-Z]*?)([0-9O]+)$", text)
    if match:
        prefix, digits = match.groups()
        text = (prefix or "MTG") + digits.replace("O", "0")
    return text


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z]", "", str(value or "").lower())


def is_borrower(record: dict[str, Any], name: str) -> bool:
    target = _normalize_name(name)
    return bool(target) and any(_normalize_name(b) == target for b in record["borrowers"])


def postal_matches(record: dict[str, Any], postal_code: str) -> bool:
    return bool(_key(postal_code)) and _key(postal_code) == _key(record["property_postal_code"])


def find_mortgage(mortgage_number: str) -> dict[str, Any] | None:
    return MORTGAGE_RECORDS.get(normalize_mortgage_number(mortgage_number))


def account_view(record: dict[str, Any]) -> dict[str, Any]:
    """Account details safe to share with a verified borrower. Never includes the postal code."""

    return {k: v for k, v in record.items() if k not in {"property_postal_code"}}


def lookup_mortgage(mortgage_number: str, borrower_name: str = "", postal_code: str = "") -> dict[str, Any]:
    """Look up a mortgage. Account details are returned only once identity is verified."""

    if not _key(mortgage_number):
        return {
            "found": False,
            "verified": False,
            "mortgage_number": "",
            "message": "No mortgage number was provided. Ask the caller to read it from their annual statement.",
        }
    record = find_mortgage(mortgage_number)
    if record is None:
        return {
            "found": False,
            "verified": False,
            "mortgage_number": str(mortgage_number).strip(),
            "message": "No mortgage matched that number. Ask the caller to confirm it digit by digit.",
        }
    name_ok, postal_ok = is_borrower(record, borrower_name), postal_matches(record, postal_code)
    if not (name_ok and postal_ok):
        missing = []
        if not _normalize_name(borrower_name):
            missing.append("the borrower's full name")
        if not _key(postal_code):
            missing.append("the property postal code")
        return {
            "found": True,
            "verified": False,
            "mortgage_number": record["mortgage_number"],
            "verification_failed": bool(not missing),
            "message": (
                f"Before sharing any account details, ask for {' and '.join(missing)}."
                if missing
                else "The name or postal code does not match this mortgage. Do not share account details. "
                "Ask the caller to confirm once; if it still does not match, a specialist must verify them."
            ),
        }
    return {"found": True, "verified": True, **account_view(record)}


def mortgage_review(request, today: date | None = None) -> list[str]:
    """Deterministic identity checks used by the servicing rules."""

    record = find_mortgage(request.mortgage_number)
    if record is None:
        return ["Mortgage number needs confirmation"]
    issues = []
    if _normalize_name(request.borrower_name) and not is_borrower(record, request.borrower_name):
        issues.append("Caller name is not a borrower on this mortgage")
    if _key(request.property_postal_code) and not postal_matches(record, request.property_postal_code):
        issues.append("Property postal code does not match this mortgage")
    return issues


def status_headline(result: dict[str, Any]) -> str:
    if not result.get("found"):
        return "Not found"
    if not result.get("verified"):
        return "Found - identity not verified"
    status = str(result.get("status", "unknown"))
    return {"active": "Active", "in_arrears": "In arrears - hardship team"}.get(status, status.replace("_", " ").title())


__all__ = [
    "MORTGAGE_RECORDS",
    "account_view",
    "find_mortgage",
    "is_borrower",
    "lookup_mortgage",
    "mortgage_review",
    "normalize_mortgage_number",
    "postal_matches",
    "status_headline",
]
