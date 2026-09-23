"""Прогноз спроса: сезонность, тренд, сегмент, база, σ (docs/design.md §4).

Всё здесь — чистые функции над DataFrame/Series с индексом (supplier, sku).
Циклов по SKU нет: пулы сезонности считаются через groupby, дни горизонта — через
policy.horizon_demand (цикл по дням горизонта, не по SKU).
"""

import numpy as np
import pandas as pd

SEASON_MONTHS = list(range(1, 13))


def closed_columns(columns, as_of) -> list:
    """Месяцы строго до месяца as_of, отсортированные по возрастанию."""
    cutoff = pd.Period(as_of, "M")
    return sorted(c for c in columns if c < cutoff)


def segment(demand12: pd.DataFrame) -> pd.Series:
    """Syntetos–Boylan по 12 последним закрытым месяцам: smooth/erratic/intermittent/lumpy/none."""
    nz = demand12 > 0
    count = nz.sum(axis=1)
    nz_values = demand12.where(nz)
    mean = nz_values.mean(axis=1)
    std = nz_values.std(axis=1, ddof=1).fillna(0.0)  # 1 ненулевой месяц → CV²=0, решает ADI
    adi = 12 / count.replace(0, np.nan)
    cv2 = ((std / mean.replace(0, np.nan)) ** 2).fillna(0.0)

    seg = pd.Series("lumpy", index=demand12.index)
    seg[(adi >= 1.32) & (cv2 < 0.49)] = "intermittent"
    seg[(adi < 1.32) & (cv2 >= 0.49)] = "erratic"
    seg[(adi < 1.32) & (cv2 < 0.49)] = "smooth"
    seg[count == 0] = "none"
    return seg


def _year_matrix(demand: pd.DataFrame, year: int) -> pd.DataFrame | None:
    cols = sorted((c for c in demand.columns if c.year == year), key=lambda p: p.month)
    if len(cols) != 12:
        return None
    return demand[cols]


def _year_index(y: pd.DataFrame) -> pd.DataFrame:
    """Ряд года / среднее года; год без продаж даёт индекс 1 (нейтрально)."""
    idx = y.div(y.mean(axis=1).replace(0, np.nan), axis=0).fillna(1.0)
    idx.columns = SEASON_MONTHS
    return idx


def _normalize(profile: pd.DataFrame) -> pd.DataFrame:
    return profile.div(profile.mean(axis=1).replace(0, np.nan), axis=0).fillna(1.0).clip(0.3, 3.0)


def _pool_key(skus: pd.DataFrame, has_sales: pd.Series) -> pd.Series:
    """group4, если в пуле ≥5 SKU с продажами; иначе product_group; иначе поставщик целиком."""
    supplier = pd.Series(skus.index.get_level_values("supplier"), index=skus.index)

    def key_and_count(col: pd.Series) -> tuple[pd.Series, pd.Series]:
        key = supplier.astype(str) + "|" + col.astype(str)
        counts = key[has_sales & col.notna()].value_counts()
        return key, key.map(counts).fillna(0)

    g4_key, g4_count = key_and_count(skus["group4"])
    pg_key, pg_count = key_and_count(skus["product_group"])

    pool = supplier + "|ALL"
    use_pg = skus["product_group"].notna() & (pg_count >= 5)
    pool[use_pg] = pg_key[use_pg]
    use_g4 = skus["group4"].notna() & (g4_count >= 5)
    pool[use_g4] = g4_key[use_g4]  # group4 приоритетнее product_group
    return pool


def _row_corr(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = a - a.mean(axis=1, keepdims=True)
    b = b - b.mean(axis=1, keepdims=True)
    num = (a * b).sum(axis=1)
    den = np.sqrt((a ** 2).sum(axis=1) * (b ** 2).sum(axis=1))
    return np.divide(num, den, out=np.zeros_like(num), where=den > 0)


def seasonal_profile(demand: pd.DataFrame, skus: pd.DataFrame) -> pd.DataFrame:
    """12 сезонных индексов (среднее 1) на SKU: смесь 0.3·свой + 0.7·группа при условиях."""
    y2024, y2025 = _year_matrix(demand, 2024), _year_matrix(demand, 2025)
    if y2024 is None or y2025 is None:
        return pd.DataFrame(1.0, index=demand.index, columns=SEASON_MONTHS)

    has_sales = demand.sum(axis=1) > 0
    pool = _pool_key(skus, has_sales)

    pool_avg = (_year_index(y2024.groupby(pool).sum()) + _year_index(y2025.groupby(pool).sum())) / 2
    pool_profile = _normalize(pool_avg)
    group_profile = pool_profile.reindex(pool.to_numpy())
    group_profile.index = pool.index

    own_avg = (_year_index(y2024) + _year_index(y2025)) / 2
    own_profile = _normalize(own_avg)

    nz_count = (demand > 0).sum(axis=1)
    corr = pd.Series(_row_corr(y2024.to_numpy(), y2025.to_numpy()), index=demand.index)
    eligible = (nz_count >= 24) & (corr >= 0.6)

    mixed = group_profile.copy()
    mixed.loc[eligible] = 0.3 * own_profile.loc[eligible] + 0.7 * group_profile.loc[eligible]
    return mixed


def trend(demand: pd.DataFrame, as_of, segment_s: pd.Series) -> pd.Series:
    """T = Σпоследних6/Σтехже6годомраньше, клип[0.67,1.5], сжатие n/(n+6); intermittent/lumpy → 1."""
    cols = closed_columns(demand.columns, as_of)
    last6 = cols[-6:]
    prev6 = [c - 12 for c in last6]
    cur = demand.reindex(columns=last6, fill_value=0.0)
    prev = demand.reindex(columns=prev6, fill_value=0.0)

    sum_cur, sum_prev = cur.sum(axis=1), prev.sum(axis=1)
    n = pd.Series(((cur.to_numpy() > 0) & (prev.to_numpy() > 0)).sum(axis=1), index=demand.index)

    valid = (sum_cur >= 6) & (sum_prev >= 6)
    r = (sum_cur / sum_prev.replace(0, np.nan)).clip(0.67, 1.5).fillna(1.0)
    t = 1 + (r - 1) * n / (n + 6)
    t = t.where(valid, 1.0)
    t = t.where(~segment_s.isin(["intermittent", "lumpy"]), 1.0)
    return t


def base_level(demand12: pd.DataFrame, season: pd.DataFrame) -> pd.Series:
    """База = среднее(demand / season[месяц]) за окно (обычно 12 закрытых месяцев)."""
    divisor = pd.DataFrame({c: season[c.month] for c in demand12.columns}, index=demand12.index)
    return (demand12 / divisor).mean(axis=1)


def sigma(demand12: pd.DataFrame) -> pd.Series:
    """σ = std(ddof=1) спроса за окно."""
    return demand12.std(axis=1, ddof=1).fillna(0.0)


def forecast_value(base: pd.Series, season: pd.DataFrame, trend_s: pd.Series, growth_pct: float,
                    month: pd.Period) -> pd.Series:
    """forecast(m) = база · season[месяц] · тренд · (1 + прирост/100)."""
    return base * season[month.month] * trend_s * (1 + growth_pct / 100)
