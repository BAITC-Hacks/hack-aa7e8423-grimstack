"""Выгрузки Systeme Electric → Dataset. Правила — docs/data-profile.md §1, §2, §4, §5."""

import re
from datetime import date
from pathlib import Path

import pandas as pd

from app.ingest.common import month_period, norm_code, read_sales_tx, read_sheet
from app.ingest.dataset import SKU_COLUMNS, Dataset

_GROUP4_RE = re.compile(r"^\d{9}_$")


def _clean_name(x) -> str | None:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    return re.sub(r"\s+", " ", str(x)).strip() or None


def _month_columns(header: pd.Series) -> dict:
    """{номер колонки: pd.Period} только для заголовков-месяцев, остальное (Итого, Ср мес…) отсеивается."""
    return {c: p for c, v in header.items() if (p := month_period(v)) is not None}


def _monthly_sales(folder: Path) -> tuple[pd.Series, pd.Series, pd.Series, pd.DataFrame]:
    """Лист SKU (первый), лист сезонности игнорируем. Возвращает name, article, moq (по коду) и продажи."""
    df = read_sheet(folder / "monthly_sales.xlsx", sheet=0)
    header, data = df.iloc[0], df.iloc[2:]
    codes = data[1].map(norm_code)
    keep = codes.notna()
    data, codes = data[keep], codes[keep]
    months = _month_columns(header)
    sales = pd.DataFrame({p: pd.to_numeric(data[c], errors="coerce").fillna(0.0).values for c, p in months.items()},
                         index=codes.values)
    name = pd.Series(data[0].map(_clean_name).values, index=codes.values)
    article = pd.Series(data[2].values, index=codes.values)
    moq = pd.Series(pd.to_numeric(data[3], errors="coerce").fillna(1.0).values, index=codes.values)
    return name, article, moq, sales


def _monthly_stock(folder: Path) -> tuple[pd.Series, pd.DataFrame]:
    df = read_sheet(folder / "monthly_stock.xlsx", sheet=0)
    header, data = df.iloc[0], df.iloc[3:]
    codes = data[2].map(norm_code)
    keep = codes.notna()
    data, codes = data[keep], codes[keep]
    months = _month_columns(header)
    stock = pd.DataFrame({p: pd.to_numeric(data[c], errors="coerce").fillna(0.0).clip(lower=0).values for c, p in months.items()},
                         index=codes.values)
    name = pd.Series(data[1].map(_clean_name).values, index=codes.values)
    return name, stock


def _find_col(header: pd.Series, name: str, *, startswith: bool = False) -> int:
    for i, v in header.items():
        if not isinstance(v, str):
            continue
        s = v.strip()
        if (s.startswith(name) if startswith else s == name):
            return i
    raise KeyError(name)


def _moq_file(folder: Path) -> tuple[pd.Series, pd.Series]:
    """Возвращает (article, moq) по коду. Строка 0 — заголовок, 1 — пустая, в конце «Итого»."""
    df = read_sheet(folder / "moq.xlsx", sheet=0)
    data = df.iloc[2:]
    codes = data[2].map(norm_code)
    keep = codes.notna()
    data, codes = data[keep], codes[keep]
    article = pd.Series(data[3].values, index=codes.values)
    moq = pd.Series(pd.to_numeric(data[4], errors="coerce").fillna(0.0).values, index=codes.values)
    return article, moq


