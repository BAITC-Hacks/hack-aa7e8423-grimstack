"""Stockout: тип месяца и восстановление упущенного спроса (docs/design.md §4 п.3)."""

import numpy as np
import pandas as pd

WEEKMASK = "1111110"  # рабочие дни для доли «в наличии» — пн-сб, вс выходной


def restore(cleaned: pd.DataFrame, stock_monthly: pd.DataFrame, tx: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Типы stockout-месяцев и добавка спроса.

    cleaned — закрытые месяцы (index (supplier, sku), колонки Period). stock_monthly
    дополнительно содержит месяц после последнего закрытого (close = open следующего).
    Возвращает (added ≥ 0, types: None|'full'|'start'|'end'|'effective'), той же формы, что cleaned.
    """
    months = list(cleaned.columns)
    added = pd.DataFrame(0.0, index=cleaned.index, columns=months)
    types = _none_frame(cleaned.index, months)

    tx = tx.assign(month=tx["date"].dt.to_period("M"))
    daily = (tx.groupby(["supplier", "sku", "month", "date"], as_index=False)["qty"].sum()
             .sort_values(["supplier", "sku", "month", "date"]))
    daily["cum"] = daily.groupby(["supplier", "sku", "month"])["qty"].cumsum()
    first_sale = daily.groupby(["supplier", "sku", "month"])["date"].min()

    for i, m in enumerate(months):
        open_ = stock_monthly[m]
        close_ = stock_monthly[m + 1]
        prev = months[max(0, i - 6):i]

        active = cleaned[prev].gt(0).any(axis=1) if prev else pd.Series(False, index=cleaned.index)

        if prev:
            in_stock = types[prev].isna() & (stock_monthly[prev] > 0)  # «в наличии»: не stockout и open > 0
            base = cleaned[prev].where(in_stock).median(axis=1)
            base = base.where(in_stock.sum(axis=1) >= 3)  # нужно ≥3 таких месяца
        else:
            base = pd.Series(np.nan, index=cleaned.index)

        type_m = _none_series(cleaned.index)
        type_m[(open_ <= 0) & (close_ <= 0)] = "full"
        type_m[(open_ <= 0) & (close_ > 0)] = "start"
        type_m[(open_ > 0) & (close_ <= 0)] = "end"
        type_m[(open_ > 0) & (close_ > 0) & (open_ < 0.25 * base)] = "effective"
        type_m[~active] = None  # классифицируем только активные SKU-месяцы
        types[m] = type_m

        share = _in_stock_share(type_m, m, open_, daily, first_sale, cleaned.index)
        need = (base * (1 - share)).clip(lower=0, upper=2 * base)
        added[m] = need.where(type_m.notna() & base.notna(), 0.0).fillna(0.0)

    return added, types


def _none_series(index: pd.Index) -> pd.Series:
    """object-Series из None: pd.Series(None, dtype=object) молча превращает None в NaN."""
    return pd.Series(np.full(len(index), None, dtype=object), index=index)


def _none_frame(index: pd.Index, columns: list) -> pd.DataFrame:
    return pd.DataFrame({c: _none_series(index) for c in columns}, index=index)


def _in_stock_share(type_m: pd.Series, m: pd.Period, open_: pd.Series, daily: pd.DataFrame,
                     first_sale: pd.Series, index: pd.Index) -> pd.Series:
    """Доля рабочих дней в наличии за месяц m: 2025+ по транзакциям, 2024 — умолчания."""
    share = pd.Series(np.nan, index=index)
    share[type_m == "full"] = 0.0
    share[type_m == "effective"] = 0.5

    if m.year < 2025:
        share[type_m == "start"] = 0.5
        share[type_m == "end"] = 0.5
        return share

    month_start = m.start_time.date()
    next_month_start = (m + 1).start_time.date()
    total_wd = np.busday_count(month_start, next_month_start, weekmask=WEEKMASK)
    day_m = daily[daily["month"] == m]

    starts = type_m[type_m == "start"].index
    if len(starts):
        if m in first_sale.index.get_level_values("month"):
            fs = first_sale.xs(m, level="month")
            wd = fs.map(lambda d: np.busday_count(d.date(), next_month_start, weekmask=WEEKMASK) / total_wd)
        else:
            wd = pd.Series(dtype=float)
        share.loc[starts] = wd.reindex(starts).fillna(0.0).astype(float)  # нет продаж в месяце → 0

    ends = type_m[type_m == "end"].index
    if len(ends):
        merged = day_m.merge(open_.rename("open").reset_index(), on=["supplier", "sku"], how="left")
        reached = merged[merged["cum"] >= merged["open"]]
        end_date = reached.groupby(["supplier", "sku"])["date"].min()
        wd = end_date.map(lambda d: np.busday_count(month_start, (d + pd.Timedelta(days=1)).date(), weekmask=WEEKMASK) / total_wd)
        share.loc[ends] = wd.reindex(ends).fillna(1.0).astype(float)  # накопленные продажи не достигли open → весь месяц в наличии

    return share
