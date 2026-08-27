import uuid
from decimal import Decimal

from pydantic import BaseModel


class RiskAssessment(BaseModel):
    payment_id: uuid.UUID
    customer_id: uuid.UUID
    external_reference: str
    amount: Decimal
    currency: str
    failure_reason: str | None
    consecutive_failures: int
    historical_success_rate: float
    risk_score: float
    risk_level: str


class RiskLevelBreakdown(BaseModel):
    low: int
    medium: int
    high: int


class RiskSummary(BaseModel):
    at_risk_payment_count: int
    revenue_at_risk: Decimal
    currency_breakdown: dict[str, Decimal]
    risk_level_breakdown: RiskLevelBreakdown
