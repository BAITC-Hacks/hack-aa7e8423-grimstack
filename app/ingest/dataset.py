"""Канонический набор данных, общий для всех поставщиков. Индекс SKU — (supplier, sku)."""

from dataclasses import dataclass
from datetime import date

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
    )
