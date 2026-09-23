"""Бэктест: наш метод (analyze) против Excel-метода (baseline) на реальной истории.

Метод, метрики и оговорки — docs/backtest.md. Идея вкратце: на каждую контрольную точку
as_of = 1-е число месяца M (M = 2026-03..2026-08) строим Dataset, урезанный до того, что
было известно НА эту дату (truncate_dataset), считаем прогноз через внутренности
app/engine/pipeline.py (_prepare/_scenario_analyze/_scenario_baseline — их вызов на чтение,
ядро не меняем) и сравниваем с фактом из ПОЛНОГО датасета.

Запуск: python -m scripts.backtest  ИЛИ  python scripts/backtest.py (см. sys.path ниже).
"""

import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # даёт работать и `python scripts/backtest.py`, и `-m scripts.backtest`
    sys.path.insert(0, str(ROOT))

from app import ingest  # noqa: E402
from app.contracts import RunParams  # noqa: E402
from app.engine import forecast, pipeline  # noqa: E402
from app.ingest.dataset import Dataset  # noqa: E402
from app.ingest.iek import _abc_category  # noqa: E402

CHECKPOINTS = [pd.Period(f"2026-{mm:02d}", "M") for mm in range(3, 9)]  # 2026-03 .. 2026-08
# Последний закрытый месяц в реальных данных: ds.as_of = 22.09.2026, сентябрь неполный.
LAST_CLOSED = pd.Period("2026-08", "M")
REPORT_PATH = ROOT / "data" / "backtest" / "report.json"

_PARAMS = RunParams()  # дефолтные L/R/SL по поставщику, growth_pct=0 — для обоих сценариев


# --- усечение датасета: главная защита от утечки будущего -----------------------------------


def truncate_dataset(ds_full: Dataset, m: pd.Period) -> Dataset:
    """Dataset, каким его видело бы ядро на 1-е число месяца m (as_of = m).

    dataclasses.replace сам по себе утечку не устраняет: нужно резать sales/stock/tx по датам
    и пересчитывать ABC-категорию IEK — иначе она посчитана по полной истории и подглядывает
    в будущее (docs/backtest.md, «Метод»).
    """
    as_of = m.start_time.date()

    cols_before = [c for c in ds_full.sales_monthly.columns if c < m]
    sales = ds_full.sales_monthly.reindex(columns=cols_before, fill_value=0.0).copy()
    sales[m] = 0.0  # текущий месяц, прошло 0 дней — так его видит ядро в свою дату (design.md §3)

    stock_cols = [c for c in ds_full.stock_monthly.columns if c <= m]
    stock = ds_full.stock_monthly.reindex(columns=stock_cols, fill_value=0.0).copy()

    cutoff = pd.Timestamp(m.start_time)
    tx = ds_full.sales_tx.loc[ds_full.sales_tx["date"] < cutoff].reset_index(drop=True)

    stock_now = pd.DataFrame({"qty": stock[m], "source": "warehouses"}, index=ds_full.skus.index)
    in_transit = pd.DataFrame(columns=["supplier", "sku", "qty", "eta"])

    skus = ds_full.skus.copy()
    iek_mask = skus.index.get_level_values("supplier") == "IEK"
    if iek_mask.any():
        window = cols_before[-12:]  # 12 закрытых месяцев до m, как app/ingest/iek.py::_ABC_MONTHS
        if window:
            volume = sales.loc[iek_mask, window].sum(axis=1)
            skus.loc[iek_mask, "category"] = _abc_category(volume)
        else:
            skus.loc[iek_mask, "category"] = "C"  # нет истории вообще — как в _abc_category без продаж

    return Dataset(as_of=as_of, skus=skus, sales_monthly=sales, stock_monthly=stock,
                    sales_tx=tx, stock_now=stock_now, in_transit=in_transit)


@dataclass
class CheckpointData:
    m: pd.Period
    ds: Dataset
    prepared: object  # pipeline.Prepared
    analyze: dict
    baseline: dict


