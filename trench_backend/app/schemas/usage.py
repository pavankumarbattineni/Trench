import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class UsageTotals(BaseModel):
    cost_usd: Decimal
    questions: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    # Calls whose model has no LiteLLM price (cost unknown, not zero).
    unpriced_calls: int
    # Calls whose token counts were estimated (provider reported none).
    estimated_calls: int


class DailyUsage(BaseModel):
    date: str
    platform_cost_usd: Decimal
    own_key_cost_usd: Decimal
    questions: int


class ModelUsage(BaseModel):
    provider: str
    model: str
    questions: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    priced: bool


class UsageSummaryResponse(BaseModel):
    days: int
    current: UsageTotals
    previous: UsageTotals
    cache_savings_usd: Decimal
    daily: list[DailyUsage]
    by_model: list[ModelUsage]


class UsageStep(BaseModel):
    step: str
    provider: str
    model: str
    key_owner: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    estimated: bool


class QueryUsage(BaseModel):
    assistant_message_id: uuid.UUID
    thread_id: uuid.UUID | None
    query_preview: str
    created_at: datetime
    provider: str
    model: str
    key_owner: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    fully_priced: bool
    estimated: bool
    steps: list[UsageStep]


class QueryUsagePage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[QueryUsage]
