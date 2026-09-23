"""Границы ручного заказа и видимость загрузки сервиса."""

import json
import logging
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import engine, ingest
from app.contracts import RunResult
from app.main import create_app


SAMPLE_RUN = Path(__file__).resolve().parents[2] / "contracts" / "sample_run.json"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "load_default", lambda: object())

    def fake_run(_dataset, params):
        run = RunResult.model_validate(json.loads(SAMPLE_RUN.read_text(encoding="utf-8")))
        run.params = params
        return run

    monkeypatch.setattr(engine, "run", fake_run)
    with TestClient(create_app(approval_path=tmp_path / "app.db")) as test_client:
        yield test_client


def test_patch_rejects_quantity_above_product_limit(client):
    run = client.post("/api/runs", json={}).json()
    line = run["lines"][0]
    limit = max(100 * line["recommended_qty"], 100 * line["moq"], 1000)
    too_large = (math.floor(limit / line["moq"]) + 1) * line["moq"]

    response = client.patch(
        f"/api/runs/{run['run_id']}/lines/{line['line_id']}",
        json={"final_qty": too_large},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_input"
    assert "не должно превышать" in response.json()["detail"]
    saved = client.get(f"/api/runs/{run['run_id']}").json()
    assert saved["lines"][0]["final_qty"] == line["final_qty"]


def test_patch_uses_relative_tolerance_for_pack_multiple(client):
    run = client.post("/api/runs", json={}).json()
    line_id = run["lines"][0]["line_id"]
    client.app.state.store.runs[run["run_id"]].lines[0].moq = 0.3
    path = f"/api/runs/{run['run_id']}/lines/{line_id}"

    assert client.patch(path, json={"final_qty": 30000.000001}).status_code == 200
    response = client.patch(path, json={"final_qty": 30000.01})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_input"
    assert "кратно" in response.json()["detail"]


def test_startup_logs_beginning_before_loading(tmp_path, monkeypatch, caplog):
    def load_default():
        assert any("Начинаю загрузку данных" in record.getMessage() for record in caplog.records)
        return object()

    monkeypatch.setattr(ingest, "load_default", load_default)
    with caplog.at_level(logging.INFO, logger="app.main"):
        with TestClient(create_app(approval_path=tmp_path / "app.db")) as client:
            assert client.get("/health").status_code == 200
    assert any("Данные загружены за" in record.getMessage() for record in caplog.records)
