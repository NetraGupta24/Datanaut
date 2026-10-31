from unittest.mock import patch

from firebase_admin import auth as firebase_auth

from app.config import MAX_ROWS_PER_REQUEST
from tests.conftest import SALES_CSV, upload


def test_upload_stores_parquet_and_metadata_only(client, fake_metadata, storage):
    res = upload(client, SALES_CSV, source_type="sample_csv")
    assert res.status_code == 201, res.text
    body = res.json()
    ds_id = body["id"]

    assert body["schema"] == {
        "headers": ["Month", "Date", "Revenue", "Units", "Region"],
        "types": ["string", "date", "number", "number", "string"],
    }
    assert body["originalRowCount"] == 6 and body["sourceType"] == "sample_csv"
    assert body["storagePath"] == f"data/user-a/{ds_id}.parquet"
    assert body["profile"]["rows"] == 6 and "Revenue" in body["profile"]["columns"]

    doc = fake_metadata.get_dataset("user-a", ds_id)
    assert doc is not None and "data" not in doc  # no full rows in Firestore
    assert len(doc["rowsPreview"]) == 6 and doc["createdAt"].endswith("Z")
    assert len(storage.load("user-a", ds_id)) == 6


def test_preview_is_limited_to_50_rows(client, fake_metadata):
    csv = "n\n" + "\n".join(str(i) for i in range(500))
    ds_id = upload(client, csv, filename="big.csv").json()["id"]
    doc = fake_metadata.get_dataset("user-a", ds_id)
    assert len(doc["rowsPreview"]) == 50 and doc["originalRowCount"] == 500


def test_rows_paging(client):
    ds_id = upload(client, SALES_CSV).json()["id"]
    res = client.get(f"/datasets/{ds_id}/rows", params={"limit": 2, "offset": 3})
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 6 and body["offset"] == 3 and body["limit"] == 2
    assert [r["Month"] for r in body["rows"]] == ["Apr", "May"]
    assert body["rows"][0]["Revenue"] == 15100
    assert body["rows"][0]["Date"].startswith("2024-04-30")


def test_rows_limit_is_capped(client):
    ds_id = upload(client, SALES_CSV).json()["id"]
    assert client.get(f"/datasets/{ds_id}/rows", params={"limit": MAX_ROWS_PER_REQUEST}).status_code == 200
    assert client.get(f"/datasets/{ds_id}/rows", params={"limit": MAX_ROWS_PER_REQUEST + 1}).status_code == 422


def test_users_cannot_read_each_others_rows(client, current_uid):
    ds_id = upload(client, SALES_CSV).json()["id"]
    current_uid["uid"] = "user-b"
    assert client.get(f"/datasets/{ds_id}/rows").status_code == 404
    assert client.delete(f"/datasets/{ds_id}").status_code == 404


def test_delete_removes_file_doc_and_linked_charts(client, fake_metadata, storage):
    ds_id = upload(client, SALES_CSV).json()["id"]
    charts = fake_metadata._user("user-a")["charts"]
    charts["c1"] = {"id": "c1", "datasetId": ds_id}
    charts["c2"] = {"id": "c2", "datasetId": "other"}

    res = client.delete(f"/datasets/{ds_id}")
    assert res.status_code == 200 and res.json() == {"deleted": True, "chartsDeleted": 1}
    assert fake_metadata.get_dataset("user-a", ds_id) is None
    assert list(charts) == ["c2"]
    assert client.get(f"/datasets/{ds_id}/rows").status_code == 404
    assert client.delete(f"/datasets/{ds_id}").status_code == 404


def test_bad_uploads_return_clear_errors(client):
    res = upload(client, b"x", filename="old.xls")
    assert res.status_code == 400 and ".xlsx" in res.json()["detail"]
    assert upload(client, SALES_CSV, name="   ").status_code == 422


def test_upload_size_limit(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    assert upload(client, SALES_CSV).status_code == 413


def test_invalid_dataset_id_rejected(client):
    assert client.get("/datasets/bad.id/rows").status_code == 422


def test_metadata_failure_cleans_up_file(client, fake_metadata, storage):
    def boom(*_):
        raise RuntimeError("firestore down")

    fake_metadata.create_dataset = boom
    res = upload(client, SALES_CSV)
    assert res.status_code == 502
    assert not list(storage.base_dir.rglob("*.parquet"))


# --- auth -----------------------------------------------------------------


def test_missing_token_is_401(unauthenticated_client):
    for method, url in [("get", "/datasets/abc/rows"), ("delete", "/datasets/abc")]:
        res = getattr(unauthenticated_client, method)(url)
        assert res.status_code == 401
        assert res.headers["www-authenticate"] == "Bearer"
    res = unauthenticated_client.post("/datasets", files={"file": ("a.csv", b"a\n1")}, data={"name": "x"})
    assert res.status_code == 401


def test_invalid_token_is_401(unauthenticated_client):
    with patch("app.auth.get_firebase_app"), patch(
        "app.auth.firebase_auth.verify_id_token", side_effect=firebase_auth.InvalidIdTokenError("bad")
    ):
        res = unauthenticated_client.get("/datasets/abc/rows", headers={"Authorization": "Bearer nope"})
    assert res.status_code == 401


def test_valid_token_uses_uid_from_token(unauthenticated_client, storage):
    import pandas as pd

    storage.save("token-uid", "ds1", pd.DataFrame({"a": [1]}))
    with patch("app.auth.get_firebase_app"), patch(
        "app.auth.firebase_auth.verify_id_token", return_value={"uid": "token-uid"}
    ):
        res = unauthenticated_client.get("/datasets/ds1/rows", headers={"Authorization": "Bearer good"})
    assert res.status_code == 200 and res.json()["rows"] == [{"a": 1}]


def test_cors_allows_only_configured_origin(client):
    ok = client.options(
        "/datasets", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"}
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    blocked = client.options(
        "/datasets", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in blocked.headers
