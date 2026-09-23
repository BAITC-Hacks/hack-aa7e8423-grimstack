"""Общие помощники разбора выгрузок 1С."""

import hashlib
import re
from pathlib import Path

import pandas as pd

# Префиксы месяцев в заголовках 1С: 'янв. 2024', 'сент. 2026', 'Январь 2024 г.'
_MONTHS = {"янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5, "июн": 6,
           "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12}
_MONTH_RE = re.compile(r"^\s*([а-яё]+)\.?\s+(\d{4})(\s*г\.?)?\s*$", re.IGNORECASE)


def month_period(label) -> pd.Period | None:
    if not isinstance(label, str):
        return None
    m = _MONTH_RE.match(label)
    if not m:
        return None
    month = _MONTHS.get(m.group(1).lower()[:3])
    return pd.Period(year=int(m.group(2)), month=month, freq="M") if month else None


def norm_code(x) -> str | None:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return None
    s = str(x).strip()
    return None if s in ("", "nan", "Итого") else s


def doc_hash(doc: str) -> str:
    return hashlib.sha1(str(doc).encode("utf-8")).hexdigest()[:10]


# ponytail: модульная переменная, не потокобезопасна — сервис грузит файлы одной выгрузки
# последовательно и однопоточно (load_uploaded), апгрейд на contextvar понадобится только при параллелизме
_last_role: str | None = None


def last_read_role() -> str | None:
    """Роль (имя файла без расширения) последнего файла, прочитанного read_sheet/read_sales_tx —
    нужна load_uploaded, чтобы указать в IngestError, какой файл не разобрался."""
    return _last_role


def read_sheet(path: Path, sheet=0) -> pd.DataFrame:
    global _last_role
    _last_role = path.stem
    return pd.read_excel(path, sheet_name=sheet, header=None, dtype=object)


TX_COLUMNS = ["supplier", "sku", "date", "doc", "qty"]


def read_sales_tx(path: Path, supplier: str) -> pd.DataFrame:
    """«Динамика продаж» 1С → строки расходных накладных 2025+ (формат одинаков у всех поставщиков).

    Файл необязателен: без него очистка от разовых строк просто не срабатывает.
    """
    if not path.exists():
        return pd.DataFrame({"supplier": pd.Series(dtype="str"), "sku": pd.Series(dtype="str"),
                             "date": pd.Series(dtype="datetime64[ns]"), "doc": pd.Series(dtype="str"),
                             "qty": pd.Series(dtype="float64")}, columns=TX_COLUMNS)
    body = read_sheet(path).iloc[1:]  # строка 0 — заголовок; «Итого» отсеется по пустому коду
    doc = body[2].astype(str)
    qty = pd.to_numeric(body[7], errors="coerce").fillna(0.0)
    tx_date = pd.to_datetime(body[0], format="%d.%m.%Y %H:%M:%S", errors="coerce")
    code = body[3].map(norm_code)
    keep = (doc.str.startswith("Расходная накладная") & (qty > 0)
            & (tx_date >= pd.Timestamp("2025-01-01")) & code.notna())
    return pd.DataFrame({"supplier": supplier, "sku": code[keep].values, "date": tx_date[keep].values,
                         "doc": doc[keep].map(doc_hash).values, "qty": qty[keep].values}, columns=TX_COLUMNS)
