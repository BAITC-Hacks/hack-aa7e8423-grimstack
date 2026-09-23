"""Контракт между ядром (ingest/engine/ai), API и фронтом.

Заморожен: меняется только по договорённости всех троих (см. docs/design.md, §6).
Примеры ответов — contracts/*.json, их валидирует tests/test_contracts.py.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Supplier = Literal["IEK", "SE"]
Urgency = Literal["critical", "high", "planned", "none"]
FileRole = Literal["monthly_sales", "monthly_stock", "sales_tx", "in_transit", "moq", "seasonality"]


class RunParams(BaseModel):
    dataset_id: str | None = None  # None — встроенные выгрузки data/raw; иначе id из POST /api/datasets
    supplier: Supplier | None = None  # None — все поставщики
    category: str | None = None
    method: Literal["analyze", "baseline"] = "analyze"
    lead_time_days: int | None = Field(None, ge=1, le=365)  # None — дефолт поставщика
    review_period_days: int | None = Field(None, ge=1, le=365)  # None — дефолт поставщика
    service_level: float | None = Field(None, gt=0.5, lt=1)  # None — по категории
    growth_pct: float = Field(0.0, ge=-90, le=500)


class Component(BaseModel):
    """Шаг обоснования.

    kind="qty"    — шаг «водопада»; все qty-компоненты строки в сумме дают recommended_qty
                    (horizon_demand + safety_stock + stock(−) + in_transit(−) + moq_rounding).
    kind="factor" — множитель прогноза (seasonality, trend, growth).
    kind="info"   — справочное количество, в сумму не входит (base, oneoff_excluded, stockout_restored).
    """

    key: str  # base | seasonality | trend | growth | oneoff_excluded | stockout_restored
    #           | horizon_demand | safety_stock | stock | in_transit | moq_rounding
    label: str
    value: float
    kind: Literal["qty", "factor", "info"]
    note: str | None = None


class OrderLine(BaseModel):
    line_id: str  # "IEK:200400085_"
    supplier: Supplier
    sku: str  # код 1С
    article: str | None
    name: str
    unit: str
    category: str | None  # «Кат. 1» / ABC-класс
    product_group: str | None  # группа Laya
    stock_free: float
    stock_source: Literal["warehouses", "estimate_lower_bound"]
    in_transit: float
    forecast_monthly: float  # прогноз на ближайший полный месяц
    safety_stock: float
    order_up_to: float
    moq: float
    recommended_qty: float
    final_qty: float  # правится менеджером, изначально = recommended_qty
    baseline_qty: float | None
    urgency: Urgency
    days_of_cover: float | None
    unit_cost: float | None
    amount: float | None  # final_qty × unit_cost
    explanation: str
    components: list[Component]
    flags: list[str]  # oneoff_excluded | project_order | stockout_restored | seasonal | trend_up
    #                   | trend_down | intermittent | overstock | approx_stock | discontinued
    #                   | keep_1m | new_item


class SupplierSummary(BaseModel):
    supplier: Supplier
    supplier_name: str
    lines_count: int
    critical_count: int
    total_qty: float
    total_amount: float | None
    status: Literal["draft", "approved"] = "draft"
    approved_at: datetime | None = None


class RunKpi(BaseModel):
    lines_to_order: int
    critical: int
    total_amount: float | None
    oneoff_units_excluded: float
    stockout_units_restored: float
    overstock_lines: int


class RunResult(BaseModel):
    run_id: str
    created_at: datetime
    params: RunParams
    data_as_of: date
    kpi: RunKpi
    suppliers: list[SupplierSummary]
    lines: list[OrderLine]  # recommended_qty > 0 или urgency != "none"
    warnings: list[str]


class OneoffEvent(BaseModel):
    date: date
    doc: str  # хэш документа
    qty: float
    capped_to: float


class SkuHistory(BaseModel):
    supplier: Supplier
    sku: str
    name: str
    months: list[str]  # "2024-01" … "2026-08"
    raw: list[float]
    cleaned: list[float]  # после вычета разовых строк
    restored: list[float]  # после восстановления stockout
    stockout: list[Literal["full", "start", "end", "effective"] | None]
    forecast_months: list[str]
    forecast: list[float]
    oneoff_events: list[OneoffEvent]


# --- HTTP-обёртки (владелец API) ---


class SupplierInfo(BaseModel):
    supplier: Supplier
    supplier_name: str
    lead_time_days: int
    review_period_days: int
    categories: list[str]


class Meta(BaseModel):
    data_as_of: date
    suppliers: list[SupplierInfo]
    product_groups: list[str]
    llm_available: bool


class LinePatch(BaseModel):
    final_qty: float = Field(ge=0)


class DatasetUploaded(BaseModel):
    dataset_id: str
    supplier: Supplier
    warnings: list[str]


class SummaryResponse(BaseModel):
    text: str
    cached: bool


class ErrorBody(BaseModel):
    detail: str
    code: str
    meta: dict = {}


class IngestError(Exception):
    """Некорректная выгрузка. API отвечает на неё 422 с ErrorBody."""

    def __init__(self, code: str, message: str, file_role: FileRole | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.file_role = file_role
