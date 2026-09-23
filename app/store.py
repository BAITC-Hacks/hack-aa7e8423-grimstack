"""In-memory datasets and runs, with SQLite history of approved orders."""

import json
import logging
import math
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

from app.contracts import RunResult


DEFAULT_APPROVALS_PATH = Path(__file__).resolve().parents[1] / "var" / "app.db"
LEGACY_APPROVALS_PATH = DEFAULT_APPROVALS_PATH.with_name("approvals.json")
CREATE_APPROVALS_TABLE = """
CREATE TABLE IF NOT EXISTS approvals (
    run_id TEXT NOT NULL,
    supplier TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    data_as_of TEXT NOT NULL,
    lines_json TEXT NOT NULL,
    PRIMARY KEY (run_id, supplier)
)
"""
logger = logging.getLogger(__name__)


@dataclass
class Store:
    default_dataset: Any
    approvals_path: Path = DEFAULT_APPROVALS_PATH
    datasets: dict[str, Any] = field(default_factory=dict)
    runs: dict[str, RunResult] = field(default_factory=dict)
    run_datasets: dict[str, Any] = field(default_factory=dict)
    lock: RLock = field(default_factory=RLock, repr=False)

    def __post_init__(self) -> None:
        with self._approval_db() as db:
            if self.approvals_path != DEFAULT_APPROVALS_PATH or not LEGACY_APPROVALS_PATH.is_file():
                return
            try:
                content = LEGACY_APPROVALS_PATH.read_text(encoding="utf-8")
                try:
                    records = json.loads(content)
                except json.JSONDecodeError:
                    records = [json.loads(line) for line in content.splitlines() if line.strip()]
                if not isinstance(records, list):
                    raise ValueError("Ожидался список утверждений")
            except (OSError, UnicodeError, ValueError) as exc:
                logger.warning("Не удалось прочитать старую историю утверждений: %s", type(exc).__name__)
                return
            for index, record in enumerate(records, start=1):
                try:
                    self._insert_approval(db, record)
                except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
                    logger.warning("Не удалось импортировать утверждение №%s: %s", index, type(exc).__name__)

    @contextmanager
    def _approval_db(self):
        self.approvals_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.approvals_path, timeout=10)) as db:
            with db:
                db.execute(CREATE_APPROVALS_TABLE)
                db.execute("CREATE INDEX IF NOT EXISTS approvals_by_date ON approvals(approved_at)")
                yield db

    @staticmethod
    def _insert_approval(db: sqlite3.Connection, record: dict[str, Any]) -> None:
        db.execute(
            """INSERT OR IGNORE INTO approvals
               (run_id, supplier, approved_at, data_as_of, lines_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                record["run_id"], record["supplier"], record["approved_at"],
                record["data_as_of"], json.dumps(record["lines"], ensure_ascii=False),
            ),
        )

    @staticmethod
    def _approval_record(row: tuple[str, str, str, str, str]) -> dict[str, Any]:
        run_id, supplier, approved_at, data_as_of, lines_json = row
        return {
            "run_id": run_id,
            "supplier": supplier,
            "approved_at": approved_at,
            "data_as_of": data_as_of,
            "lines": json.loads(lines_json),
        }

    def dataset_for(self, dataset_id: str | None) -> Any:
        if dataset_id is None:
            return self.default_dataset
        return self.datasets[dataset_id]

    def save_run(self, result: RunResult, dataset: Any) -> None:
        with self.lock:
            self.refresh_totals(result)
            self.runs[result.run_id] = result
            self.run_datasets[result.run_id] = dataset

    @staticmethod
    def refresh_totals(result: RunResult) -> None:
        """Keep displayed totals aligned with the currently selected supplier and edits."""
        for supplier in result.suppliers:
            own = [line for line in result.lines if line.supplier == supplier.supplier]
            active = [line for line in own if line.final_qty > 0]
            supplier.lines_count = len(active)
            supplier.critical_count = sum(line.urgency == "critical" for line in active)
            supplier.total_qty = sum(line.final_qty for line in active)
            has_prices = any(
                line.unit_cost is not None and math.isfinite(line.unit_cost)
                for line in own
            )
            supplier.total_amount = (
                round(sum(line.amount or 0 for line in active), 2) if has_prices else None
            )

        result.kpi.lines_to_order = sum(s.lines_count for s in result.suppliers)
        result.kpi.critical = sum(s.critical_count for s in result.suppliers)
        priced = [s.total_amount for s in result.suppliers if s.total_amount is not None]
        result.kpi.total_amount = round(sum(priced), 2) if priced else None

    def append_approval(self, record: dict[str, Any]) -> dict[str, Any]:
        """Commit an approval before marking the in-memory order as approved."""
        with self._approval_db() as db:
            self._insert_approval(db, record)
            row = db.execute(
                """SELECT run_id, supplier, approved_at, data_as_of, lines_json
                   FROM approvals WHERE run_id = ? AND supplier = ?""",
                (record["run_id"], record["supplier"]),
            ).fetchone()
        if row is None:
            raise RuntimeError("Утверждение не сохранено")
        return self._approval_record(row)

    def list_approvals(self) -> list[dict[str, Any]]:
        with self._approval_db() as db:
            rows = db.execute(
                """SELECT run_id, supplier, approved_at, data_as_of, lines_json
                   FROM approvals ORDER BY approved_at DESC, run_id, supplier"""
            ).fetchall()
        return [self._approval_record(row) for row in rows]
