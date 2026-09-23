"""Выгрузки IEK → Dataset. Правила — docs/data-profile.md §1, §2, §4, §5."""

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from app.ingest.common import month_period, norm_code, read_sales_tx, read_sheet
from app.ingest.dataset import SKU_COLUMNS, Dataset

SUPPLIER = "IEK"
_JUNK_CODES = {"0", "1"}
_ETA_RE = re.compile(r"поступление до (\d{2}\.\d{2}\.\d{4})")
_PACK_RE = re.compile(r"\((\d+)(?:/\d+)?\)\s*$")
_GROUP4_RE = re.compile(r"^\d{9}_$")
_MONTHS = pd.period_range("2024-01", "2026-09", freq="M")
_ABC_MONTHS = pd.period_range("2025-09", "2026-08", freq="M")  # 12 закрытых месяцев для категории


def _clean_text(x) -> str | None:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = re.sub(r"\s+", " ", str(x)).strip()
    return s or None


def _package_size(name: str | None) -> float | None:
    if not name:
        return None
    m = _PACK_RE.search(name)
    return float(m.group(1)) if m else None


def _month_columns(header_row: pd.Series) -> dict:
    """Колонка → pd.Period для тех колонок заголовка, что распознаны как месяц."""
    return {col: p for col, val in header_row.items() if (p := month_period(val)) is not None}


def _monthly_sales(path: Path) -> tuple[pd.DataFrame, pd.Series]:
    """Заголовок в строках 0–1, данные с строки 2. Строка и колонка «Итого» отбрасываются сами:
    у строки «Итого» пустой код, а колонка «Итого» не распознаётся как месяц."""
    raw = read_sheet(path)
    months = _month_columns(raw.iloc[0])
    body = raw.iloc[2:]
    code = body[1].map(norm_code)
    body, code = body[code.notna()], code[code.notna()]
    idx = pd.Index(code.values, name="sku")
    # .values — body[col] индексирован номерами строк листа, а не idx; без .values pandas
    # выровняет по меткам индекса и получит одни NaN
    sales = pd.DataFrame({p: pd.to_numeric(body[col], errors="coerce").fillna(0.0).values
                          for col, p in months.items()}, index=idx)
    name = pd.Series(body[0].map(_clean_text).values, index=idx, name="name")
    return sales, name


