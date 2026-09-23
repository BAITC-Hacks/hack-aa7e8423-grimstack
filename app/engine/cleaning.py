"""Очистка ряда продаж: паллетный поток SE и разовые крупные строки (docs/design.md §4 п.1-2)."""

from datetime import date

import numpy as np
import pandas as pd

MIN_ROWS, SE_MIN_ROWS = 5, 20  # минимум строк SKU для статистики разовых строк
MAX_B_MONTHS, SE_MAX_B_MONTHS = 3, 4  # больше месяцев с B-строками — это регулярный опт

_EVENT_COLUMNS = ["supplier", "sku", "date", "doc", "month", "qty", "capped_to", "excess", "project"]


def pallet_mask(tx: pd.DataFrame, skus: pd.DataFrame) -> pd.Series:
    """Строки паллетного потока SE: supplier=='SE', MOQ ≥ 50, qty ≥ MOQ, qty кратно MOQ.

    Регулярный канал, в помесячный файл не входит и в статистику разовых строк не берётся.
    """
    idx = pd.MultiIndex.from_arrays([tx["supplier"], tx["sku"]])
    moq = skus["moq"].reindex(idx).to_numpy()
    qty = tx["qty"].to_numpy()
    is_se = (tx["supplier"] == "SE").to_numpy()
    with np.errstate(invalid="ignore"):
        pallet = is_se & (moq >= 50) & (qty >= moq) & (qty % moq == 0)
    return pd.Series(pallet, index=tx.index)


def detect_oneoffs(tx: pd.DataFrame, skus: pd.DataFrame, sales_monthly: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Отмечает разовые крупные строки по правилу B/U (design.md §4 п.2).

    Статистика — по SKU, только строки без паллетного потока. Отмеченная строка
    заменяется типичной (capped_to = медиана строк SKU), excess = qty − capped_to.
    """
    rows = tx.loc[~pallet_mask(tx, skus)].copy()
    if rows.empty:
        return pd.DataFrame(columns=_EVENT_COLUMNS)
    rows["month"] = rows["date"].dt.to_period("M")

    by_sku = rows.groupby(["supplier", "sku"])["qty"]
    stats = pd.DataFrame({"median": by_sku.median(), "q1": by_sku.quantile(0.25),
                           "q3": by_sku.quantile(0.75), "count": by_sku.size()})
    is_se = stats.index.get_level_values("supplier") == "SE"
    stats["min_rows"] = np.where(is_se, SE_MIN_ROWS, MIN_ROWS)
    stats["max_b_months"] = np.where(is_se, SE_MAX_B_MONTHS, MAX_B_MONTHS)
    stats["second"] = _second_largest(rows)
    stats["stat3"] = _stat3(sales_monthly, as_of).reindex(stats.index)

    rows = rows.join(stats, on=["supplier", "sku"])
    idx = pd.MultiIndex.from_arrays([rows["supplier"], rows["sku"]])

    enough = rows["count"] >= rows["min_rows"]
    cond_b = (enough & (rows["qty"] > 5 * rows["median"])
              & (rows["qty"] > rows["q3"] + 3 * (rows["q3"] - rows["q1"]))
              & (rows["qty"] >= rows["stat3"]))
    b_months = rows.loc[cond_b].groupby(["supplier", "sku"])["month"].nunique()
    regular = b_months[b_months > stats.loc[b_months.index, "max_b_months"]].index
    final_b = cond_b & ~idx.isin(regular)

    final_u = enough & (rows["qty"] >= 2 * rows["second"]) & (rows["qty"] > 5 * rows["median"])

    flagged = rows.loc[final_b | final_u].copy()
    flagged["capped_to"] = flagged["median"]
    flagged["excess"] = flagged["qty"] - flagged["capped_to"]
    flagged["project"] = flagged.groupby("doc")["doc"].transform("size") >= 3
    return flagged[_EVENT_COLUMNS].reset_index(drop=True)


def _second_largest(rows: pd.DataFrame) -> pd.Series:
    """Вторая по величине строка SKU (для правила «беспрецедентной» строки U)."""
    ordered = rows.sort_values(["supplier", "sku", "qty"], ascending=[True, True, False])
    rank = ordered.groupby(["supplier", "sku"]).cumcount()
    second = ordered.loc[rank == 1, ["supplier", "sku", "qty"]].set_index(["supplier", "sku"])["qty"]
    return second


def _stat3(sales_monthly: pd.DataFrame, as_of: date) -> pd.Series:
    """Медиана (у SE — среднее) ненулевых ЗАКРЫТЫХ месяцев SKU из sales_monthly."""
    closed_cols = [c for c in sales_monthly.columns if c < pd.Period(as_of, "M")]
    closed = sales_monthly[closed_cols].where(sales_monthly[closed_cols] > 0)
    is_se = sales_monthly.index.get_level_values("supplier") == "SE"
    return pd.Series(np.where(is_se, closed.mean(axis=1), closed.median(axis=1)), index=sales_monthly.index)


def monthly_excess(events: pd.DataFrame, like: pd.DataFrame) -> pd.DataFrame:
    """Сумма excess по (supplier, sku) × месяц; форма/индекс/колонки как у like, 0 вне событий."""
    result = pd.DataFrame(0.0, index=like.index, columns=like.columns)
    if events.empty:
        return result

    idx = pd.MultiIndex.from_arrays([events["supplier"], events["sku"]])
    in_scope = idx.isin(like.index) & events["month"].isin(like.columns).to_numpy()
    if not in_scope.any():
        return result

    pivot = (events.loc[in_scope].assign(_idx=idx[in_scope])
             .groupby(["_idx", "month"])["excess"].sum().unstack("month"))
    pivot.index = pd.MultiIndex.from_tuples(pivot.index, names=like.index.names)
    return result.add(pivot, fill_value=0.0).reindex(index=like.index, columns=like.columns).fillna(0.0)