def build_checkpoint(ds_full: Dataset, m: pd.Period) -> CheckpointData:
    ds = truncate_dataset(ds_full, m)
    prepared = pipeline._prepare(ds)
    analyze = pipeline._scenario_analyze(ds, prepared, _PARAMS)
    baseline = pipeline._scenario_baseline(ds, prepared, _PARAMS)
    return CheckpointData(m=m, ds=ds, prepared=prepared, analyze=analyze, baseline=baseline)


@dataclass
class FullContext:
    """Полный (неусечённый) датасет — источник факта и «сейчас» для замороженного капитала."""

    ds: Dataset
    prepared: object
    analyze: dict


def build_full_context(ds_full: Dataset) -> FullContext:
    prepared = pipeline._prepare(ds_full)
    analyze = pipeline._scenario_analyze(ds_full, prepared, _PARAMS)
    return FullContext(ds=ds_full, prepared=prepared, analyze=analyze)


# --- факт + все варианты прогноза в одну длинную таблицу (SKU × checkpoint × h) ----------------


def _stock_open(ds_full: Dataset, month: pd.Period, idx: pd.Index) -> pd.Series:
    if month in ds_full.stock_monthly.columns:
        return ds_full.stock_monthly[month].reindex(idx).fillna(0.0)
    return pd.Series(0.0, index=idx)


def _forecast_rows(ck: CheckpointData, full: FullContext) -> list[pd.DataFrame]:
    """Строки для WAPE/аблации: факт из полного датасета, прогноз analyze/baseline + шаги аблации."""
    idx = ck.prepared.idx
    include_sku = ~ck.prepared.do_not_order  # «метод бы рассматривал этот SKU» (design.md §4 п.6)
    trend_flat = pd.Series(1.0, index=idx)

    cleaned_mean12 = ck.prepared.cleaned[ck.prepared.last12_cols].mean(axis=1)
    restored_mean12 = ck.prepared.demand[ck.prepared.last12_cols].mean(axis=1)

    frames = []
    for h in (1, 2, 3):
        month = ck.m + (h - 1)
        if month > LAST_CLOSED:
            continue  # месяц ещё не закрыт в реальных данных — факта для него нет

        stock_open = _stock_open(full.ds, month, idx)
        not_stockout = stock_open > 0  # факт цензурирован при остатке ≤0 на начало месяца
        include = (include_sku & not_stockout).to_numpy()

        fc_analyze = forecast.forecast_value(ck.prepared.base, ck.prepared.season, ck.prepared.trend, 0.0, month)
        fc_base_season = forecast.forecast_value(ck.prepared.base, ck.prepared.season, trend_flat, 0.0, month)
        actual_raw = full.prepared.raw[month].reindex(idx) if month in full.prepared.raw.columns \
            else pd.Series(np.nan, index=idx)
        actual_cleaned = full.prepared.cleaned[month].reindex(idx) if month in full.prepared.cleaned.columns \
            else pd.Series(np.nan, index=idx)

        frame = pd.DataFrame({
            "supplier": idx.get_level_values("supplier"),
            "sku": idx.get_level_values("sku"),
            "checkpoint": str(ck.m),
            "h": h,
            "month": str(month),
            "segment": ck.prepared.segment.to_numpy(),
            "actual_raw": actual_raw.to_numpy(),
            "actual_cleaned": actual_cleaned.to_numpy(),
            "forecast_analyze": fc_analyze.to_numpy(),
            "forecast_baseline": ck.prepared.base_baseline.to_numpy(),
            "step_cleaned_mean12": cleaned_mean12.to_numpy(),
            "step_restored_mean12": restored_mean12.to_numpy(),
            "step_base_season": fc_base_season.to_numpy(),
        })
        frames.append(frame.loc[include])
    return frames


