"""Политика пополнения: S, SS, кратность, срочность, overstock (docs/design.md §4 п.6–7)."""

from datetime import timedelta

import numpy as np
import pandas as pd


def effective_lr(L: pd.Series, R: pd.Series, keep_1m: pd.Series) -> pd.Series:
    """keep_1m ограничивает горизонт до 30 дней (целевое покрытие 1 месяц)."""
    lr = L + R
    return lr.where(~keep_1m, np.minimum(lr, 30.0))


def horizon_demand(base: pd.Series, season: pd.DataFrame, trend_s: pd.Series, growth_pct: float,
                    as_of, lr_days: pd.Series) -> pd.Series:
    """Сумма прогноза по дням (as_of, as_of+lr] помесячно пропорционально дням в месяце.

    Цикл идёт по дням горизонта (максимум ~70), не по SKU — каждая итерация векторизована.
    """
    total = pd.Series(0.0, index=base.index)
    max_h = int(lr_days.max()) if len(lr_days) else 0
    for d in range(1, max_h + 1):
        month = pd.Period(as_of + timedelta(days=d), "M")
        f = base * season[month.month] * trend_s * (1 + growth_pct / 100)
        active = (lr_days >= d).to_numpy()
        total = total + np.where(active, f / month.days_in_month, 0.0)
    return total


def safety_stock(sigma: pd.Series, z: pd.Series, lr_days: pd.Series) -> pd.Series:
    return z * sigma * np.sqrt(lr_days / 30.0)


def in_transit_eligible(in_transit: pd.DataFrame, as_of, lr_days: pd.Series) -> pd.Series:
    """Сумма in_transit.qty с eta ≤ as_of + lr, свой lr на строку.

    lr_days обычно принимает 2–3 различных значения (по поставщику/параметрам) —
    цикл по уникальным значениям, не по SKU.
    """
    result = pd.Series(0.0, index=lr_days.index)
    if in_transit.empty:
        return result
    tx = in_transit.copy()
    tx["key"] = list(zip(tx["supplier"], tx["sku"]))
    for lr in lr_days.unique():
        cutoff = pd.Timestamp(as_of + timedelta(days=int(lr)))
        keys = lr_days.index[lr_days == lr]
        sums = tx.loc[tx["eta"] <= cutoff].groupby("key")["qty"].sum()
        key_tuples = list(keys)
        result.loc[keys] = sums.reindex(key_tuples).fillna(0.0).to_numpy()
    return result


def apply(*, base: pd.Series, season: pd.DataFrame, trend_s: pd.Series, sigma_s: pd.Series,
          growth_pct: float, segment_s: pd.Series, median_line: pd.Series, do_not_order: pd.Series,
          L: pd.Series, R: pd.Series, z: pd.Series, keep_1m: pd.Series, stock_now: pd.Series,
          in_transit: pd.DataFrame, moq: pd.Series, as_of, forecast_monthly: pd.Series) -> dict:
    """Считает S, SS, net, qty, срочность, overstock. Возвращает словарь Series (design.md §4 п.6–7)."""
    lr = effective_lr(L, R, keep_1m)
    horizon = horizon_demand(base, season, trend_s, growth_pct, as_of, lr)
    ss = safety_stock(sigma_s, z, lr)
    s = horizon + ss
    floor_segments = segment_s.isin(["intermittent", "lumpy"])
    s = s.where(~floor_segments, np.maximum(s, median_line))

    transit_lr = in_transit_eligible(in_transit, as_of, lr)
    net = s - stock_now - transit_lr
    moq_safe = moq.replace(0, np.nan)
    qty_raw = np.ceil(net / moq_safe - 1e-9) * moq
    qty = pd.Series(np.where((net > 0) & ~do_not_order, qty_raw, 0.0), index=s.index).fillna(0.0)

    transit_l = in_transit_eligible(in_transit, as_of, L)
    daily_forecast = (forecast_monthly / 30.0).replace(0, np.nan)
    cover = ((stock_now + transit_l) / daily_forecast)

    urgency = pd.Series("planned", index=s.index)
    urgency[cover.notna() & (cover < (L + R))] = "high"
    urgency[cover.notna() & (cover < L)] = "critical"
    urgency[qty <= 0] = "none"

    overstock = (stock_now + transit_lr) > (s + 3 * forecast_monthly)

    return {"horizon_demand": horizon, "safety_stock": ss, "order_up_to": s, "net": net,
            "qty": qty, "urgency": urgency, "overstock": overstock, "days_of_cover": cover,
            "in_transit_lr": transit_lr, "in_transit_l": transit_l}
