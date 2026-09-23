"""Дополнительные модели API, не входящие в общий контракт ядра и фронта."""

from pydantic import BaseModel

from app.contracts import RunParams, Supplier, Urgency


class CompareRequest(BaseModel):
    base: RunParams
    scenario: RunParams


class CompareDelta(BaseModel):
    lines_to_order: int
    critical: int
    total_qty: float
    total_amount: float | None


class ChangedLine(BaseModel):
    line_id: str
    name: str
    supplier: Supplier
    base_qty: float
    scenario_qty: float
    base_urgency: Urgency
    scenario_urgency: Urgency


class CompareResult(BaseModel):
    base_run_id: str
    scenario_run_id: str
    delta: CompareDelta
    changed: list[ChangedLine]