def build_long_table(checkpoints: list[CheckpointData], full: FullContext) -> pd.DataFrame:
    frames = [f for ck in checkpoints for f in _forecast_rows(ck, full)]
    return pd.concat(frames, ignore_index=True)


# --- метрика 1: WAPE по поставщику × методу × факту, срез по сегментам SBC ------------------


def _wape_cell(df: pd.DataFrame, actual_col: str, forecast_col: str) -> dict:
    a = df[actual_col].to_numpy(dtype=float)
    f = df[forecast_col].to_numpy(dtype=float)
    denom = float(a.sum())
    value = round(float(np.abs(a - f).sum() / denom), 4) if denom > 0 else None
    return {"wape": value, "n": int(len(df))}


_ACTUAL_KINDS = (("raw", "actual_raw"), ("cleaned", "actual_cleaned"))


def wape_block(long_df: pd.DataFrame, supplier: str) -> dict:
    g = long_df[long_df["supplier"] == supplier]
    out = {kind: {"analyze": _wape_cell(g, acol, "forecast_analyze"),
                  "baseline": _wape_cell(g, acol, "forecast_baseline")}
           for kind, acol in _ACTUAL_KINDS}
    by_segment = {}
    for seg, gs in g.groupby("segment"):
        by_segment[str(seg)] = {kind: {"analyze": _wape_cell(gs, acol, "forecast_analyze"),
                                        "baseline": _wape_cell(gs, acol, "forecast_baseline")}
                                 for kind, acol in _ACTUAL_KINDS}
    out["by_segment"] = by_segment
    return out


# --- метрика 4: аблация WAPE (факт cleaned, метод analyze) ----------------------------------


ABLATION_STEPS = [
    ("raw_mean_12", "forecast_baseline"),          # тот же столбец, что и baseline-прогноз (design.md §4)
    ("cleaned_mean_12", "step_cleaned_mean12"),
    ("cleaned_restored_mean_12", "step_restored_mean12"),
    ("base_season", "step_base_season"),
    ("base_season_trend", "forecast_analyze"),      # тот же столбец, что и analyze-прогноз
]

DEMO_RESTORE_SKU = ("IEK", "010300096_")  # УЗО: разовая строка 630 шт (05.2025) + stockout 06-07.2025


def ablation_block(long_df: pd.DataFrame, supplier: str) -> list[dict]:
    g = long_df[long_df["supplier"] == supplier]
    return [{"step": step, **_wape_cell(g, "actual_cleaned", col)} for step, col in ABLATION_STEPS]


def restore_effect_example(long_df: pd.DataFrame) -> dict:
    """Вклад восстановления stockout на конкретном SKU — на агрегате он тонет в шуме (design.md §5 MH3)."""
    supplier, sku = DEMO_RESTORE_SKU
    g = long_df[(long_df["supplier"] == supplier) & (long_df["sku"] == sku)]
    return {"sku": f"{supplier}:{sku}", "note": "разовая строка 630 шт 05.2025 + stockout 06-07.2025",
            "cleaned_mean_12": _wape_cell(g, "actual_cleaned", "step_cleaned_mean12"),
            "cleaned_restored_mean_12": _wape_cell(g, "actual_cleaned", "step_restored_mean12")}


# --- метрика 2: калибровка уровня сервиса -----------------------------------------------------


def _actual_horizon_demand(full: FullContext, idx: pd.Index, as_of_date, lr_days: pd.Series) -> pd.Series:
    """Фактический спрос за L+R дней от as_of_date, помесячно пропорционально дням (raw полного датасета).

    Если окно выходит за LAST_CLOSED — NaN: для этих дней факта ещё физически нет.
    """
    total = pd.Series(0.0, index=idx)
    invalid = pd.Series(False, index=idx)
    max_h = int(lr_days.max()) if len(lr_days) else 0
    for d in range(1, max_h + 1):
        month = pd.Period(as_of_date + timedelta(days=d), "M")
        active = (lr_days >= d).to_numpy()
        if month > LAST_CLOSED:
            invalid = invalid | pd.Series(active, index=idx)
            continue
        vals = (full.prepared.raw[month].reindex(idx).fillna(0.0) if month in full.prepared.raw.columns
                else pd.Series(0.0, index=idx))
        total = total + np.where(active, vals.to_numpy() / month.days_in_month, 0.0)
    return pd.Series(total, index=idx).where(~invalid)


