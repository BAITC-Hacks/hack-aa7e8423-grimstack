"""Историческая проверка прогноза: закрытые месяцы до среза → следующий полный месяц."""

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from app import ingest
from app.engine import pipeline

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "backtest" / "report.json"
CUTOFFS = (date(2026, 4, 1), date(2026, 5, 1), date(2026, 6, 1), date(2026, 7, 1))


def snapshot(dataset, cutoff: date):
    """Будущие продажи и транзакции физически исключены до подготовки прогноза."""
    month = pd.Period(cutoff, "M")
    return replace(dataset, as_of=cutoff,
                   sales_monthly=dataset.sales_monthly.loc[:, dataset.sales_monthly.columns < month].copy(),
                   stock_monthly=dataset.stock_monthly.loc[:, dataset.stock_monthly.columns <= month].copy(),
                   sales_tx=dataset.sales_tx[dataset.sales_tx["date"] < pd.Timestamp(cutoff)].copy(),
                   in_transit=dataset.in_transit.iloc[0:0].copy())


def evaluate(dataset, cutoffs=CUTOFFS):
    actual_by_supplier = {supplier: [] for supplier in ("IEK", "SE")}
    analyzed_by_supplier = {supplier: [] for supplier in ("IEK", "SE")}
    baseline_by_supplier = {supplier: [] for supplier in ("IEK", "SE")}
    periods = []
    for cutoff in cutoffs:
        target = pd.Period(cutoff, "M")
        if target not in dataset.sales_monthly.columns:
            raise ValueError(f"Нет факта продаж за {target}")
        historical = snapshot(dataset, cutoff)
        prepared = pipeline._prepare(historical)
        actual = dataset.sales_monthly[target].clip(lower=0).reindex(prepared.idx).fillna(0)
        analyzed = (prepared.base * prepared.season[target.month] * prepared.trend).clip(lower=0)
        baseline = prepared.base_baseline.clip(lower=0)
        for supplier in ("IEK", "SE"):
            keys = prepared.idx.get_level_values("supplier") == supplier
            actual_by_supplier[supplier].extend(actual[keys].to_numpy(dtype=float).tolist())
            analyzed_by_supplier[supplier].extend(analyzed[keys].to_numpy(dtype=float).tolist())
            baseline_by_supplier[supplier].extend(baseline[keys].to_numpy(dtype=float).tolist())
        periods.append(str(target))

    report = {"metric": "WAPE monthly quantity", "periods": periods,
              "method": "Для каждого месяца используются только продажи и транзакции до его начала; baseline — среднее сырых последних 12 месяцев. Оценивается прогноз спроса, не итоговый заказ.",
              "results": {}}
    for supplier in ("IEK", "SE"):
        actual = np.array(actual_by_supplier[supplier])
        denominator = float(actual.sum())
        report["results"][supplier] = {
            "analyze_wape": round(float(np.abs(actual - analyzed_by_supplier[supplier]).sum() / denominator), 4),
            "baseline_wape": round(float(np.abs(actual - baseline_by_supplier[supplier]).sum() / denominator), 4),
            "observations": len(actual),
        }
    return report


if __name__ == "__main__":
    result = evaluate(ingest.load_default())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
