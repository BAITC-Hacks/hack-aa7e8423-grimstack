"""API contract checks while the calculation core still returns sample data."""

import json
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from app import engine
from app.api.routes import EXPORT_COLUMNS, MAX_UPLOAD_BYTES
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(approval_path=tmp_path / "approvals.json")) as test_client:
        yield test_client


def new_run(client, **params):
    response = client.post("/api/runs", json=params)
    assert response.status_code == 200, response.text
    return response.json()


def assert_error(response, status):
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"detail", "code", "meta"}
    assert isinstance(body["detail"], str) and body["detail"]
    return body


def test_health_meta_and_saved_filtered_run(client):
    assert client.get("/health").json() == {"status": "ok"}
    meta = client.get("/api/meta")
    assert meta.status_code == 200
    assert {s["supplier"] for s in meta.json()["suppliers"]} == {"IEK", "SE"}

    run = new_run(client, supplier="IEK")
    assert {line["supplier"] for line in run["lines"]} == {"IEK"}
    assert run["kpi"]["lines_to_order"] == len(run["lines"]) == 3
    assert run["kpi"]["total_amount"] is None
    assert client.get(f"/api/runs/{run['run_id']}").json() == run
    assert_error(client.get("/api/runs/missing"), 404)
    assert_error(client.post("/api/runs", json={"dataset_id": "missing"}), 404)
    assert_error(client.get("/api/not-a-route"), 404)
    assert_error(client.get("/api"), 404)


def test_patch_recalculates_totals_and_checks_quantity(client):
    run = new_run(client)
    line = next(item for item in run["lines"] if item["supplier"] == "SE")
    original_se = next(s for s in run["suppliers"] if s["supplier"] == "SE")
    new_qty = line["final_qty"] + line["moq"]
    path = f"/api/runs/{run['run_id']}/lines/{line['line_id']}"

    updated = client.patch(path, json={"final_qty": new_qty})
    assert updated.status_code == 200
    assert updated.json()["amount"] == round(new_qty * line["unit_cost"], 2)
    saved = client.get(f"/api/runs/{run['run_id']}").json()
    se = next(s for s in saved["suppliers"] if s["supplier"] == "SE")
    delta = updated.json()["amount"] - line["amount"]
    assert se["total_qty"] == original_se["total_qty"] + line["moq"]
    assert se["total_amount"] == round(original_se["total_amount"] + delta, 2)
    assert saved["kpi"]["total_amount"] == se["total_amount"]

    assert_error(client.patch(path, json={"final_qty": -1}), 422)
    assert "кратно" in assert_error(
        client.patch(path, json={"final_qty": new_qty + 1}), 422
    )["detail"]
    assert_error(client.patch(f"/api/runs/{run['run_id']}/lines/unknown", json={"final_qty": 1}), 404)

    assert client.patch(path, json={"final_qty": 0}).status_code == 200
    saved = client.get(f"/api/runs/{run['run_id']}").json()
    assert saved["kpi"]["lines_to_order"] == 5
    assert next(s for s in saved["suppliers"] if s["supplier"] == "SE")["lines_count"] == 2


def test_approval_is_persisted_once_and_blocks_changes(tmp_path):
    approval_path = tmp_path / "approvals.json"
    with TestClient(create_app(approval_path=approval_path)) as client:
        run = new_run(client)
        endpoint = f"/api/runs/{run['run_id']}/suppliers/IEK/approve"
        first = client.post(endpoint)
        assert first.status_code == 200
        assert first.json()["status"] == "approved"
        assert first.json()["approved_at"]
        assert client.post(endpoint).status_code == 200
        records = json.loads(approval_path.read_text(encoding="utf-8"))
        assert len(records) == 1
        assert records[0]["supplier"] == "IEK"
        assert len(records[0]["lines"]) == 3

        line_id = next(line["line_id"] for line in run["lines"] if line["supplier"] == "IEK")
        assert_error(
            client.patch(f"/api/runs/{run['run_id']}/lines/{line_id}", json={"final_qty": 0}),
            409,
        )
        assert_error(client.post(f"/api/runs/{run['run_id']}/suppliers/XYZ/approve"), 404)


