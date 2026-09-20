"""API 冒烟测试。"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_analyze_ok():
    resp = client.post("/api/analyze", json={
        "start_stitches": 10,
        "rows": ["k10", "[inc1 k1]*5"],
    })
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert rows[0]["status"] == "ok"
    assert rows[0]["remaining"] == 10
    assert rows[1]["status"] == "ok"
    assert rows[1]["consumed"] == 10
    assert rows[1]["produced"] == 10


def test_analyze_shortage_payload():
    resp = client.post("/api/analyze", json={
        "start_stitches": 5,
        "rows": ["k2 dec4"],
    })
    row = resp.json()["rows"][0]
    assert row["status"] == "shortage"
    assert row["failing_instruction"] == "dec4"
    assert row["shortage"] == 1
    assert row["failing_start"] == 3 and row["failing_end"] == 7


def test_analyze_validation_error():
    resp = client.post("/api/analyze", json={"start_stitches": -1, "rows": []})
    assert resp.status_code == 422


def test_index_served():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "编织" in resp.text
