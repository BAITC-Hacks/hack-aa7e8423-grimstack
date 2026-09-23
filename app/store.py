"""In-memory datasets and runs, with a durable log of approved orders."""

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

from app.contracts import RunResult


DEFAULT_APPROVALS_PATH = Path(__file__).resolve().parents[1] / "var" / "approvals.json"


@dataclass
class Store:
    default_dataset: Any
    approvals_path: Path = DEFAULT_APPROVALS_PATH
    datasets: dict[str, Any] = field(default_factory=dict)
    runs: dict[str, RunResult] = field(default_factory=dict)
    run_datasets: dict[str, Any] = field(default_factory=dict)
    lock: RLock = field(default_factory=RLock, repr=False)

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

    def append_approval(self, record: dict[str, Any]) -> None:
        """Write the entire JSON list atomically so a failed write keeps the old log."""
        self.approvals_path.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if self.approvals_path.exists():
            content = self.approvals_path.read_text(encoding="utf-8")
            try:
                existing = json.loads(content)
            except json.JSONDecodeError:
                existing = [json.loads(line) for line in content.splitlines() if line.strip()]
        if not isinstance(existing, list):
            raise ValueError("Файл утверждений повреждён")
        existing.append(record)
        temporary = self.approvals_path.with_suffix(".tmp")
        try:
            temporary.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(self.approvals_path)
        finally:
            temporary.unlink(missing_ok=True)
