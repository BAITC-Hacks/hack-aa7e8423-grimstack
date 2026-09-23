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


def read_sheet(path: Path, sheet=0) -> pd.DataFrame:
    return pd.read_excel(path, sheet_name=sheet, header=None, dtype=object)