def service_level_block(checkpoints: list[CheckpointData], full: FullContext, supplier: str) -> dict:
    analyze_hits = analyze_n = baseline_hits = baseline_n = n_window_unavailable = 0
    targets = []
    for ck in checkpoints:
        idx = ck.prepared.idx
        supplier_mask = idx.get_level_values("supplier") == supplier
        include = (~ck.prepared.do_not_order) & supplier_mask
        lr = ck.analyze["L"] + ck.analyze["R"]
        actual = _actual_horizon_demand(full, idx, ck.m.start_time.date(), lr)
        valid = include & actual.notna()
        n_window_unavailable += int((include & actual.isna()).sum())

        analyze_hits += int((ck.analyze["order_up_to"].where(valid) >= actual.where(valid)).sum())
        baseline_hits += int((ck.baseline["order_up_to"].where(valid) >= actual.where(valid)).sum())
        analyze_n += int(valid.sum())
        baseline_n += int(valid.sum())
        targets.append(ck.analyze["sl"].where(valid).dropna())

    target = round(float(pd.concat(targets).mean()), 4) if targets and sum(len(t) for t in targets) else None
    return {
        "target": target,
        "analyze": {"share": round(analyze_hits / analyze_n, 4) if analyze_n else None, "n": analyze_n},
        "baseline": {"share": round(baseline_hits / baseline_n, 4) if baseline_n else None, "n": baseline_n},
        "n_window_unavailable": n_window_unavailable,
    }


# --- метрика 3: замороженный капитал SE -------------------------------------------------------


def frozen_capital_block(checkpoints: list[CheckpointData]) -> list[dict]:
    """Σ «СС реал» × max(0, остаток на 1-е число M − S_analyze) — «заморожено сверх целевого уровня»."""
    out = []
    for ck in checkpoints:
        idx = ck.prepared.idx
        se_mask = idx.get_level_values("supplier") == "SE"
        unit_cost = ck.ds.skus["unit_cost"].reindex(idx)
        stock_m = ck.ds.stock_now["qty"].reindex(idx)
        excess = (stock_m - ck.analyze["order_up_to"]).clip(lower=0.0)
        frozen = (unit_cost * excess).where(se_mask & unit_cost.notna(), 0.0)
        out.append({"as_of": str(ck.m.start_time.date()), "amount": round(float(frozen.sum()), 2),
                     "skus": int((frozen > 0).sum())})
    return out


def frozen_capital_now(full: FullContext) -> dict:
    idx = full.prepared.idx
    se_mask = idx.get_level_values("supplier") == "SE"
    unit_cost = full.ds.skus["unit_cost"].reindex(idx)
    stock_now = full.ds.stock_now["qty"].reindex(idx)
    transit = full.ds.in_transit.groupby(["supplier", "sku"])["qty"].sum().reindex(idx).fillna(0.0)
    excess = (stock_now + transit - full.analyze["order_up_to"]).clip(lower=0.0)
    frozen = (unit_cost * excess).where(se_mask & unit_cost.notna(), 0.0)
    return {"as_of": str(full.ds.as_of), "amount": round(float(frozen.sum()), 2), "skus": int((frozen > 0).sum())}


# --- метрика 5: демо-ряды для слайдов ----------------------------------------------------------


DEMO_SKUS = [
    ("IEK", "130300792_", "сезонная труба"),
    ("SE", "030200201_", "рост"),
    ("IEK", "010300096_", "разовый заказ + stockout"),
]


