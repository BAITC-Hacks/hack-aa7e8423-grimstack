"""Синтетический Dataset для приёмочных тестов: 33 месяца 2024-01…2026-09, as_of = 22.09.2026."""

from datetime import date, timedelta

import pandas as pd

from app.ingest.common import doc_hash
from app.ingest.dataset import Dataset

AS_OF = date(2026, 9, 22)
MONTHS = pd.period_range("2024-01", "2026-09", freq="M")
TX_DAYS = (3, 7, 11, 15, 19)  # все ≤ 21, чтобы попадать и в неполный сентябрь


def noisy(level: float, n: int = len(MONTHS), amp: float = 0.15) -> list[float]:
    wave = (0, 1, -1, 0.5, -0.5)  # детерминированный шум без тренда
    return [level * (1 + amp * wave[i % len(wave)]) for i in range(n)]


def seasonal(level: float, pattern12: list[float], n: int = len(MONTHS)) -> list[float]:
    return [level * pattern12[i % 12] for i in range(n)]


def make_dataset(series: dict[str, list[float]], *, supplier: str = "IEK", stock: dict[str, list[float]] | None = None,
                 stock_now: float = 50.0, in_transit: float = 0.0, moq: float = 1.0, category: str = "B",
                 unit_cost: float | None = None, tx_lines: int = len(TX_DAYS)) -> Dataset:
    skus = list(series)
    idx = pd.MultiIndex.from_tuples([(supplier, s) for s in skus], names=["supplier", "sku"])
    sales = pd.DataFrame([[float(v) for v in series[s]] for s in skus], index=idx, columns=MONTHS)
    stock = stock or {s: [10 * max(series[s])] * len(MONTHS) for s in skus}
    stock_monthly = pd.DataFrame([[float(v) for v in stock[s]] for s in skus], index=idx, columns=MONTHS)

    tx = [{"supplier": supplier, "sku": s, "date": pd.Timestamp(p.year, p.month, day),
           "doc": doc_hash(f"{s}-{p}-{k}"), "qty": sales.loc[(supplier, s), p] / tx_lines}
          for s in skus for p in MONTHS if p >= pd.Period("2025-01", "M") and sales.loc[(supplier, s), p] > 0
          for k, day in enumerate(TX_DAYS[:tx_lines])]

    return Dataset(
        as_of=AS_OF,
        skus=pd.DataFrame({"article": None, "name": [f"Товар {s}" for s in skus], "unit": "шт",
                           "category": category, "group4": None, "moq": float(moq),
                           "unit_cost": float("nan") if unit_cost is None else float(unit_cost),
                           "discontinued": False, "keep_1m": False, "new_item": False, "product_group": None},
                          index=idx),
        sales_monthly=sales,
        stock_monthly=stock_monthly,
        sales_tx=pd.DataFrame(tx, columns=["supplier", "sku", "date", "doc", "qty"]),
        stock_now=pd.DataFrame({"qty": float(stock_now), "source": "warehouses"}, index=idx),
        in_transit=pd.DataFrame([{"supplier": supplier, "sku": s, "qty": float(in_transit),
                                  "eta": pd.Timestamp(AS_OF + timedelta(days=10))}
                                 for s in skus if in_transit > 0], columns=["supplier", "sku", "qty", "eta"]),
    )


def add_oneoff(ds: Dataset, sku: str, day: date, qty: float) -> None:
    """Разовая крупная строка: попадает и в накладные, и в помесячные продажи (как в выгрузках 1С)."""
    supplier = ds.skus.xs(sku, level="sku").index[0]
    row = {"supplier": supplier, "sku": sku, "date": pd.Timestamp(day), "doc": doc_hash(f"oneoff-{sku}-{day}"), "qty": float(qty)}
    ds.sales_tx = pd.concat([ds.sales_tx, pd.DataFrame([row])], ignore_index=True)
    ds.sales_monthly.loc[(supplier, sku), pd.Period(day, "M")] += qty
