"""Deterministic mortgage servicing rules, routing, and packet builders.

Rules model common Canadian closed-mortgage terms: an annual lump-sum
prepayment allowance, a payment-increase allowance, prepayment charges on
amounts above the allowance, and interest adjustments when payment dates or
frequencies change. They are examples for a demo, not any lender's terms.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

try:
    from .mortgage_directory import find_mortgage, is_borrower, mortgage_review, postal_matches
    from .payment_math import FREQUENCY_LABELS, level_payment, payment_for_frequency, periodic_rate
    from .schemas import (
        DocumentChecklist,
        DocumentChecklistItem,
        FieldValidation,
        GateSignal,
        RequestClassification,
        RuleFinding,
        SecurityHardshipGate,
        ServiceRequest,
        ServiceRequestPacket,
        ServicingDecision,
    )
except ImportError:
    from mortgage_directory import find_mortgage, is_borrower, mortgage_review, postal_matches
    from payment_math import FREQUENCY_LABELS, level_payment, payment_for_frequency, periodic_rate
    from schemas import (
        DocumentChecklist,
        DocumentChecklistItem,
        FieldValidation,
        GateSignal,
        RequestClassification,
        RuleFinding,
        SecurityHardshipGate,
        ServiceRequest,
        ServiceRequestPacket,
        ServicingDecision,
    )


# Every checklist item has a stable type. Similar words never satisfy a different document.
DOCUMENTS: dict[str, tuple[str, str]] = {
    "void_cheque": (
        "Void cheque or pre-authorized debit form for the new account",
        "Needed before payments can be drawn from a different bank account.",
    ),
    "payout_authorization": (
        "Payout statement request signed by all borrowers",
        "Every borrower on title must authorize a payout statement.",
    ),
    "purchase_agreement": (
        "Agreement of purchase and sale",
        "Confirms the closing date for a sale.",
    ),
    "lender_direction": (
        "Letter of direction from the new lender or lawyer",
        "Confirms where payout funds are coming from when switching or refinancing.",
    ),
    "insurance_declaration": (
        "Home insurance declaration page",
        "Shows the lender is listed as mortgagee and coverage is in force.",
    ),
    "property_tax_bill": (
        "Latest property tax bill",
        "Needed to review tax collected with payments.",
    ),
    "income_change_proof": (
        "Proof of income change (Record of Employment, termination letter, or medical note)",
        "Helps the hardship team assess relief options.",
    ),
    "legal_notice_copy": (
        "Copy of any legal notice received",
        "The hardship team needs the notice and its deadline.",
    ),
}
DOCUMENT_KEYS = {label: key for key, (label, _) in DOCUMENTS.items()}

BLOCKING_FIELD_QUESTIONS = {
    "borrower_name": "What is your full name as it appears on the mortgage?",
    "mortgage_number": "What is your mortgage number? It is on your annual statement.",
    "property_postal_code": "To verify your identity, what is the postal code of the property?",
    "contact_method": "What is the best phone number or email to reach you?",
    "request_summary": "What would you like to change or ask about today?",
    "payment_change_detail": "Which part of your payment would you like to change: the amount, how often you pay, or the payment date?",
    "effective_date": "When would you like the change to start?",
    "prepayment_amount_cad": "How much would you like to prepay?",
    "payout_date": "What date do you need the mortgage paid out by?",
    "payout_reason": "Is the payout for a sale, a switch to another lender, or paying the mortgage off?",
}

HARDSHIP_CATEGORIES = {"job_loss", "income_reduction", "illness", "bereavement", "separation", "arrears", "distress", "legal_notice"}
SECURITY_CATEGORIES = {"third_party_pressure", "suspicious_message", "urgent_payment_redirect", "caller_not_borrower"}
SPECIALIST_TYPES = {"rate_term_change", "life_event", "other"}
# priority_rationale of the placeholder classification used before the model has classified the call.
UNCLASSIFIED = "Waiting for the caller's request."
CONTACT_CHANGE_WINDOW_DAYS = 30
RENEWAL_WINDOW_DAYS = 120


def _as_model(model_type, value):
    if isinstance(value, model_type):
        return value
    if value is None:
        return model_type()
    if isinstance(value, str):
        return model_type.model_validate_json(value)
    return model_type.model_validate(value)


def _blank(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text in {"", "unknown", "not specified", "unspecified", "n/a", "none", "not provided"}


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        normalized = item.strip()
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            result.append(normalized)
    return result


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _parse_date(value: str) -> date | None:
    text = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", str(value or "").strip(), flags=re.IGNORECASE)
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def present_circumstances(request: ServiceRequest, categories: set[str]) -> list[str]:
    return [c.description for c in request.circumstances if c.status in {"present", "uncertain"} and c.category in categories]


def _has_category(request: ServiceRequest, category: str) -> bool:
    return any(c.category == category and c.status in {"present", "uncertain"} for c in request.circumstances)


def identity_verified(request: ServiceRequest) -> bool:
    record = find_mortgage(request.mortgage_number)
    return bool(record and is_borrower(record, request.borrower_name) and postal_matches(record, request.property_postal_code))


def _request_types(classification: RequestClassification) -> set[str]:
    return {classification.request_type, *classification.secondary_request_types}


def _document_record(key: str, request: ServiceRequest):
    records = [r for r in request.evidence_records if r.document_type == key]
    received = [r for r in records if r.status == "received" and r.evidence_ids]
    return received[-1] if received else records[-1] if records else None


def _document_provided(key: str, request: ServiceRequest) -> bool:
    record = _document_record(key, request)
    return bool(record and record.status == "received" and record.evidence_ids)


def prepare_request(request_value, received_evidence=()):
    """Only the server capture registry can mint received evidence, never an LLM."""

    request = _as_model(ServiceRequest, request_value)
    records = []
    for record in request.evidence_records:
        data = record.model_dump()
        data["evidence_ids"] = []
        if data["status"] == "received":
            data["status"] = "available"
        records.append(data)
    for evidence in received_evidence:
        for kind in evidence.get("document_types", []):
            if kind in DOCUMENTS:
                records.append({"document_type": kind, "status": "received", "evidence_ids": [evidence["id"]]})
    return request.model_copy(update={"evidence_records": []}).model_dump() | {"evidence_records": records}


def required_document_keys(request: ServiceRequest, classification: RequestClassification) -> list[tuple[str, str]]:
    """(document key, priority) pairs that apply to this request."""

    types = _request_types(classification)
    changes = request.requested_changes
    docs: list[tuple[str, str]] = []
    if changes.new_bank_account:
        docs.append(("void_cheque", "required"))
    if "payout_discharge" in types:
        docs.append(("payout_authorization", "required"))
        reason = changes.payout_reason.lower()
        if "sale" in reason or "sell" in reason:
            docs.append(("purchase_agreement", "required"))
        elif any(word in reason for word in ("switch", "refinanc", "lender", "transfer")):
            docs.append(("lender_direction", "required"))
    if "property_tax_insurance" in types:
        text = f"{request.request_summary} {request.raw_summary}".lower()
        wants_insurance = "insur" in text
        wants_tax = "tax" in text
        if wants_insurance or not wants_tax:
            docs.append(("insurance_declaration", "required" if wants_insurance else "conditional"))
        if wants_tax or not wants_insurance:
            docs.append(("property_tax_bill", "required" if wants_tax else "conditional"))
    if "hardship" in types or present_circumstances(request, HARDSHIP_CATEGORIES - {"legal_notice"}):
        docs.append(("income_change_proof", "recommended"))
    if _has_category(request, "legal_notice"):
        docs.append(("legal_notice_copy", "required"))
    seen: set[str] = set()
    return [(k, p) for k, p in docs if not (k in seen or seen.add(k))]


def _next_customer_message(route: str, missing: list[str], required_documents: list[str]) -> str:
    if route == "security_review":
        return (
            "For your security, a mortgage specialist needs to confirm a few details before we can make "
            "this change. Nothing has been changed on your account."
        )
    if route == "hardship_support":
        return (
            "Thank you for telling me. Our hardship team can go over options with you, such as a payment "
            "deferral or a longer amortization. Nothing is decided yet, and I have written down what you shared."
        )
    for field_name in missing:
        if field_name in BLOCKING_FIELD_QUESTIONS:
            return BLOCKING_FIELD_QUESTIONS[field_name]
    if missing:
        return f"Could you clarify one detail for me: {missing[0]}?"
    if route == "specialist_review":
        return (
            "This request needs a mortgage specialist to review it. I have written it up so you will not have "
            "to repeat yourself. Nothing has been changed on your account yet."
        )
    if required_documents:
        return f"Do you have this document handy now: {required_documents[0]}?"
    return (
        "I have everything a servicing representative needs to process this request. Nothing has been "
        "changed on your account until they confirm it with you."
    )


def validate_required_fields(request_value: Any, classification_value: Any = None) -> dict[str, Any]:
    """Validate minimum request facts before servicing rules run."""

    request = _as_model(ServiceRequest, request_value)
    classification = _as_model(RequestClassification, classification_value) if classification_value else None
    changes = request.requested_changes
    missing: list[str] = []
    warnings: list[str] = []

    for field_name in ("borrower_name", "mortgage_number", "property_postal_code", "contact_method", "request_summary"):
        if _blank(getattr(request, field_name)):
            missing.append(field_name)

    types = _request_types(classification) if classification else set()
    if "payment_change" in types:
        if not (changes.new_payment_amount_cad or changes.new_payment_frequency != "not specified"
                or not _blank(changes.new_payment_day) or changes.skip_payment or changes.new_bank_account):
            missing.append("payment_change_detail")
        elif _blank(changes.effective_date) and not changes.skip_payment:
            missing.append("effective_date")
    if "prepayment" in types and not changes.prepayment_amount_cad:
        missing.append("prepayment_amount_cad")
    if "payout_discharge" in types:
        if _blank(changes.payout_date):
            missing.append("payout_date")
        if _blank(changes.payout_reason):
            missing.append("payout_reason")

    for name in ("effective_date", "payout_date"):
        value = getattr(changes, name)
        if not _blank(value):
            parsed = _parse_date(value)
            if parsed is None:
                missing.append(f"Confirm a valid calendar date for the {name.replace('_', ' ')}")
            elif parsed < date.today():
                missing.append(f"Confirm the {name.replace('_', ' ')}; it is in the past")

    verified = identity_verified(request)
    if not verified and not any(f in missing for f in ("borrower_name", "mortgage_number", "property_postal_code")):
        warnings.append("Identity is not verified against the mortgage record.")

    missing.extend(request.missing_or_uncertain_facts)
    missing = _dedupe(missing)
    return FieldValidation(
        intake_status="missing_info" if missing else "valid",
        missing_fields=missing,
        warnings=_dedupe(warnings),
        identity_verified=verified,
    ).model_dump(exclude_none=True)


def apply_servicing_rules(
    request_value: Any,
    validation_value: Any,
    classification_value: Any,
    today: date | None = None,
) -> dict[str, Any]:
    """Apply deterministic servicing rules and first-pass routing."""

    today = today or date.today()
    request = _as_model(ServiceRequest, request_value)
    validation = _as_model(FieldValidation, validation_value)
    classification = _as_model(RequestClassification, classification_value)
    changes = request.requested_changes
    types = _request_types(classification)
    record = find_mortgage(request.mortgage_number)

    findings: list[RuleFinding] = []
    notes: list[str] = []
    required_docs: list[str] = []

    def add(rule_id: str, severity: str, message: str, action: str, document: str | None = None) -> None:
        findings.append(RuleFinding(rule_id=rule_id, severity=severity, message=message, required_action=action, document=document))
        if document:
            required_docs.append(document)

    if validation.missing_fields:
        add("INTAKE-001", "medium", "Required request details are missing.", "collect_info")

    identity_issues = mortgage_review(request) if not _blank(request.mortgage_number) else []
    for issue in identity_issues:
        action = "collect_info" if issue == "Mortgage number needs confirmation" else "security_review"
        add("ID-001", "high", issue, action)

    for key, priority in required_document_keys(request, classification):
        label = DOCUMENTS[key][0]
        if priority == "required" and not _document_provided(key, request):
            add("DOC-001", "medium", f"Missing document: {label}. {DOCUMENTS[key][1]}", "collect_document", label)

    # "other" is also the placeholder before the call has been classified.
    classified = classification.priority_rationale != UNCLASSIFIED and not _blank(request.request_summary)
    if not _blank(request.request_summary) and classification.priority_rationale == UNCLASSIFIED:
        add("TYPE-000", "medium", "Request not classified yet; type-specific details are unchecked.", "collect_info")
    for request_type in sorted(types & SPECIALIST_TYPES if classified else set()):
        add("TYPE-001", "medium", f"{request_type.replace('_', ' ').capitalize()} requests are handled by a mortgage specialist.", "specialist_review")
    if "hardship" in types:
        add("TYPE-002", "high", "Caller is asking for financial hardship help.", "hardship_referral")

    if record:
        years = record["remaining_amortization_months"] / 12
        monthly = level_payment(record["balance"], record["rate"], years, 12, record["compounding"])
        in_arrears = record["missed_payments_12m"] > 0

        if in_arrears:
            if types & {"payment_change", "hardship"} or changes.skip_payment:
                add("ARR-001", "high", f"Account has {record['missed_payments_12m']} missed payment(s) in the last 12 months.", "hardship_referral")
            else:
                notes.append(f"Account has {record['missed_payments_12m']} missed payment(s) in the last 12 months.")

        if changes.new_payment_amount_cad:
            frequency = changes.new_payment_frequency if changes.new_payment_frequency in FREQUENCY_LABELS else record["payment_frequency"]
            baseline = payment_for_frequency(monthly, frequency, record["balance"], record["rate"], years, record["compounding"])
            if frequency == record["payment_frequency"]:
                baseline = record["payment_amount"]
            ceiling = baseline * (1 + record["payment_increase_pct"])
            if changes.new_payment_amount_cad > ceiling + 0.005:
                add("PAY-001", "medium",
                    f"Requested payment {_money(changes.new_payment_amount_cad)} is above the {record['payment_increase_pct']:.0%} "
                    f"increase allowance (maximum {_money(ceiling)}).", "specialist_review")
            elif changes.new_payment_amount_cad < baseline - 0.005:
                add("PAY-002", "medium",
                    f"Requested payment {_money(changes.new_payment_amount_cad)} is below the required {_money(baseline)}; "
                    "lowering payments needs a specialist to review the amortization.", "specialist_review")
            else:
                notes.append(f"New payment {_money(changes.new_payment_amount_cad)} is within the payment-increase allowance.")

        if changes.new_payment_frequency not in {"not specified", record["payment_frequency"]}:
            notes.append(
                f"Frequency change from {FREQUENCY_LABELS[record['payment_frequency']].lower()} to "
                f"{FREQUENCY_LABELS[changes.new_payment_frequency].lower()} is allowed. An interest adjustment may "
                "apply on the changeover."
            )
        if not _blank(changes.new_payment_day):
            notes.append(f"Payment date change to {changes.new_payment_day}: a one-time interest adjustment may apply.")

        if changes.skip_payment and not in_arrears:
            if record["skip_payment_allowed"]:
                notes.append("Eligible to skip one payment. Interest for the skipped period is added to the balance.")
            else:
                add("SKIP-001", "medium", f"The {record['product']} product does not include a skip-a-payment option.", "specialist_review")

        if changes.prepayment_amount_cad:
            amount = changes.prepayment_amount_cad
            remaining = record["prepayment_remaining_this_year"]
            if amount >= record["balance"]:
                notes.append("Prepayment covers the full balance; treat as a payout and request a payout statement.")
            elif amount <= remaining + 0.005:
                notes.append(f"{_money(amount)} prepayment is within the remaining annual allowance of {_money(remaining)}. No prepayment charge.")
            else:
                excess = amount - remaining
                r = periodic_rate(record["rate"], 12, record["compounding"])
                three_months = excess * ((1 + r) ** 3 - 1)
                add("PRE-001", "medium",
                    f"{_money(amount)} prepayment exceeds the remaining allowance of {_money(remaining)} by {_money(excess)}.",
                    "specialist_review")
                notes.append(
                    f"Estimated charge on the excess is at least three months' interest (about {_money(three_months)})"
                    + ("; fixed-rate mortgages may be charged the higher interest rate differential." if record["compounding"] == "semi_annual" else ".")
                )

        maturity = datetime.strptime(record["maturity_date"], "%Y-%m-%d").date()
        payout = _parse_date(changes.payout_date)
        if "payout_discharge" in types and payout and payout < maturity:
            notes.append(
                f"Payout before the {record['maturity_date']} maturity: a prepayment charge applies to any amount "
                "above the annual allowance. The payout statement shows the exact figure."
            )
        days_to_maturity = (maturity - today).days
        if 0 <= days_to_maturity <= RENEWAL_WINDOW_DAYS:
            notes.append(f"Term matures {record['maturity_date']} ({days_to_maturity} days). Mention renewal options are coming.")

        if "property_tax_insurance" in types:
            notes.append(
                "Property tax is collected with mortgage payments."
                if record["property_tax_in_payment"]
                else "Property tax is not collected with payments; the borrower pays the municipality directly."
            )

    if any(f.required_action == "security_review" for f in findings):
        route = "security_review"
    elif any(f.required_action == "hardship_referral" for f in findings):
        route = "hardship_support"
    elif any(f.required_action == "specialist_review" for f in findings):
        route = "specialist_review"
    elif validation.missing_fields or any(f.required_action in {"collect_info", "collect_document"} for f in findings):
        route = "needs_documents"
    else:
        route = "ready_to_process"

    return ServicingDecision(
        routing_decision=route,
        servicing_notes=_dedupe(notes),
        required_documents=_dedupe(required_docs),
        findings=findings,
        audit_trail=[
            "Validated minimum request fields and identity details.",
            f"Classified request as {classification.request_type} with {classification.priority} priority.",
            "Applied prepayment, payment-change, payout, arrears, and maturity rules.",
            f"Initial route selected: {route}.",
        ],
    ).model_dump(exclude_none=True)


def generate_document_checklist(request_value: Any, classification_value: Any, decision_value: Any) -> dict[str, Any]:
    """Generate a caller-facing checklist from deterministic document rules."""

    request = _as_model(ServiceRequest, request_value)
    classification = _as_model(RequestClassification, classification_value)
    items: list[DocumentChecklistItem] = []
    for key, priority in required_document_keys(request, classification):
        label, reason = DOCUMENTS[key]
        record = _document_record(key, request)
        provided = _document_provided(key, request)
        items.append(DocumentChecklistItem(
            item=label,
            reason=reason,
            priority=priority,
            already_provided=provided,
            status=record.status if record else "unknown",
            evidence_ids=record.evidence_ids if provided else [],
        ))
    return DocumentChecklist(
        items=items,
        customer_tip=(
            "Hold documents up to the camera to add them. Cover full bank account numbers; "
            "the last four digits are enough."
        ),
    ).model_dump(exclude_none=True)


def security_and_hardship_gate(
    request_value: Any,
    validation_value: Any,
    classification_value: Any,
    decision_value: Any,
) -> dict[str, Any]:
    """Apply deterministic security and hardship gates. Security always wins."""

    request = _as_model(ServiceRequest, request_value)
    classification = _as_model(RequestClassification, classification_value)
    decision = _as_model(ServicingDecision, decision_value)
    changes = request.requested_changes
    types = _request_types(classification)
    record = find_mortgage(request.mortgage_number)
    moves_money = bool(changes.prepayment_amount_cad) or "payout_discharge" in types
    signals: list[GateSignal] = []

    def signal(signal_id: str, severity: str, message: str, security: bool = False, hardship: bool = False) -> None:
        signals.append(GateSignal(signal_id=signal_id, severity=severity, message=message, route_to_security=security, route_to_hardship=hardship))

    if request.caller_role == "other_third_party" or _has_category(request, "caller_not_borrower"):
        signal("SEC-001", "high", "Caller is not a borrower on the mortgage. Share general information only.", security=True)
    if request.caller_role == "authorized_third_party":
        signal("SEC-002", "high", "Caller says they act for the borrower. Verify power of attorney or authorization on file.", security=True)
    if changes.new_bank_account and moves_money:
        signal("SEC-003", "high", "Bank account change requested together with a prepayment or payout.", security=True)
    if record and record["contact_changed_days_ago"] <= CONTACT_CHANGE_WINDOW_DAYS and (changes.new_bank_account or moves_money):
        signal("SEC-004", "high",
               f"Contact details changed {record['contact_changed_days_ago']} days ago and the caller wants to move money or change the bank account.",
               security=True)
    for description in present_circumstances(request, SECURITY_CATEGORIES - {"caller_not_borrower"}):
        signal("SEC-005", "high", f"Possible fraud indicator: {description}", security=True)

    for description in present_circumstances(request, HARDSHIP_CATEGORIES - {"legal_notice"}):
        signal("HARD-001", "high", f"Hardship circumstance: {description}", hardship=True)
    if _has_category(request, "legal_notice"):
        signal("HARD-002", "urgent", "Caller received a legal notice (power of sale or foreclosure). Time-sensitive.", hardship=True)
    if decision.routing_decision == "hardship_support":
        signal("HARD-003", "high", "Servicing rules referred this request to the hardship team.", hardship=True)

    if any(s.route_to_security for s in signals) or decision.routing_decision == "security_review":
        final_route = "security_review"
    elif any(s.route_to_hardship for s in signals):
        final_route = "hardship_support"
    else:
        final_route = decision.routing_decision

    return SecurityHardshipGate(
        final_routing_decision=final_route,
        signals=signals,
        audit_trail=decision.audit_trail + [
            "Applied deterministic security and hardship gates.",
            f"Final route selected: {final_route}.",
        ],
    ).model_dump(exclude_none=True)


ROUTE_LABELS = {
    "ready_to_process": "Ready to process",
    "needs_documents": "Needs info or documents",
    "specialist_review": "Specialist review",
    "hardship_support": "Hardship support",
    "security_review": "Security review",
}


def build_service_request_packet(
    request_value: Any,
    validation_value: Any,
    classification_value: Any,
    decision_value: Any,
    checklist_value: Any,
    gate_value: Any,
) -> dict[str, Any]:
    """Build the Markdown packet handed to a servicing specialist."""

    request = _as_model(ServiceRequest, request_value)
    validation = _as_model(FieldValidation, validation_value)
    classification = _as_model(RequestClassification, classification_value)
    decision = _as_model(ServicingDecision, decision_value)
    checklist = _as_model(DocumentChecklist, checklist_value)
    gate = _as_model(SecurityHardshipGate, gate_value)

    missing = _dedupe(validation.missing_fields)
    route = gate.final_routing_decision
    priority = "urgent" if any(s.severity == "urgent" for s in gate.signals) else classification.priority
    changes = request.requested_changes

    change_lines = []
    if changes.new_payment_amount_cad:
        change_lines.append(f"- New payment amount: {_money(changes.new_payment_amount_cad)}")
    if changes.new_payment_frequency != "not specified":
        change_lines.append(f"- New frequency: {FREQUENCY_LABELS.get(changes.new_payment_frequency, changes.new_payment_frequency)}")
    if not _blank(changes.new_payment_day):
        change_lines.append(f"- New payment date: {changes.new_payment_day}")
    if changes.skip_payment:
        change_lines.append("- Skip one payment")
    if changes.prepayment_amount_cad:
        change_lines.append(f"- Lump-sum prepayment: {_money(changes.prepayment_amount_cad)}")
    if not _blank(changes.payout_date) or not _blank(changes.payout_reason):
        change_lines.append(f"- Payout by {changes.payout_date} ({changes.payout_reason})")
    if changes.new_bank_account:
        change_lines.append("- Change the bank account for payments")
    if not _blank(changes.effective_date):
        change_lines.append(f"- Effective: {changes.effective_date}")

    def bullets(items: list[str], fallback: str) -> str:
        return "\n".join(f"- {item}" for item in items) if items else f"- {fallback}"

    checklist_lines = [f"- [{item.status}] **{item.item}** ({item.priority}) - {item.reason}" for item in checklist.items]
    finding_lines = [f"- `{f.rule_id}` [{f.severity}] {f.message}" for f in decision.findings]
    signal_lines = [f"- `{s.signal_id}` [{s.severity}] {s.message}" for s in gate.signals]
    audit_lines = [f"{i}. {entry}" for i, entry in enumerate(gate.audit_trail, start=1)]

    handoff = (
        f"{request.borrower_name if not _blank(request.borrower_name) else 'Unidentified caller'} "
        f"({'identity verified' if validation.identity_verified else 'identity NOT verified'}) on mortgage "
        f"{request.mortgage_number if not _blank(request.mortgage_number) else 'not provided'} is asking about "
        f"{classification.request_type.replace('_', ' ')}. Summary: {(request.raw_summary or request.request_summary).rstrip('.')}."
    )
    next_message = _next_customer_message(route, missing, decision.required_documents)

    markdown = f"""# Mortgage Service Request