def demo_series(checkpoints: list[CheckpointData], full: FullContext) -> list[dict]:
    out = []
    for supplier, sku, label in DEMO_SKUS:
        key = (supplier, sku)
        months = []
        for ck in checkpoints:
            m = ck.m
            if key not in ck.prepared.idx:
                continue
            fc_analyze = float(forecast.forecast_value(ck.prepared.base, ck.prepared.season, ck.prepared.trend,
                                                         0.0, m).loc[key])
            fc_baseline = float(ck.prepared.base_baseline.loc[key])
            actual_raw = float(full.prepared.raw.loc[key, m]) if m in full.prepared.raw.columns else None
            actual_cleaned = float(full.prepared.cleaned.loc[key, m]) if m in full.prepared.cleaned.columns else None
            months.append({"month": str(m), "actual_raw": actual_raw, "actual_cleaned": actual_cleaned,
                           "forecast_analyze_h1": round(fc_analyze, 1), "forecast_baseline": round(fc_baseline, 1)})
        out.append({"sku": f"{supplier}:{sku}", "label": label, "months": months})
    return out


# --- сборка отчёта ------------------------------------------------------------------------------


CAVEATS = [
    "in_transit на контрольных точках пуст (истории поставок нет) — S на бэктесте завышает нужду "
    "относительно того, что менеджер видел бы с реальным «в пути»; это ограничение данных, не ошибка метода.",
    "Справочник SE («Категория 2026», флаги !!! / Кат. 5 / keep_1m) — снимок текущего файла, не пересчитан "
    "по состоянию на дату M; возможная небольшая утечка в категории SE (не в цифрах спроса).",
    "Текущий остаток IEK в блоке «сейчас» (frozen_capital_now) — нижняя оценка (приходы сентября неизвестны); "
    "на контрольных точках 03-08.2026 остаток взят из stock_monthly и точен.",
    "SKU-месяцы с остатком на начало месяца ≤ 0 исключены из WAPE и калибровки сервиса — факт в них цензурирован "
    "дефицитом, а не отражает реальный спрос.",
    "Горизонт бэктеста — 6 точек: для h=3 выборка SKU-месяцев меньше, чем для h=1 (не все точки доходят "
    "до месяца ≤ 2026-08 на третьем шаге) — доверия к h=3 меньше.",
    "Калибровка уровня сервиса считается только там, где окно L+R не выходит за последний закрытый месяц "
    "(2026-08); для длинных окон SE (L+R до 70 дней) часть SKU×точек исключена — см. n_window_unavailable.",
]


def build_report(ds_full: Dataset) -> dict:
    full = build_full_context(ds_full)
    checkpoints = [build_checkpoint(ds_full, m) for m in CHECKPOINTS]
    long_df = build_long_table(checkpoints, full)

    suppliers = {}
    for supplier in ("IEK", "SE"):
        block = {
            "wape": wape_block(long_df, supplier),
            "service_level": service_level_block(checkpoints, full, supplier),
            "ablation": ablation_block(long_df, supplier),
        }
        if supplier == "IEK":
            block["ablation_restore_example"] = restore_effect_example(long_df)
        if supplier == "SE":
            block["frozen_capital"] = frozen_capital_block(checkpoints)
            block["frozen_capital_now"] = frozen_capital_now(full)
        suppliers[supplier] = block

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checkpoints": [str(m) for m in CHECKPOINTS],
        "suppliers": suppliers,
        "demo_skus": demo_series(checkpoints, full),
        "caveats": CAVEATS,
    }


def main() -> None:
    t0 = time.perf_counter()
    ds_full = ingest.load_default()
    t_load = time.perf_counter() - t0

    report = build_report(ds_full)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    total = time.perf_counter() - t0
    print(f"OK: {REPORT_PATH} записан. load_default={t_load:.1f}с, всего={total:.1f}с")


if __name__ == "__main__":
    main()
