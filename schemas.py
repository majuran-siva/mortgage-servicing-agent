"""Structured data contracts for the mortgage servicing request workflow."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


RequestType = Literal[
    "payment_change",
    "prepayment",
    "payout_discharge",
    "property_tax_insurance",
    "account_information",
    "hardship",
    "rate_term_change",
    "life_event",
    "other",
]

Priority = Literal["low", "medium", "high", "urgent"]
IntakeStatus = Literal["valid", "missing_info"]
RoutingDecision = Literal[
    "ready_to_process",
    "needs_documents",
    "specialist_review",
    "hardship_support",
    "security_review",
]
PaymentFrequency = Literal[
    "monthly",
    "semi_monthly",
    "bi_weekly",
    "accelerated_bi_weekly",
    "weekly",
    "accelerated_weekly",
    "not specified",
]
CallerRole = Literal["borrower", "authorized_third_party", "other_third_party", "unknown"]
DocumentStatus = Literal["unknown", "missing", "planned", "available", "received"]


class EvidenceRecord(BaseModel):
    document_type: str = Field(description="Canonical document key from the extraction instructions.")
    status: DocumentStatus = "unknown"
    source_turn_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class CircumstanceFact(BaseModel):
    """A hardship, vulnerability, or security circumstance the caller raised."""

    category: str = Field(
        description=(
            "Hardship: job_loss, income_reduction, illness, bereavement, separation, arrears, legal_notice, distress. "
            "Security: third_party_pressure, suspicious_message, urgent_payment_redirect, caller_not_borrower."
        )
    )
    status: Literal["present", "absent", "uncertain"]
    description: str
    source_turn_ids: list[str] = Field(default_factory=list)


class FactSource(BaseModel):
    field: str
    source_turn_ids: list[str] = Field(default_factory=list)


class RequestedChanges(BaseModel):
    """What the caller wants changed. Unset values mean the caller did not ask for that change."""

    new_payment_amount_cad: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    new_payment_frequency: PaymentFrequency = "not specified"
    new_payment_day: str = Field(default="not specified", description="Requested payment day or date, e.g. '15th of the month' or 'Fridays'.")
    skip_payment: bool = False
    prepayment_amount_cad: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    payout_date: str = Field(default="not specified", description="Requested payout or closing date as YYYY-MM-DD.")
    payout_reason: str = Field(default="not specified", description="sale, switching lenders, paying off, refinancing, or not specified.")
    new_bank_account: bool = Field(default=False, description="True only if the caller wants payments drawn from a different bank account.")
    effective_date: str = Field(default="not specified", description="When the change should take effect, YYYY-MM-DD.")

    # Gemini's response schema has no exclusiveMinimum, so a zero amount means "not asked for".
    @field_validator("new_payment_amount_cad", "prepayment_amount_cad")
    @classmethod
    def _zero_is_unset(cls, value: Optional[float]) -> Optional[float]:
        return None if value == 0 else value


class ServiceRequest(BaseModel):
    """Normalized facts extracted from a mortgage servicing call."""

    borrower_name: str = Field(description="Caller's full name as they say it appears on the mortgage.")
    mortgage_number: str = Field(description="Mortgage account number if supplied.")
    property_postal_code: str = Field(description="Postal code of the mortgaged property, used for verification.")
    caller_role: CallerRole = "unknown"
    contact_method: str = Field(description="Best phone number or email for follow-up.")
    request_summary: str = Field(description="Plain-language description of what the caller wants.")
    requested_changes: RequestedChanges = Field(default_factory=RequestedChanges)
    circumstances: list[CircumstanceFact] = Field(default_factory=list)
    documents_mentioned: list[str] = Field(default_factory=list)
    evidence_records: list[EvidenceRecord] = Field(default_factory=list)
    missing_or_uncertain_facts: list[str] = Field(default_factory=list)
    raw_summary: str = Field(description="Short factual summary of the call so far.")
    assumptions: list[str] = Field(default_factory=list)
    fact_sources: list[FactSource] = Field(default_factory=list)


class FieldValidation(BaseModel):
    """Deterministic validation of minimum request information."""

    intake_status: IntakeStatus
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    identity_verified: bool = False


class RequestClassification(BaseModel):
    """LLM classification of request type and operational priority."""

    request_type: RequestType
    secondary_request_types: list[RequestType] = Field(default_factory=list)
    priority: Priority
    priority_rationale: str
    customer_needs: list[str] = Field(default_factory=list)


class RuleFinding(BaseModel):
    """Deterministic finding generated by servicing rules."""

    rule_id: str
    severity: Priority
    message: str
    required_action: Literal[
        "collect_info",
        "collect_document",
        "specialist_review",
        "hardship_referral",
        "security_review",
        "note",
    ]
    document: Optional[str] = None


class ServicingDecision(BaseModel):
    """Deterministic routing output after servicing rules."""

    routing_decision: RoutingDecision
    servicing_notes: list[str] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)
    findings: list[RuleFinding] = Field(default_factory=list)
    audit_trail: list[str] = Field(default_factory=list)


class DocumentChecklistItem(BaseModel):
    item: str
    reason: str
    priority: Literal["required", "recommended", "conditional"]
    already_provided: bool = False
    status: DocumentStatus = "unknown"
    evidence_ids: list[str] = Field(default_factory=list)


class DocumentChecklist(BaseModel):
    items: list[DocumentChecklistItem] = Field(default_factory=list)
    customer_tip: str


class GateSignal(BaseModel):
    """Deterministic security or hardship signal."""

    signal_id: str
    severity: Priority
    message: str
    route_to_security: bool = False
    route_to_hardship: bool = False


class SecurityHardshipGate(BaseModel):
    """Final deterministic security and hardship routing gate."""

    final_routing_decision: RoutingDecision
    signals: list[GateSignal] = Field(default_factory=list)
    audit_trail: list[str] = Field(default_factory=list)


class ServiceRequestPacket(BaseModel):
    """Final packet handed to a servicing specialist."""

    request_type: RequestType
    intake_status: IntakeStatus
    priority: Priority
    routing_decision: RoutingDecision
    missing_information: list[str] = Field(default_factory=list)
    required_documents: list[DocumentChecklistItem] = Field(default_factory=list)
    servicing_notes: list[str] = Field(default_factory=list)
    specialist_handoff_summary: str
    customer_next_message: str
    audit_trail: list[str] = Field(default_factory=list)
    markdown: str


class RequestAnalysis(BaseModel):
    """Single model output: extracted request plus its classification."""

    request: ServiceRequest
    classification: RequestClassification
