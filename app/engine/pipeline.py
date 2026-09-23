"""Конвейер расчёта: prepare (не зависит от RunParams) → run/history/meta (docs/design.md §4, задача 5).

prepare(ds) кэшируется в модульном dict по id(ds) — пересчёт спроса/сезонности/тренда/сегмента
дорогой и не меняется между вызовами с разными RunParams на одном Dataset.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import NormalDist
from uuid import uuid4

import numpy as np
import pandas as pd

from app.contracts import (Meta, OneoffEvent, OrderLine, RunKpi, RunParams, RunResult,
                            SkuHistory, Supplier, SupplierInfo, SupplierSummary)
from app.engine import explain, forecast, policy
from app.engine.config import DEFAULT_SERVICE_LEVEL, DO_NOT_ORDER_CATEGORIES, SERVICE_LEVEL, SUPPLIERS

try:
    from app.engine.cleaning import detect_oneoffs as _detect_oneoffs_impl
    from app.engine.cleaning import monthly_excess as _monthly_excess_impl
except ImportError:  # cleaning.py пишет параллельный агент — до готовности нулевые поправки
    _detect_oneoffs_impl = None
    _monthly_excess_impl = None

try:
    from app.engine.stockout import restore as _restore_impl
except ImportError:  # stockout.py пишет параллельный агент
    _restore_impl = None


# --- prepare: всё, что не зависит от RunParams --------------------------------------------


@dataclass
class Prepared:
    as_of: object
    idx: pd.Index
    closed_cols: list
    last12_cols: list
    last18_cols: list
    raw: pd.DataFrame
    cleaned: pd.DataFrame
    added: pd.DataFrame
    types: pd.DataFrame
    demand: pd.DataFrame
    events: pd.DataFrame
    excess: pd.DataFrame
    segment: pd.Series
    season: pd.DataFrame
    trend: pd.Series
    base: pd.Series
    sigma: pd.Series
    base_baseline: pd.Series
    sigma_baseline: pd.Series
    median_line: pd.Series
    do_not_order: pd.Series
    seasonal_flag: pd.Series
    trend_up: pd.Series
    trend_down: pd.Series
    project_flag: pd.Series
    approx_stock: pd.Series
    oneoff_excess_total: pd.Series
    restored_12: pd.Series
    restored_18: pd.Series


_CACHE: dict[int, tuple] = {}
_CACHE_SIZE = 4


def _detect_oneoffs(tx, skus, sales_monthly, as_of) -> pd.DataFrame:
    if _detect_oneoffs_impl is None:
        return pd.DataFrame(columns=["supplier", "sku", "date", "doc", "month", "qty", "capped_to",
                                       "excess", "project"])
    return _detect_oneoffs_impl(tx, skus, sales_monthly, as_of)


def _monthly_excess(events: pd.DataFrame, like: pd.DataFrame) -> pd.DataFrame:
    if _monthly_excess_impl is None:
        return pd.DataFrame(0.0, index=like.index, columns=like.columns)
    return _monthly_excess_impl(events, like)


def _restore(cleaned: pd.DataFrame, stock_monthly: pd.DataFrame, tx: pd.DataFrame):
    if _restore_impl is None:
        return 0, None
    return _restore_impl(cleaned, stock_monthly, tx)


def _build(ds) -> Prepared:
    idx = ds.skus.index
    closed = forecast.closed_columns(ds.sales_monthly.columns, ds.as_of)
    last12 = closed[-12:]
    last18 = closed[-18:]
    raw = ds.sales_monthly.reindex(index=idx, columns=closed, fill_value=0.0).clip(lower=0.0)

    events = _detect_oneoffs(ds.sales_tx, ds.skus, ds.sales_monthly, ds.as_of)
    excess = _monthly_excess(events, raw)
    cleaned = (raw - excess).clip(lower=0.0)

    added, types = _restore(cleaned, ds.stock_monthly.reindex(index=idx), ds.sales_tx)
    if not isinstance(added, pd.DataFrame):
        added = pd.DataFrame(0.0, index=idx, columns=closed)
    else:
        added = added.reindex(index=idx, columns=closed, fill_value=0.0)
    if isinstance(types, pd.DataFrame):
        types = types.reindex(index=idx, columns=closed)
    else:
        types = pd.DataFrame(None, index=idx, columns=closed, dtype=object)

    demand = cleaned + added
    demand12 = demand[last12]

    segment_s = forecast.segment(demand12)
    season = forecast.seasonal_profile(demand, ds.skus)
    trend_s = forecast.trend(demand, ds.as_of, segment_s)
    base = forecast.base_level(demand12, season)
    # σ — по очищенному ряду (design.md §4 п.6): без восстановления stockout, иначе
    # заполненный провал одновременно поднимает среднее и гасит дисперсию, и SS проседает
    # ровно настолько, что съедает прирост potребности — MH3 (test_acceptance) перестаёт расти.
    sigma_s = forecast.sigma(cleaned[last12])

    raw12 = raw[last12]
    base_baseline = raw12.mean(axis=1)
    sigma_baseline = raw12.std(axis=1, ddof=1).fillna(0.0)

    tx = ds.sales_tx
    median_line = (tx.groupby(["supplier", "sku"])["qty"].median() if len(tx) else pd.Series(dtype=float))
    median_line = median_line.reindex(idx).fillna(1.0)

    do_not_order = (ds.skus["discontinued"].fillna(False).astype(bool)
                     | ds.skus["category"].isin(DO_NOT_ORDER_CATEGORIES)
                     | (ds.skus["moq"].fillna(0.0) == 0.0)
                     | (segment_s == "none"))

    if len(events):
        project_flag = events.groupby(["supplier", "sku"])["project"].any().reindex(idx).fillna(False)
    else:
        project_flag = pd.Series(False, index=idx)

    seasonal_flag = (season.max(axis=1) / season.min(axis=1).replace(0, np.nan)) >= 1.5
    approx_stock = ds.stock_now["source"].reindex(idx) == "estimate_lower_bound"

    # sum по excess/added — общие для kpi (окно 12 мес.) и флагов строки (окно 18 мес.,
    # design.md/задача 5 допускает более широкое окно для флага, чтобы не терять реальный stockout).
    oneoff_excess_total = excess.sum(axis=1)
    restored_12 = added[last12].sum(axis=1)
    restored_18 = added[last18].sum(axis=1)

    return Prepared(as_of=ds.as_of, idx=idx, closed_cols=closed, last12_cols=last12, last18_cols=last18, raw=raw,
                     cleaned=cleaned, added=added, types=types, demand=demand, events=events,
                     excess=excess, segment=segment_s, season=season, trend=trend_s, base=base,
                     sigma=sigma_s, base_baseline=base_baseline, sigma_baseline=sigma_baseline,
                     median_line=median_line, do_not_order=do_not_order,
                     seasonal_flag=seasonal_flag.fillna(False), trend_up=trend_s >= 1.05,
                     trend_down=trend_s <= 0.95, project_flag=project_flag, approx_stock=approx_stock,
                     oneoff_excess_total=oneoff_excess_total, restored_12=restored_12, restored_18=restored_18)


def _prepare(ds) -> Prepared:
    key = id(ds)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] is ds:
        return cached[1]
    prepared = _build(ds)
    if len(_CACHE) >= _CACHE_SIZE:  # загрузки через /api/datasets не должны копиться в памяти
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (ds, prepared)
    return prepared


# --- параметры, применяемые к prepared -----------------------------------------------------


def _lr_series(idx: pd.Index, params: RunParams) -> tuple[pd.Series, pd.Series]:
    supplier = pd.Series(idx.get_level_values("supplier"), index=idx)
    default_l = supplier.map(lambda s: float(SUPPLIERS[s]["lead_time_days"]))
    default_r = supplier.map(lambda s: float(SUPPLIERS[s]["review_period_days"]))
    L = default_l if params.lead_time_days is None else pd.Series(float(params.lead_time_days), index=idx)
    R = default_r if params.review_period_days is None else pd.Series(float(params.review_period_days), index=idx)
    return L, R


def _sl_series(skus: pd.DataFrame, params: RunParams) -> pd.Series:
    if params.service_level is not None:
        return pd.Series(float(params.service_level), index=skus.index)
    return skus["category"].map(SERVICE_LEVEL).fillna(DEFAULT_SERVICE_LEVEL)


def _z_series(sl: pd.Series) -> pd.Series:
    zmap = {v: NormalDist().inv_cdf(v) for v in sl.unique()}
    return sl.map(zmap)


def _scenario(ds, prepared: Prepared, params: RunParams, *, base, season, trend_s, sigma_s,
              growth_pct: float) -> dict:
    idx = prepared.idx
    L, R = _lr_series(idx, params)
    sl = _sl_series(ds.skus, params)
    z = _z_series(sl)
    keep_1m = ds.skus["keep_1m"].fillna(False).astype(bool)
    stock_now = ds.stock_now["qty"].reindex(idx).fillna(0.0)
    moq = ds.skus["moq"].fillna(0.0)
    target_month = pd.Period(ds.as_of, "M") + 1
    forecast_monthly = forecast.forecast_value(base, season, trend_s, growth_pct, target_month)

    result = policy.apply(base=base, season=season, trend_s=trend_s, sigma_s=sigma_s,
                           growth_pct=growth_pct, segment_s=prepared.segment, median_line=prepared.median_line,
                           do_not_order=prepared.do_not_order, L=L, R=R, z=z, keep_1m=keep_1m,
                           stock_now=stock_now, in_transit=ds.in_transit, moq=moq, as_of=ds.as_of,
                           forecast_monthly=forecast_monthly)
    result.update(L=L, R=R, sl=sl, stock_now=stock_now, moq=moq, forecast_monthly=forecast_monthly,
                   target_month=target_month, season=season, trend=trend_s)
    return result


def _scenario_analyze(ds, prepared: Prepared, params: RunParams) -> dict:
    return _scenario(ds, prepared, params, base=prepared.base, season=prepared.season,
                      trend_s=prepared.trend, sigma_s=prepared.sigma, growth_pct=params.growth_pct)


def _scenario_baseline(ds, prepared: Prepared, params: RunParams) -> dict:
    idx = prepared.idx
    season_flat = pd.DataFrame(1.0, index=idx, columns=forecast.SEASON_MONTHS)
    trend_flat = pd.Series(1.0, index=idx)
    return _scenario(ds, prepared, params, base=prepared.base_baseline, season=season_flat,
                      trend_s=trend_flat, sigma_s=prepared.sigma_baseline, growth_pct=0.0)


# --- сборка строк ----------------------------------------------------------------------------


def _oneoff_note(events: pd.DataFrame, supplier: str, sku: str) -> str | None:
    if events.empty:
        return None
    sub = events[(events["supplier"] == supplier) & (events["sku"] == sku)]
    if sub.empty:
        return None
    biggest = sub.loc[sub["qty"].idxmax()]
    date_s = pd.Timestamp(biggest["date"]).strftime("%d.%m.%Y")
    return f"{date_s}, документ {biggest['doc']}: {biggest['qty']:.0f} шт (обычно {biggest['capped_to']:.0f})"


_STOCKOUT_RU = {"full": "полный дефицит", "start": "дефицит в начале месяца",
                "end": "дефицит в конце месяца", "effective": "остаток ниже 25% базы"}


def _restored_note(added: pd.DataFrame, types: pd.DataFrame, key: tuple) -> str | None:
    row = added.loc[key]
    if row.empty or float(row.max()) <= 0:
        return None
    month = row.idxmax()
    t = types.loc[key, month] if key in types.index else None
    return f"{month} ({_STOCKOUT_RU.get(t, 'нехватка')}): +{row[month]:.0f}"


def _opt_str(v) -> str | None:
    """Строковое поле SKU может прийти как NaN (пропуск в выгрузке) вместо None."""
    return None if pd.isna(v) else str(v)


def _filter_mask(ds, idx: pd.Index, params: RunParams) -> pd.Series:
    mask = pd.Series(True, index=idx)
    if params.supplier is not None:
        mask &= pd.Series(idx.get_level_values("supplier"), index=idx) == params.supplier
    if params.category is not None:
        mask &= (ds.skus["category"] == params.category) | (ds.skus["product_group"] == params.category)
    return mask


def _order_lines(ds, prepared: Prepared, params: RunParams) -> tuple[list[OrderLine], pd.Series]:
    analyze_r = _scenario_analyze(ds, prepared, params)
    baseline_r = _scenario_baseline(ds, prepared, params)
    primary = analyze_r if params.method == "analyze" else baseline_r

    filter_mask = _filter_mask(ds, prepared.idx, params)
    selected = filter_mask & (primary["qty"] > 0)

    lines: list[OrderLine] = []
    for key in prepared.idx[selected.to_numpy()]:
        supplier, sku = key
        sku_row = ds.skus.loc[key]
        unit = sku_row["unit"] or "шт"
        article = _opt_str(sku_row["article"])
        category = _opt_str(sku_row["category"])
        product_group = _opt_str(sku_row["product_group"])
        mq = float(primary["moq"].loc[key])
        q = float(primary["qty"].loc[key])
        fm = float(primary["forecast_monthly"].loc[key])
        stock = float(primary["stock_now"].loc[key])
        transit = float(primary["in_transit_lr"].loc[key])
        ss = float(primary["safety_stock"].loc[key])
        s_target = float(primary["order_up_to"].loc[key])
        horizon = float(primary["horizon_demand"].loc[key])
        floor = max(0.0, s_target - horizon - ss)  # подъём S до типичной строки при редком спросе
        approx = bool(prepared.approx_stock.loc[key])
        unit_cost = sku_row["unit_cost"]
        unit_cost = None if pd.isna(unit_cost) else float(unit_cost)
        cover = primary["days_of_cover"].loc[key]
        cover = None if pd.isna(cover) else round(float(cover), 1)

        oe = float(prepared.oneoff_excess_total.loc[key])
        rs = float(prepared.restored_18.loc[key])

        flags = []
        if oe > 0:
            flags.append("oneoff_excluded")
        if bool(prepared.project_flag.loc[key]):
            flags.append("project_order")
        if rs > 0:
            flags.append("stockout_restored")
        if bool(prepared.seasonal_flag.loc[key]):
            flags.append("seasonal")
        if bool(prepared.trend_up.loc[key]):
            flags.append("trend_up")
        if bool(prepared.trend_down.loc[key]):
            flags.append("trend_down")
        if prepared.segment.loc[key] in ("intermittent", "lumpy"):
            flags.append("intermittent")
        if bool(primary["overstock"].loc[key]):
            flags.append("overstock")
        if approx:
            flags.append("approx_stock")
        if bool(sku_row["discontinued"]):
            flags.append("discontinued")
        if bool(sku_row["keep_1m"]):
            flags.append("keep_1m")
        if bool(sku_row["new_item"]):
            flags.append("new_item")

        if params.method == "analyze":
            month = primary["target_month"]
            season_val = float(prepared.season.loc[key, month.month])
            trend_val = float(prepared.trend.loc[key])
            comps = explain.components_analyze(
                base=float(prepared.base.loc[key]), oneoff_excess=oe,
                oneoff_note=_oneoff_note(prepared.events, supplier, sku) if oe > 0 else None,
                restored=rs, restored_note=_restored_note(prepared.added, prepared.types, key) if rs > 0 else None,
                season_val=season_val, month=month, trend_val=trend_val, growth_pct=params.growth_pct,
                category=category, sl=float(primary["sl"].loc[key]), horizon=horizon, ss=ss, floor=floor,
                stock=stock, transit=transit, moq=mq, qty=q, approx_stock=approx)
            explanation = explain.explanation_analyze(
                unit=unit, forecast_monthly=fm, qty=q, moq=mq, seasonal=bool(prepared.seasonal_flag.loc[key]),
                season_val=season_val, trend_up=bool(prepared.trend_up.loc[key]),
                trend_down=bool(prepared.trend_down.loc[key]), trend_val=trend_val,
                stockout_restored=rs > 0, oneoff_excluded=oe > 0, in_transit=transit)
            baseline_qty = float(baseline_r["qty"].loc[key])
        else:
            comps = explain.components_baseline(base=float(prepared.base_baseline.loc[key]), horizon=horizon,
                                                  ss=ss, floor=floor, stock=stock, transit=transit, moq=mq, qty=q,
                                                  sl=float(primary["sl"].loc[key]), approx_stock=approx)
            lr_val = float((primary["L"].loc[key] + primary["R"].loc[key]))
            explanation = explain.explanation_baseline(unit=unit, avg_monthly=fm, qty=q, moq=mq, lr_days=lr_val)
            baseline_qty = None

        lines.append(OrderLine(
            line_id=f"{supplier}:{sku}", supplier=supplier, sku=sku, article=article,
            name=sku_row["name"], unit=unit, category=category, product_group=product_group,
            stock_free=round(stock, 1), stock_source=ds.stock_now.loc[key, "source"], in_transit=round(transit, 1),
            forecast_monthly=round(fm, 1), safety_stock=round(ss, 1), order_up_to=round(s_target, 1), moq=mq,
            recommended_qty=q, final_qty=q, baseline_qty=baseline_qty, urgency=primary["urgency"].loc[key],
            days_of_cover=cover, unit_cost=unit_cost, amount=(round(q * unit_cost, 2) if unit_cost is not None else None),
            explanation=explanation, components=comps, flags=flags))

    return lines, filter_mask


_URGENCY_ORDER = {"critical": 0, "high": 1, "planned": 2, "none": 3}


def _sort_lines(lines: list[OrderLine]) -> list[OrderLine]:
    return sorted(lines, key=lambda l: (_URGENCY_ORDER[l.urgency],
                                          -(l.amount if l.amount is not None else l.final_qty)))


def _suppliers_summary(lines: list[OrderLine]) -> list[SupplierSummary]:
    by_supplier: dict[str, list[OrderLine]] = {}
    for l in lines:
        by_supplier.setdefault(l.supplier, []).append(l)
    out = []
    for supplier, rows in by_supplier.items():
        amounts = [r.amount for r in rows if r.amount is not None]
        out.append(SupplierSummary(
            supplier=supplier, supplier_name=SUPPLIERS[supplier]["name"], lines_count=len(rows),
            critical_count=sum(1 for r in rows if r.urgency == "critical"),
            total_qty=sum(r.final_qty for r in rows),
            total_amount=(round(sum(amounts), 2) if amounts else None)))
    return out


def _kpi(lines: list[OrderLine], prepared: Prepared) -> RunKpi:
    keys = [(l.supplier, l.sku) for l in lines]
    amounts = [l.amount for l in lines if l.amount is not None]
    return RunKpi(
        lines_to_order=len(lines), critical=sum(1 for l in lines if l.urgency == "critical"),
        total_amount=(round(sum(amounts), 2) if amounts else None),
        oneoff_units_excluded=round(float(prepared.oneoff_excess_total.reindex(keys).sum()), 1) if keys else 0.0,
        stockout_units_restored=round(float(prepared.restored_12.reindex(keys).sum()), 1) if keys else 0.0,
        overstock_lines=sum(1 for l in lines if "overstock" in l.flags))


def _warnings(lines: list[OrderLine], prepared: Prepared, filter_mask: pd.Series) -> list[str]:
    out = []
    if any("approx_stock" in l.flags for l in lines):
        out.append("IEK: текущий остаток — нижняя оценка (остаток на 01.09 минус продажи сентября)")
    skipped = int((prepared.do_not_order & filter_mask).sum())
    if skipped:
        out.append(f"Не заказываем {skipped} SKU: списаны с закупки, не подлежат заказу или нет истории продаж")
    return out


# --- публичное API -----------------------------------------------------------------------


def run(ds, params: RunParams) -> RunResult:
    prepared = _prepare(ds)
    lines, filter_mask = _order_lines(ds, prepared, params)
    lines = _sort_lines(lines)
    return RunResult(run_id=uuid4().hex[:12], created_at=datetime.now(), params=params, data_as_of=ds.as_of,
                      kpi=_kpi(lines, prepared), suppliers=_suppliers_summary(lines), lines=lines,
                      warnings=_warnings(lines, prepared, filter_mask))


def history(ds, supplier: Supplier, sku: str, params: RunParams) -> SkuHistory:
    prepared = _prepare(ds)
    key = (supplier, sku)
    if key not in prepared.idx:
        raise KeyError(f"SKU не найден: {supplier}:{sku}")

    months = [str(c) for c in prepared.closed_cols]
    raw = [float(v) for v in prepared.raw.loc[key]]
    cleaned = [float(v) for v in prepared.cleaned.loc[key]]
    restored = [float(v) for v in (prepared.cleaned.loc[key] + prepared.added.loc[key])]
    stockout = [prepared.types.loc[key, c] for c in prepared.closed_cols]

    target_month = pd.Period(ds.as_of, "M") + 1
    forecast_months = [str(target_month + i) for i in range(3)]
    base, season, trend_s = prepared.base.loc[key], prepared.season.loc[key], float(prepared.trend.loc[key])
    forecast_vals = [float(base) * float(season[(target_month + i).month]) * trend_s * (1 + params.growth_pct / 100)
                      for i in range(3)]

    events = prepared.events
    sub = events[(events["supplier"] == supplier) & (events["sku"] == sku)] if len(events) else events
    oneoff_events = [OneoffEvent(date=pd.Timestamp(r["date"]).date(), doc=str(r["doc"]), qty=float(r["qty"]),
                                   capped_to=float(r["capped_to"]))
                       for _, r in sub.sort_values("date").iterrows()] if len(sub) else []

    return SkuHistory(supplier=supplier, sku=sku, name=ds.skus.loc[key, "name"], months=months, raw=raw,
                       cleaned=cleaned, restored=restored, stockout=stockout, forecast_months=forecast_months,
                       forecast=forecast_vals, oneoff_events=oneoff_events)


def meta(ds) -> Meta:
    suppliers = []
    present = set(ds.skus.index.get_level_values("supplier"))
    for code, cfg in SUPPLIERS.items():
        if code in present:
            cats = ds.skus.xs(code, level="supplier")["category"]
            categories = sorted({c for c in cats.dropna().unique() if c})
        else:
            categories = []
        suppliers.append(SupplierInfo(supplier=code, supplier_name=cfg["name"],
                                        lead_time_days=cfg["lead_time_days"],
                                        review_period_days=cfg["review_period_days"], categories=categories))
    product_groups = sorted({g for g in ds.skus["product_group"].dropna().unique() if g})
    from app.ai import has_cache
    llm_available = bool(os.getenv("OPENAI_API_KEY") and os.getenv("MODEL")) or has_cache()
    return Meta(data_as_of=ds.as_of, suppliers=suppliers, product_groups=product_groups,
                llm_available=llm_available)
