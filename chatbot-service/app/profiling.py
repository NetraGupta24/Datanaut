"""Compact dataset profile.

`profile_dataframe` returns structured JSON that is stored in Firestore.
`format_profile` renders it as dense text for the LLM prompt (under ~1,500 tokens
for 20 columns). Numbers are rounded to 6 significant digits and category labels
are truncated.
"""

import math

import numpy as np
import pandas as pd

TOP_CATEGORIES = 5
TOP_CORRELATIONS = 5
MAX_LABEL_CHARS = 40
MAX_CORRELATION_COLUMNS = 50
TREND_METRICS = 3
TREND_FLAT_PCT = 5.0

# Median gap between consecutive timestamps -> human label and pandas offset alias
_FREQUENCIES = [
    (pd.Timedelta(minutes=50), pd.Timedelta(minutes=70), "hourly", "h"),
    (pd.Timedelta(hours=20), pd.Timedelta(hours=28), "daily", "D"),
    (pd.Timedelta(days=6), pd.Timedelta(days=8), "weekly", "W"),
    (pd.Timedelta(days=27), pd.Timedelta(days=32), "monthly", "M"),
    (pd.Timedelta(days=88), pd.Timedelta(days=93), "quarterly", "Q"),
    (pd.Timedelta(days=360), pd.Timedelta(days=370), "yearly", "Y"),
]


def _num(value) -> float | int | None:
    """JSON/Firestore-safe number rounded to 6 significant digits."""
    if value is None:
        return None
    value = float(value)
    if math.isnan(value) or math.isinf(value):
        return None
    rounded = float(f"{value:.6g}")
    return int(rounded) if rounded.is_integer() and abs(rounded) < 1e15 else rounded


def _label(value) -> str:
    text = str(value)
    return text if len(text) <= MAX_LABEL_CHARS else text[: MAX_LABEL_CHARS - 1] + "…"


