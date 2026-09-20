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


def test_analyze_adaptive_payload():
    resp = client.post("/api/analyze", json={
        "start_stitches": 20,
        "rows": ["[k2 inc1]*~5 k5"],
    })
    assert resp.status_code == 200
    row = resp.json()["rows"][0]
    assert row["status"] == "ok"
    assert row["adaptive_blocks"] == [{
        "start": 0, "end": 12, "op_pos": 9, "reserve": 5, "actual": 5,
    }]


def test_analyze_adaptive_error_payload():
    resp = client.post("/api/analyze", json={
        "start_stitches": 20,
        "rows": ["[k2 inc1]*~4 k4"],
    })
    row = resp.json()["rows"][0]
    assert row["status"] == "error"
    assert row["error_pos"] == 9
    assert "无法整除" in row["error"]


def test_index_served():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "编织" in resp.text