def _td_sheet(folder: Path):
    df = read_sheet(folder / "in_transit.xlsx", sheet="TDSheet")
    header, data = df.iloc[1], df.iloc[2:]
    col = {
        "article": _find_col(header, "Артикул поставщика"),
        "code": _find_col(header, "Код 1с"),
        "name": _find_col(header, "Наименование"),
        "category": _find_col(header, "Категория 2026"),
        "unit_cost": _find_col(header, "СС реал"),
        "showcase": _find_col(header, "Витрина"),
        "reserve_stock": _find_col(header, "Остаток ТЗ"),
        "rc_ekt": _find_col(header, "РЦ ЕКТ", startswith=True),
        "retail": _find_col(header, "Розничный склад"),
        "free": _find_col(header, "Свободный остаток"),
        "transit": _find_col(header, "СЭ в пути 24.09"),
    }
    codes = data[col["code"]].map(norm_code)
    keep = codes.notna()
    data, codes = data[keep], codes[keep]

    def num(key):
        return pd.to_numeric(data[col[key]], errors="coerce").fillna(0.0)

    stock_now = (num("free") + num("showcase") + num("reserve_stock") + num("rc_ekt") + num("retail")).values
    category = data[col["category"]].map(lambda v: f"Кат. {int(float(v))}" if pd.notna(v) else None).values
    unit_cost = num("unit_cost").values
    name = data[col["name"]].map(_clean_name).values
    article = data[col["article"]].values
    months = _month_columns(header)
    monthly_sales = pd.DataFrame({p: pd.to_numeric(data[c], errors="coerce").fillna(0.0).values for c, p in months.items()},
                                 index=codes.values)
    frame = pd.DataFrame({"name": name, "article": article, "category": category, "unit_cost": unit_cost,
                          "stock_now": stock_now}, index=codes.values)
    transit_qty = num("transit")
    in_transit = pd.DataFrame({"supplier": "SE", "sku": codes.values, "qty": transit_qty.values,
                               "eta": pd.Timestamp(2026, 9, 24)})
    in_transit = in_transit[in_transit["qty"] > 0].reset_index(drop=True)
    return frame, monthly_sales, in_transit


def load(folder: Path, as_of: date) -> Dataset:
    months = pd.period_range("2024-01", pd.Period(as_of, "M"), freq="M")

    sales_name, sales_article, sales_moq, sales = _monthly_sales(folder)
    stock_name, stock = _monthly_stock(folder)
    tx = read_sales_tx(folder / "sales_tx.xlsx", "SE")
    moq_article, moq_qty = _moq_file(folder)
    td, td_sales, in_transit = _td_sheet(folder)

    # 22 SKU есть только в TDSheet (нет в monthly_sales) — добавить им помесячные продажи из TDSheet
    only_td = td_sales.index.difference(sales.index)
    sales = pd.concat([sales, td_sales.loc[only_td]])

    universe = sales.index.union(stock.index).union(td.index).union(moq_qty.index)

    sales = sales.reindex(index=universe, columns=months, fill_value=0.0)
    stock = stock.reindex(index=universe, columns=months, fill_value=0.0).clip(lower=0)

    # moq: файл MOQ (0 остаётся 0 — «не заказывать»), иначе кратность из monthly_sales, иначе 1
    moq = moq_qty.reindex(universe)
    moq = moq.where(moq.notna(), sales_moq.reindex(universe))
    moq = moq.fillna(1.0)

    # имя — из 1С: TDSheet → продажи → остатки
    name = td["name"].reindex(universe)
    name = name.where(name.notna(), sales_name.reindex(universe))
    name = name.where(name.notna(), stock_name.reindex(universe))

    # артикул — из TDSheet → продажи → MOQ
    article = td["article"].reindex(universe)
    article = article.where(article.notna(), sales_article.reindex(universe))
    article = article.where(article.notna(), moq_article.reindex(universe))

    category = td["category"].reindex(universe)
    unit_cost = td["unit_cost"].reindex(universe)

    discontinued = name.fillna("").str.contains("!!!", regex=False)
    new_item = category == "Кат. 5"

    idx = pd.MultiIndex.from_arrays([["SE"] * len(universe), universe], names=["supplier", "sku"])
    sales.index = idx
    stock.index = idx

    skus = pd.DataFrame({
        "article": article.values, "name": name.values, "unit": "шт", "category": category.values,
        "group4": [s[:4] if _GROUP4_RE.match(s) else None for s in universe],
        "moq": moq.values, "unit_cost": unit_cost.values, "discontinued": discontinued.values,
        "keep_1m": False, "new_item": new_item.values, "product_group": None,
    }, index=idx)[SKU_COLUMNS]

    stock_now_td = td["stock_now"].reindex(universe)
    sep2026 = months[-1]
    estimate = (stock[sep2026].values - sales[sep2026].values)
    estimate = pd.Series(estimate, index=universe).clip(lower=0)
    stock_now_qty = stock_now_td.where(stock_now_td.notna(), estimate)
    stock_source = pd.Series("estimate_lower_bound", index=universe)
    stock_source[stock_now_td.notna()] = "warehouses"
    stock_now = pd.DataFrame({"qty": stock_now_qty.values, "source": stock_source.values}, index=idx)

    return Dataset(as_of=as_of, skus=skus, sales_monthly=sales, stock_monthly=stock,
                   sales_tx=tx, stock_now=stock_now, in_transit=in_transit)
