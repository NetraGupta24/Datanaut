import json

import numpy as np
import pandas as pd

from app.profiling import format_profile, infer_frequency, profile_dataframe


def make_frame():
    rng = np.random.default_rng(7)
    n = 120
    units = rng.integers(100, 500, n)
    return pd.DataFrame(
        {
            "date": pd.date_range("2020-01-31", periods=n, freq="ME"),
            "revenue": units * 40 + np.arange(n) * 50,  # rising, strongly tied to units
            "units": units,
            "noise": rng.normal(0, 1, n),
            "region": rng.choice(["North", "South", "East", "West", "Central", "Islands"], n),
        }
    )


def test_numeric_summary_matches_pandas():
    df = make_frame()
    profile = profile_dataframe(df, ["date", "number", "number", "number", "string"])
    units = profile["columns"]["units"]
    assert units["count"] == 120 and units["missing_pct"] == 0
    assert units["mean"] == float(f"{df['units'].mean():.6g}")
    assert units["median"] == df["units"].median()
    assert units["min"] == df["units"].min() and units["max"] == df["units"].max()
    assert {"q1", "q3", "std", "skew", "outliers"} <= units.keys()


def test_iqr_outliers_and_missing():
    df = pd.DataFrame({"x": [10, 11, 12, 11, 10, 12, 500, None]})
    col = profile_dataframe(df, ["number"])["columns"]["x"]
    assert col["outliers"] == 1
    assert col["missing_pct"] == 12.5 and col["count"] == 7


def test_categorical_top_five_with_percentages():
    df = pd.DataFrame({"c": ["a"] * 5 + ["b"] * 3 + ["c", "d", "e", "f", "g"]})
    col = profile_dataframe(df, ["string"])["columns"]["c"]
    assert col["unique"] == 7
    assert len(col["top"]) == 5
    assert col["top"][0] == {"value": "a", "pct": round(5 / 13 * 100, 4)}


def test_datetime_frequency_and_trend():
    df = make_frame()
    col = profile_dataframe(df, ["date", "number", "number", "number", "string"])["columns"]["date"]
    assert col["min"] == "2020-01-31" and col["frequency"] == "monthly"
    revenue_trend = next(t for t in col["trend"] if t["metric"] == "revenue")
    assert revenue_trend["direction"] == "up" and revenue_trend["change_pct"] > 5


def test_infer_frequency_labels():
    assert infer_frequency(pd.Series(pd.date_range("2024-01-01", periods=10, freq="D")))[0] == "daily"
    assert infer_frequency(pd.Series(pd.date_range("2024-01-01", periods=10, freq="W")))[0] == "weekly"
    assert infer_frequency(pd.Series(pd.to_datetime(["2024-01-01", "2024-01-02"])))[0] is None


def test_dataset_level_duplicates_and_correlations():
    df = make_frame()
    df = pd.concat([df, df.iloc[[0, 1]]], ignore_index=True)
    profile = profile_dataframe(df, ["date", "number", "number", "number", "string"])
    assert profile["rows"] == 122 and profile["cols"] == 5
    assert profile["duplicate_rows"] == 2
    top = profile["top_correlations"][0]
    assert {top["a"], top["b"]} == {"revenue", "units"} and top["r"] > 0.8
    assert len(profile["top_correlations"]) <= 5


def test_profile_is_compact_for_twenty_columns():
    rng = np.random.default_rng(1)
    n = 2000
    data, types = {}, []
    for i in range(12):
        data[f"metric_{i}"] = rng.normal(1000, 250, n)
        types.append("number")
    for i in range(6):
        data[f"category_{i}"] = rng.choice([f"Category label number {k}" for k in range(30)], n)
        types.append("string")
    for i in range(2):
        data[f"date_{i}"] = pd.date_range("2021-01-01", periods=n, freq="D")
        types.append("date")
    profile = profile_dataframe(pd.DataFrame(data), types)

    text = format_profile(profile)
    approx_tokens = len(text) / 3  # conservative: text with many numbers is ~3 chars per token
    assert approx_tokens < 1500, approx_tokens
    assert text.count("\n- ") == 20 and "Top correlations:" in text

    stored = json.dumps(profile, allow_nan=False)  # Firestore/JSON safe: no NaN/inf anywhere
    assert len(stored) < 10_000  # tiny next to Firestore's 1 MB doc limit


def test_format_profile_lines():
    df = make_frame()
    text = format_profile(profile_dataframe(df, ["date", "number", "number", "number", "string"]))
    assert text.startswith("Dataset: 120 rows x 5 columns, 0 duplicate rows")
    assert "- units [number]: count 120, missing 0%, mean " in text
    assert "- date [date]: count 120, missing 0%, range 2020-01-31 to 2029-12-31, frequency monthly" in text
    assert "revenue up (+" in text
    assert "- region [string]: count 120, missing 0%, 6 unique, top " in text
