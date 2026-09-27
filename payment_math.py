"""Canadian mortgage payment math used for payment scenario charts.

Fixed-rate Canadian mortgages compound semi-annually by law (Interest Act);
variable-rate products here compound monthly. All figures are estimates for
illustration, never an offer or a statement of account.
"""

from __future__ import annotations

from typing import Any

PERIODS_PER_YEAR = {
    "monthly": 12,
    "semi_monthly": 24,
    "bi_weekly": 26,
    "accelerated_bi_weekly": 26,
    "weekly": 52,
    "accelerated_weekly": 52,
}

FREQUENCY_LABELS = {
    "monthly": "Monthly",
    "semi_monthly": "Semi-monthly",
    "bi_weekly": "Bi-weekly",
    "accelerated_bi_weekly": "Accelerated bi-weekly",
    "weekly": "Weekly",
    "accelerated_weekly": "Accelerated weekly",
}

MAX_YEARS = 40


def periodic_rate(annual_rate: float, periods_per_year: int, compounding: str) -> float:
    if compounding == "semi_annual":
        return (1 + annual_rate / 2) ** (2 / periods_per_year) - 1
    return (1 + annual_rate / 12) ** (12 / periods_per_year) - 1


def level_payment(balance: float, annual_rate: float, years: float, periods_per_year: int, compounding: str) -> float:
    r = periodic_rate(annual_rate, periods_per_year, compounding)
    n = round(years * periods_per_year)
    if r == 0:
        return balance / n
    return balance * r / (1 - (1 + r) ** -n)


def payment_for_frequency(monthly_payment: float, frequency: str, balance: float, annual_rate: float, years: float, compounding: str) -> float:
    """Payment amount for a frequency, derived the way Canadian lenders quote it."""

    if frequency == "monthly":
        return monthly_payment
    if frequency == "accelerated_bi_weekly":
        return monthly_payment / 2
    if frequency == "accelerated_weekly":
        return monthly_payment / 4
    # Non-accelerated frequencies keep the same amortization.
    return level_payment(balance, annual_rate, years, PERIODS_PER_YEAR[frequency], compounding)


def amortize(balance: float, annual_rate: float, payment: float, periods_per_year: int, compounding: str) -> dict[str, Any]:
    """Run the schedule to payoff. Returns years, total interest, and yearly balances."""

    r = periodic_rate(annual_rate, periods_per_year, compounding)
    if payment <= balance * r:
        return {"pays_off": False, "years": None, "total_interest": None, "yearly_balances": [round(balance, 2)]}
    remaining, interest_total, period = balance, 0.0, 0
    yearly = [round(balance, 2)]
    while remaining > 0.005 and period < MAX_YEARS * periods_per_year:
        interest = remaining * r
        interest_total += interest
        remaining = max(0.0, remaining + interest - payment)
        period += 1
        if period % periods_per_year == 0 or remaining <= 0.005:
            yearly.append(round(remaining, 2))
    return {
        "pays_off": remaining <= 0.005,
        "years": round(period / periods_per_year, 2),
        "total_interest": round(interest_total, 2),
        "yearly_balances": yearly,
    }


def build_scenario(
    record: dict[str, Any],
    *,
    new_payment_amount: float | None = None,
    new_frequency: str | None = None,
    prepayment_amount: float | None = None,
) -> dict[str, Any]:
    """Compare the current schedule with the caller's proposed change."""

    balance = float(record["balance"])
    rate = float(record["rate"])
    compounding = record["compounding"]
    years = record["remaining_amortization_months"] / 12
    frequency = record["payment_frequency"]
    payment = float(record["payment_amount"])
    monthly = level_payment(balance, rate, years, 12, compounding)

    current = amortize(balance, rate, payment, PERIODS_PER_YEAR[frequency], compounding)

    proposed_frequency = new_frequency if new_frequency in PERIODS_PER_YEAR else frequency
    if new_frequency in PERIODS_PER_YEAR and new_frequency != frequency:
        proposed_payment = payment_for_frequency(monthly, new_frequency, balance, rate, years, compounding)
    else:
        proposed_payment = payment
    if new_payment_amount:
        proposed_payment = float(new_payment_amount)
    proposed_balance = balance - float(prepayment_amount or 0)
    if proposed_balance < 0:
        raise ValueError("Prepayment is larger than the outstanding balance.")
    proposed = amortize(proposed_balance, rate, proposed_payment, PERIODS_PER_YEAR[proposed_frequency], compounding)

    labels = []
    if prepayment_amount:
        labels.append(f"${prepayment_amount:,.0f} lump sum")
    if proposed_frequency != frequency:
        labels.append(FREQUENCY_LABELS[proposed_frequency].lower())
    if new_payment_amount:
        labels.append(f"${proposed_payment:,.2f} payments")
    saved_interest = (
        round(current["total_interest"] - proposed["total_interest"], 2)
        if current["pays_off"] and proposed["pays_off"]
        else None
    )
    saved_years = (
        round(current["years"] - proposed["years"], 2)
        if current["pays_off"] and proposed["pays_off"]
        else None
    )
    return {
        "title": "Proposed: " + (", ".join(labels) if labels else "no change"),
        "current": {
            "payment": round(payment, 2),
            "frequency": FREQUENCY_LABELS[frequency],
            **current,
        },
        "proposed": {
            "payment": round(proposed_payment, 2),
            "frequency": FREQUENCY_LABELS[proposed_frequency],
            "balance_after_prepayment": round(proposed_balance, 2),
            **proposed,
        },
        "interest_saved": saved_interest,
        "years_saved": saved_years,
        "assumptions": (
            f"Rate {rate * 100:.2f}% held for the full amortization, "
            f"{'semi-annual' if compounding == 'semi_annual' else 'monthly'} compounding, "
            "no missed payments or further prepayments. Estimate only, not an offer."
        ),
    }


__all__ = ["FREQUENCY_LABELS", "PERIODS_PER_YEAR", "amortize", "build_scenario", "level_payment", "payment_for_frequency"]