def _monthly_stock(path: Path) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Заголовок в строках 0–2, данные с строки 3. Отрицательные остатки → 0."""
    raw = read_sheet(path)
    months = _month_columns(raw.iloc[0])
    body = raw.iloc[3:]
    code = body[2].map(norm_code)
    body, code = body[code.notna()], code[code.notna()]
    idx = pd.Index(code.values, name="sku")
    stock = pd.DataFrame({p: pd.to_numeric(body[col], errors="coerce").fillna(0.0).clip(lower=0.0).values
                          for col, p in months.items()}, index=idx)
    name = pd.Series(body[0].map(_clean_text).values, index=idx, name="name")
    unit = pd.Series(body[1].map(_clean_text).values, index=idx, name="unit")
    return stock, name, unit


def _in_transit(path: Path) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series, pd.Series]:
    """«Путь»: ассортиментная матрица с 6 колонками поставок. Служебные строки (пустой код, '0',
    '1', заголовки секций) и дубли кода выкинуть. Секция «НОВИНКИ» → флаг new_item."""
    raw = read_sheet(path)
    header = raw.iloc[0]
    shipment_cols = {}
    for col in range(3, raw.shape[1]):
        m = _ETA_RE.search(str(header[col]))
        if m:
            shipment_cols[col] = pd.Timestamp(datetime.strptime(m.group(1), "%d.%m.%Y"))

    body = raw.iloc[1:].copy()
    code = body[0].map(norm_code)
    is_section_header = code.isna() & body[1].notna() & (body[1].astype(str).str.strip() != "1")
    section = body[1].where(is_section_header).ffill().astype(str)
    new_item_all = section.str.contains("новинк", case=False, na=False)

    body["sku"] = code
    body["new_item"] = new_item_all
    body = body[body["sku"].notna() & ~body["sku"].isin(_JUNK_CODES)]
    body = body.drop_duplicates(subset="sku", keep="first").set_index("sku")

    name = body[2].map(_clean_text)
    article = body[1].map(_clean_text)
    keep_1m = name.fillna("").str.contains("поддерживаем склад", case=False, na=False)
    new_item = body["new_item"]

    rows = []
    for col, eta in shipment_cols.items():
        qty = pd.to_numeric(body[col], errors="coerce")
        for sku, v in qty[qty > 0].items():
            rows.append({"supplier": SUPPLIER, "sku": sku, "qty": float(v), "eta": eta})
    transit = pd.DataFrame(rows, columns=["supplier", "sku", "qty", "eta"])

    return transit, name.rename("name"), article.rename("article"), new_item.rename("new_item"), \
        keep_1m.rename("keep_1m")


def _moq(path: Path) -> tuple[pd.Series, pd.Series, pd.Series]:
    """«Мин. разр. к отгр.» — кратность. Дубли кода — первая строка. Пусто → упаковка из
    названия '(N)'/'(N/M)', иначе 1."""
    raw = read_sheet(path)
    body = raw.iloc[1:].copy()
    body["sku"] = body[1].map(norm_code)
    body = body[body["sku"].notna()]
    body = body.drop_duplicates(subset="sku", keep="first").set_index("sku")

    name = body[3].map(_clean_text)
    article = body[2].map(_clean_text)
    raw_moq = pd.to_numeric(body[4], errors="coerce")
    pack = name.map(_package_size)
    moq = raw_moq.where(raw_moq.notna(), pack).fillna(1.0)
    return name.rename("name"), article.rename("article"), moq.rename("moq")


def _abc_category(volume: pd.Series) -> pd.Series:
    """ABC по количеству продаж: кумулятивная доля ≤80% → A, ≤95% → B, иначе C. Без продаж → C."""
    category = pd.Series("C", index=volume.index)
    positive = volume[volume > 0].sort_values(ascending=False)
    if positive.empty:
        return category
    share = positive.cumsum() / positive.sum()
    category.loc[share.index[share <= 0.80]] = "A"
    category.loc[share.index[(share > 0.80) & (share <= 0.95)]] = "B"
    return category


def load(folder: Path, as_of: date) -> Dataset:
    folder = Path(folder)
    sales, sales_name = _monthly_sales(folder / "monthly_sales.xlsx")
    stock, stock_name, stock_unit = _monthly_stock(folder / "monthly_stock.xlsx")
    tx = read_sales_tx(folder / "sales_tx.xlsx", SUPPLIER)
    transit, transit_name, transit_article, transit_new_item, transit_keep_1m = \
        _in_transit(folder / "in_transit.xlsx")
    moq_name, moq_article, moq_value = _moq(folder / "moq.xlsx")

    universe = sorted(set(sales.index) | set(stock.index) | set(transit["sku"]) | set(moq_value.index))
    idx = pd.Index(universe, name="sku")

    sales = sales.reindex(index=idx, columns=_MONTHS, fill_value=0.0)
    stock = stock.reindex(index=idx, columns=_MONTHS, fill_value=0.0)

    category = _abc_category(sales.loc[:, _ABC_MONTHS].sum(axis=1))

    name = sales_name.reindex(idx)
    name = name.where(name.notna(), stock_name.reindex(idx))
    name = name.where(name.notna(), transit_name.reindex(idx))
    name = name.where(name.notna(), moq_name.reindex(idx))

    article = moq_article.reindex(idx)
    article = article.where(article.notna(), transit_article.reindex(idx))

    unit = stock_unit.reindex(idx)
    unit = unit.where(unit.notna(), "шт")

    group4 = pd.Series([sku[:4] if _GROUP4_RE.match(sku) else None for sku in idx], index=idx)
    discontinued = name.fillna("").str.contains("!!!", regex=False)
    keep_1m = transit_keep_1m.reindex(idx).fillna(False)
    new_item = transit_new_item.reindex(idx).fillna(False)
    moq = moq_value.reindex(idx).fillna(1.0)

    skus = pd.DataFrame({
        "article": article, "name": name, "unit": unit, "category": category, "group4": group4,
        "moq": moq, "unit_cost": float("nan"), "discontinued": discontinued, "keep_1m": keep_1m,
        "new_item": new_item, "product_group": None,
    }, index=idx)[SKU_COLUMNS]

    sep = pd.Period("2026-09", "M")
    stock_now = pd.DataFrame({
        "qty": (stock[sep] - sales[sep]).clip(lower=0.0),
        "source": "estimate_lower_bound",
    }, index=idx)

    def with_supplier(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out.index = pd.MultiIndex.from_arrays([[SUPPLIER] * len(out), out.index], names=["supplier", "sku"])
        return out

    return Dataset(
        as_of=as_of,
        skus=with_supplier(skus),
        sales_monthly=with_supplier(sales),
        stock_monthly=with_supplier(stock),
        sales_tx=tx,
        stock_now=with_supplier(stock_now),
        in_transit=transit,
    )
