import io
import json

import pandas as pd
import pytest

from app.ingest import IngestError, load_dataframe, preview_records, rows_to_json


def test_csv_types_numbers_dates_and_month_names():
    csv = "Month,Date,Revenue,Region\nJan,2024-01-31,12000,North\nFeb,2024-02-29,13500.5,South\n"
    df, types = load_dataframe("s.csv", csv.encode())
    assert types == ["string", "date", "number", "string"]
    assert list(df["Month"]) == ["Jan", "Feb"]  # month names must not become dates
    assert pd.api.types.is_datetime64_any_dtype(df["Date"])
    assert df["Revenue"].tolist() == [12000, 13500.5]


def test_mixed_column_stays_string_and_blank_cells_are_missing():
    csv = "Code,Score\nA-1,10\n2024-01-01,\nB-7,30\n"
    df, types = load_dataframe("s.csv", csv.encode())
    assert types == ["string", "number"]
    assert df["Score"].isna().sum() == 1


def test_semicolon_csv_and_header_toggle_off():
    csv = "1;2\n3;4\n"
    df, types = load_dataframe("s.csv", csv.encode(), first_row_header=False)
    assert list(df.columns) == ["Field 1", "Field 2"]
    assert len(df) == 2 and types == ["number", "number"]


def test_blank_and_duplicate_headers_and_empty_rows():
    csv = "a,,a\n1,2,3\n,,\n4,5,6\n"
    df, _ = load_dataframe("s.csv", csv.encode())
    assert list(df.columns) == ["a", "Field 2", "a (2)"]
    assert len(df) == 2


def test_json_array_and_data_wrapper():
    records = [{"category": "Hardware", "value": 32, "meta": {"x": 1}}, {"category": "Software", "value": 24}]
    for payload in (records, {"data": records}):
        df, types = load_dataframe("p.json", json.dumps(payload).encode())
        assert types[:2] == ["string", "number"]
        assert df.loc[0, "meta"] == '{"x": 1}'


def test_xlsx_roundtrip():
    buf = io.BytesIO()
    pd.DataFrame(
        {"when": pd.to_datetime(["2024-01-01", "2024-02-01"]), "qty": [1, 2], "name": ["a", "b"]}
    ).to_excel(buf, index=False)
    df, types = load_dataframe("book.xlsx", buf.getvalue())
    assert types == ["date", "number", "string"]
    assert df["qty"].sum() == 3


@pytest.mark.parametrize(
    "filename,content,message",
    [
        ("old.xls", b"x", ".xlsx"),
        ("notes.txt", b"x", "Unsupported"),
        ("empty.csv", b"", "empty"),
        ("bad.json", b"{not json", "Invalid JSON"),
        ("obj.json", b'{"a": 1}', "array of objects"),
        ("headers.csv", b"a,b\n", "No data rows"),
    ],
)
def test_rejects_bad_uploads(filename, content, message):
    with pytest.raises(IngestError, match=message):
        load_dataframe(filename, content)


def test_rows_json_is_null_safe_and_iso_dates():
    df, _ = load_dataframe("s.csv", b"d,v,t\n2024-01-05,1.5,x\n2024-01-06,,\n")
    rows = json.loads(rows_to_json(df))
    assert rows[0]["d"].startswith("2024-01-05T00:00:00")
    assert rows[1]["v"] is None and rows[1]["t"] is None


def test_preview_is_capped():
    df = pd.DataFrame({"text": ["x" * 1000] * 50})
    assert len(preview_records(df, 50)) == 50
    assert len(preview_records(df, 50, max_bytes=10_000)) < 50