def test_export_contains_only_positive_order_lines_and_blank_iek_prices(client):
    run = new_run(client)
    iek_line = next(line for line in run["lines"] if line["supplier"] == "IEK")
    assert client.patch(
        f"/api/runs/{run['run_id']}/lines/{iek_line['line_id']}", json={"final_qty": 0}
    ).status_code == 200

    exported = client.get(f"/api/runs/{run['run_id']}/export.xlsx?supplier=IEK")
    assert exported.status_code == 200
    assert "attachment;" in exported.headers["content-disposition"]
    workbook = load_workbook(BytesIO(exported.content), read_only=True, data_only=True)
    rows = list(workbook.active.values)
    workbook.close()
    assert rows[0] == EXPORT_COLUMNS
    assert len(rows) == 3  # header and two remaining IEK products
    assert iek_line["sku"] not in {row[0] for row in rows[1:]}
    assert all(row[4] > 0 and row[5] is None and row[6] is None for row in rows[1:])
    assert_error(client.get(f"/api/runs/{run['run_id']}/export.xlsx?supplier=XYZ"), 404)


def test_upload_validation_and_uploaded_dataset_selection(client):
    roles = ("monthly_sales", "monthly_stock", "in_transit", "moq")

    def files(content=b"PKsample"):
        return [(role, (f"{role}.xlsx", content)) for role in roles]

    assert_error(client.post("/api/datasets/XYZ", files=files()), 404)
    missing = assert_error(client.post("/api/datasets/IEK", files=files()[:-1]), 422)
    assert missing["meta"]["file_role"] == "moq"
    assert_error(client.post("/api/datasets/IEK", files=files(b"")), 422)
    assert_error(client.post("/api/datasets/IEK", files=files(b"not xlsx")), 422)
    oversized = [("monthly_sales", ("sales.xlsx", b"PK" + b"x" * MAX_UPLOAD_BYTES))]
    oversized += files()[1:]
    assert_error(client.post("/api/datasets/IEK", files=oversized), 422)

    uploaded = client.post("/api/datasets/IEK", files=files())
    assert uploaded.status_code == 200
    assert uploaded.json()["supplier"] == "IEK"
    assert uploaded.json()["dataset_id"]
    assert new_run(client, dataset_id=uploaded.json()["dataset_id"])["params"][
        "dataset_id"
    ] == uploaded.json()["dataset_id"]


def test_history_and_summary_errors(client, monkeypatch):
    run = new_run(client)
    sku = run["lines"][0]["sku"]
    supplier = run["lines"][0]["supplier"]
    history = client.get(f"/api/sku/{supplier}/{sku}/history?run_id={run['run_id']}")
    assert history.status_code == 200
    assert history.json()["sku"] == sku
    assert_error(client.get(f"/api/sku/{supplier}/{sku}/history?run_id=missing"), 404)

    def unknown_sku(*_args, **_kwargs):
        raise KeyError("missing")

    monkeypatch.setattr(engine, "history", unknown_sku)
    assert_error(client.get("/api/sku/IEK/missing/history"), 404)
    assert_error(client.get("/api/sku/XYZ/missing/history"), 404)
    assert_error(client.post(f"/api/runs/{run['run_id']}/summary?supplier=IEK"), 503)


def test_spa_fallback_does_not_mask_api_errors(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<h1>app</h1>", encoding="utf-8")
    (dist / "favicon.txt").write_text("icon", encoding="utf-8")
    with TestClient(create_app(approval_path=tmp_path / "approvals.json", frontend_dist=dist)) as client:
        assert "<h1>app</h1>" in client.get("/orders/123").text
        assert client.get("/favicon.txt").text == "icon"
        assert_error(client.get("/api/not-a-route"), 404)