**Request type:** {classification.request_type.replace("_", " ").capitalize()}
**Identity:** {"Verified" if validation.identity_verified else "Not verified"}
**Intake status:** {validation.intake_status.replace("_", " ").capitalize()}
**Priority:** {priority.capitalize()}
**Routing decision:** {ROUTE_LABELS[route]}

## Requested Changes
{chr(10).join(change_lines) or "- None captured yet."}

## Missing Information
{bullets(missing, "No required details are missing.")}

## Documents
{chr(10).join(checklist_lines) or "- No documents required by current rules."}

## Servicing Notes
{bullets(decision.servicing_notes, "No servicing notes.")}

This packet records a request only. It does not change the mortgage, approve anything, or give financial advice. A servicing representative must confirm every change with the borrower.

## Specialist Handoff Summary
{handoff}

## Next Message to the Caller
{next_message}

## Rule Findings
{chr(10).join(finding_lines) or "- No rule findings."}

## Security and Hardship Signals
{chr(10).join(signal_lines) or "- No security or hardship signal was triggered."}

## Audit Trail
{chr(10).join(audit_lines)}
"""
    return ServiceRequestPacket(
        request_type=classification.request_type,
        intake_status=validation.intake_status,
        priority=priority,
        routing_decision=route,
        missing_information=missing,
        required_documents=checklist.items,
        servicing_notes=decision.servicing_notes,
        specialist_handoff_summary=handoff,
        customer_next_message=next_message,
        audit_trail=gate.audit_trail,
        markdown=markdown,
    ).model_dump(exclude_none=True)
