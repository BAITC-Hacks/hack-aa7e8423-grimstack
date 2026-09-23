"""Канонический набор данных, общий для всех поставщиков. Индекс SKU — (supplier, sku)."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

SKU_COLUMNS = ["article", "name", "unit", "category", "group4", "moq", "unit_cost",
               "discontinued", "keep_1m", "new_item", "product_group"]


@dataclass
class Dataset:
    as_of: date
    # index (supplier, sku); колонки SKU_COLUMNS. moq: кратность, 0 — не заказывать; unit_cost: NaN, если нет
    skus: pd.DataFrame
    # index (supplier, sku); колонки pd.Period('2024-01','M') … месяц as_of (последний неполный); пропуски = 0
    sales_monthly: pd.DataFrame
    # те же индекс и колонки; остаток на 1-е число месяца, ≥ 0
    stock_monthly: pd.DataFrame
    # колонки: supplier, sku, date (datetime64), doc (хэш), qty (> 0) — строки расходных накладных
    sales_tx: pd.DataFrame
    # index (supplier, sku); колонки: qty (≥ 0), source ('warehouses' | 'estimate_lower_bound')
    stock_now: pd.DataFrame
    # колонки: supplier, sku, qty (> 0), eta (datetime64)
    in_transit: pd.DataFrame
    metadata: dict[str, Any] = field(default_factory=dict)


def rename_supplier(data: Dataset, supplier: str, *, name: str, template: str,
                    lead_time_days: int, review_period_days: int) -> Dataset:
    """Assign an uploaded template's entire SKU universe to one independent supplier."""
    for attribute in ("skus", "sales_monthly", "stock_monthly", "stock_now"):
        frame = getattr(data, attribute).copy()
        frame.index = pd.MultiIndex.from_arrays(
            [[supplier] * len(frame), frame.index.get_level_values("sku")],
            names=["supplier", "sku"],
        )
        setattr(data, attribute, frame)
    for attribute in ("sales_tx", "in_transit"):
        frame = getattr(data, attribute).copy()
        frame["supplier"] = supplier
        setattr(data, attribute, frame)
    data.metadata.setdefault("custom_suppliers", {})[supplier] = {
        "name": name,
        "template": template,
        "lead_time_days": lead_time_days,
        "review_period_days": review_period_days,
    }
    return data


def concat(parts: list[Dataset]) -> Dataset:
    def by_sku(attr):
        return pd.concat([getattr(p, attr) for p in parts])

    def rows(attr):
        return pd.concat([getattr(p, attr) for p in parts], ignore_index=True)

    return Dataset(
        as_of=max(p.as_of for p in parts),
        skus=by_sku("skus"),
        sales_monthly=by_sku("sales_monthly").fillna(0.0),
        stock_monthly=by_sku("stock_monthly").fillna(0.0),
        sales_tx=rows("sales_tx"),
        stock_now=by_sku("stock_now"),
        in_transit=rows("in_transit"),
        metadata={"custom_suppliers": {
            code: config
            for part in parts
            for code, config in part.metadata.get("custom_suppliers", {}).items()
        }},
    )