def _iso(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%d") if ts == ts.normalize() else ts.isoformat(timespec="seconds")


def numeric_summary(series: pd.Series) -> dict:
    x = pd.to_numeric(series, errors="coerce").dropna().astype(float)
    if x.empty:
        return {}
    q1, median, q3 = x.quantile([0.25, 0.5, 0.75])
    iqr = q3 - q1
    outliers = int(((x < q1 - 1.5 * iqr) | (x > q3 + 1.5 * iqr)).sum())
    return {
        "mean": _num(x.mean()),
        "median": _num(median),
        "std": _num(x.std()) if len(x) > 1 else None,
        "min": _num(x.min()),
        "max": _num(x.max()),
        "q1": _num(q1),
        "q3": _num(q3),
        "skew": _num(x.skew()) if len(x) > 2 else None,
        "outliers": outliers,
    }


def categorical_summary(series: pd.Series, total_rows: int) -> dict:
    values = series.dropna().astype(str)
    values = values[values.str.strip() != ""]
    counts = values.value_counts()
    return {
        "unique": int(counts.size),
        "top": [
            {"value": _label(value), "pct": _num(count / total_rows * 100)}
            for value, count in counts.head(TOP_CATEGORIES).items()
        ],
    }


def infer_frequency(dates: pd.Series) -> tuple[str | None, str | None]:
    """Return (label, pandas alias) such as ("monthly", "M"), or (None, None)."""
    unique = pd.Series(dates.dropna().unique()).sort_values()
    if len(unique) < 3:
        return None, None
    gap = unique.diff().dropna().median()
    for low, high, label, alias in _FREQUENCIES:
        if low <= gap <= high:
            return label, alias
    return "irregular", None


def _trend(df: pd.DataFrame, date_col: str, metric: str) -> dict | None:
    """Compare the mean of the first and last third of the series ordered by date."""
    data = df[[date_col, metric]].dropna().sort_values(date_col)
    if len(data) < 6:
        return None
    third = len(data) // 3
    first = float(data[metric].iloc[:third].mean())
    last = float(data[metric].iloc[-third:].mean())
    change = (last - first) / abs(first) * 100 if first else (math.copysign(100.0, last) if last else 0.0)
    direction = "flat" if abs(change) < TREND_FLAT_PCT else ("up" if change > 0 else "down")
    return {"metric": metric, "direction": direction, "change_pct": _num(change)}


def datetime_summary(df: pd.DataFrame, col: str, numeric_cols: list[str]) -> dict:
    dates = df[col].dropna()
    if dates.empty:
        return {}
    label, _ = infer_frequency(dates)
    trends = [t for t in (_trend(df, col, m) for m in numeric_cols[:TREND_METRICS]) if t]
    return {"min": _iso(dates.min()), "max": _iso(dates.max()), "frequency": label, "trend": trends}


def top_correlations(df: pd.DataFrame, numeric_cols: list[str]) -> list[dict]:
    cols = [c for c in numeric_cols[:MAX_CORRELATION_COLUMNS] if df[c].nunique(dropna=True) > 1]
    if len(cols) < 2:
        return []
    corr = df[cols].astype(float).corr(min_periods=3)
    pairs = []
    for i, a in enumerate(cols):
        for b in cols[i + 1 :]:
            r = corr.at[a, b]
            if not np.isnan(r):
                pairs.append((abs(r), a, b, r))
    pairs.sort(key=lambda p: p[0], reverse=True)
    return [{"a": a, "b": b, "r": round(float(r), 3)} for _, a, b, r in pairs[:TOP_CORRELATIONS]]


def profile_dataframe(df: pd.DataFrame, types: list[str]) -> dict:
    total = len(df)
    numeric_cols = [c for c, t in zip(df.columns, types) if t == "number"]
    columns: dict[str, dict] = {}
    for col, col_type in zip(df.columns, types):
        series = df[col]
        missing = int(series.isna().sum())
        if col_type == "string":
            missing += int((series.dropna().astype(str).str.strip() == "").sum())
        info = {
            "type": col_type,
            "count": total - missing,
            "missing_pct": _num(missing / total * 100) if total else 0,
        }
        if col_type == "number":
            info.update(numeric_summary(series))
        elif col_type == "date":
            info.update(datetime_summary(df, col, numeric_cols))
        else:
            info.update(categorical_summary(series, total))
        columns[col] = info

    return {
        "rows": total,
        "cols": len(df.columns),
        "duplicate_rows": int(df.duplicated().sum()) if total else 0,
        "columns": columns,
        "top_correlations": top_correlations(df, numeric_cols),
    }


_NUMERIC_FIELDS = ("mean", "median", "std", "min", "max", "q1", "q3", "skew")


def _signed(value) -> str:
    return f"+{value}" if isinstance(value, (int, float)) and value > 0 else str(value)


def format_profile(profile: dict) -> str:
    """One line per column; much denser than JSON for the LLM context window."""
    lines = [
        f"Dataset: {profile.get('rows', 0)} rows x {profile.get('cols', 0)} columns, "
        f"{profile.get('duplicate_rows', 0)} duplicate rows",
        "Columns:",
    ]
    for name, col in profile.get("columns", {}).items():
        parts = [f"count {col.get('count')}", f"missing {col.get('missing_pct')}%"]
        col_type = col.get("type")
        if col_type == "number":
            parts += [f"{key} {col[key]}" for key in _NUMERIC_FIELDS if col.get(key) is not None]
            parts.append(f"IQR outliers {col.get('outliers', 0)}")
        elif col_type == "date":
            if col.get("min"):
                parts.append(f"range {col['min']} to {col['max']}")
            parts.append(f"frequency {col.get('frequency') or 'unknown'}")
            trends = [f"{t['metric']} {t['direction']} ({_signed(t['change_pct'])}%)" for t in col.get("trend", [])]
            if trends:
                parts.append("trend " + ", ".join(trends))
        else:
            parts.append(f"{col.get('unique', 0)} unique")
            top = [f"{t['value']} ({t['pct']}%)" for t in col.get("top", [])]
            if top:
                parts.append("top " + "; ".join(top))
        lines.append(f"- {name} [{col_type}]: " + ", ".join(parts))

    correlations = profile.get("top_correlations") or []
    if correlations:
        lines.append("Top correlations: " + ", ".join(f"{c['a']}~{c['b']} r={c['r']}" for c in correlations))
    return "\n".join(lines)
