"""Данные процесса и операции над сохранёнными прогонами."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app import ingest
from app.contracts import DatasetUploaded, OrderLine, RunResult, Supplier, SupplierSummary

ROOT = Path(__file__).resolve().parents[1]
APPROVALS = ROOT / "var" / "approvals.json"


class Store:
    def __init__(self):
        self.datasets = {}
        self.runs: dict[str, RunResult] = {}
        self.run_datasets: dict[str, object] = {}
        self.latest_dataset = None

    def load(self):
        self.datasets["default"] = ingest.load_default()
        self.latest_dataset = self.datasets["default"]

    def dataset(self, dataset_id: str | None):
        return self.datasets.get(dataset_id or "default")

    def save_run(self, result: RunResult, dataset):
        self.runs[result.run_id] = result
        self.run_datasets[result.run_id] = dataset
        self.latest_dataset = dataset

    def upload(self, supplier: Supplier, files: dict[str, bytes]) -> DatasetUploaded:
        dataset = ingest.load_uploaded(supplier, files)
        dataset_id = uuid4().hex[:12]
        self.datasets[dataset_id] = dataset
        return DatasetUploaded(dataset_id=dataset_id, supplier=supplier, warnings=[])

    def line(self, run: RunResult, line_id: str) -> OrderLine | None:
        return next((line for line in run.lines if line.line_id == line_id), None)

    def supplier(self, run: RunResult, code: Supplier) -> SupplierSummary | None:
        return next((item for item in run.suppliers if item.supplier == code), None)

    def update_line(self, run: RunResult, line: OrderLine, quantity: float):
        line.final_qty = quantity
        line.amount = round(quantity * line.unit_cost, 2) if line.unit_cost is not None else None
        self.refresh_totals(run)

    def refresh_totals(self, run: RunResult):
        for summary in run.suppliers:
            rows = [line for line in run.lines if line.supplier == summary.supplier]
            summary.total_qty = sum(line.final_qty for line in rows)
            amounts = [line.amount for line in rows if line.amount is not None]
            summary.total_amount = round(sum(amounts), 2) if amounts else None
        amounts = [line.amount for line in run.lines if line.amount is not None]
        run.kpi.total_amount = round(sum(amounts), 2) if amounts else None

    def approve(self, run: RunResult, summary: SupplierSummary):
        record = {"run_id": run.run_id, "supplier": summary.supplier,
                  "approved_at": datetime.now(timezone.utc).isoformat(),
                  "lines": [{"line_id": line.line_id, "final_qty": line.final_qty}
                            for line in run.lines if line.supplier == summary.supplier]}
        APPROVALS.parent.mkdir(parents=True, exist_ok=True)
        with APPROVALS.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        summary.status = "approved"
        summary.approved_at = datetime.fromisoformat(record["approved_at"])


store = Store()
