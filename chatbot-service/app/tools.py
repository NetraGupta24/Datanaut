"""Whitelisted analysis tools the LLM can call.

The model only chooses a tool and its arguments; pandas does every calculation.
Arguments are validated with Pydantic, column names are checked against the
DataFrame, and filters are applied as boolean masks. Model output is never
passed to eval/exec/DataFrame.query.
"""

import difflib
import json
import math
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

MAX_RESULT_ROWS = 50
MAX_FILTERS = 5

FilterOp = Literal["==", "!=", ">", ">=", "<", "<=", "in", "between"]
Agg = Literal["sum", "mean", "median", "min", "max", "count"]
Freq = Literal["D", "W", "M", "Q", "Y"]


class ToolError(ValueError):
    """Bad arguments for this dataset; the message goes back to the model so it can retry."""


# --- argument models ------------------------------------------------------


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Filter(_Args):
    column: str = Field(description="Exact column name")
    op: FilterOp
    value: Any = Field(description="Scalar; a list for 'in'; [low, high] for 'between'")

    @model_validator(mode="after")
    def _check_value_shape(self):
        if self.op == "in" and not (isinstance(self.value, list) and self.value):
            raise ValueError("'in' needs a non-empty list value")
        if self.op == "between" and not (isinstance(self.value, list) and len(self.value) == 2):
            raise ValueError("'between' needs [low, high]")
        if self.op not in ("in", "between") and isinstance(self.value, (list, dict)):
            raise ValueError(f"'{self.op}' needs a single value")
        return self


class _Filtered(_Args):
    filters: list[Filter] = Field(default_factory=list, max_length=MAX_FILTERS, description="Optional row filters (AND)")


class DescribeColumnArgs(_Filtered):
    column: str


class GroupByArgs(_Filtered):
    column: str = Field(description="Column to group by")
    metric: str | None = Field(None, description="Numeric column to aggregate; omit when agg is 'count'")
    agg: Agg = "sum"


class TopNArgs(_Filtered):
    column: str = Field(description="Column whose values are ranked")
    metric: str = Field(description="Numeric column to rank by")
    n: int = Field(5, ge=1, le=MAX_RESULT_ROWS)
    agg: Agg = "sum"
    order: Literal["desc", "asc"] = Field("desc", description="'desc' for top, 'asc' for bottom")


class TrendArgs(_Filtered):
    date_column: str
    metric: str | None = Field(None, description="Numeric column; omit when agg is 'count'")
    freq: Freq = Field("M", description="D=day, W=week, M=month, Q=quarter, Y=year")
    agg: Agg = "sum"


class CompareArgs(_Filtered):
    column: str = Field(description="Column that holds the two groups")
    group_a: Any = Field(description="Value of `column` for the first group")
    group_b: Any = Field(description="Value of `column` for the second group")
    metric: str = Field(description="Numeric column to compare")


class CorrelationArgs(_Filtered):
    col_a: str
    col_b: str


class OutliersArgs(_Filtered):
    column: str
    method: Literal["iqr", "zscore"] = "iqr"


# --- helpers --------------------------------------------------------------


def _py(value):
    """Plain JSON-safe Python value."""
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        ts = pd.Timestamp(value)
        if pd.isna(ts):
            return None
        return ts.strftime("%Y-%m-%d") if ts == ts.normalize() else ts.isoformat(timespec="seconds")
    if isinstance(value, pd.Period):
        return str(value)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return None
        return int(value) if value.is_integer() and abs(value) < 1e15 else round(value, 4)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value if isinstance(value, (int, str)) else str(value)


def _table(columns: list[str], rows: list[list], note: str | None = None) -> dict:
    total = len(rows)
    out = {"columns": columns, "rows": [[_py(v) for v in row] for row in rows[:MAX_RESULT_ROWS]]}
    notes = [note] if note else []
    if total > MAX_RESULT_ROWS:
        notes.append(f"showing {MAX_RESULT_ROWS} of {total} rows")
    if notes:
        out["note"] = "; ".join(notes)
    return out


def _column(df: pd.DataFrame, name: str) -> str:
    if name in df.columns:
        return name
    lowered = {str(c).lower(): c for c in df.columns}
    if name.lower() in lowered:
        return lowered[name.lower()]
    close = difflib.get_close_matches(name, [str(c) for c in df.columns], n=3)
    hint = f" Did you mean {', '.join(repr(c) for c in close)}?" if close else ""
    raise ToolError(f"Unknown column {name!r}.{hint} Available: {', '.join(map(str, df.columns))}")


def _is_number(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def _is_date(s: pd.Series) -> bool:
    return pd.api.types.is_datetime64_any_dtype(s)


def _numeric(df: pd.DataFrame, name: str) -> str:
    col = _column(df, name)
    if not _is_number(df[col]):
        raise ToolError(f"Column {col!r} is not numeric.")
    return col


def _coerce(series: pd.Series, value, column: str):
    try:
        if _is_number(series):
            return float(value)
        if _is_date(series):
            return pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        kind = "a number" if _is_number(series) else "a date"
        raise ToolError(f"Filter value {value!r} for {column!r} must be {kind}.") from exc
    return str(value).strip().casefold()


def _text(series: pd.Series) -> pd.Series:
    return series.astype("string").str.strip().str.casefold()


def _mask(df: pd.DataFrame, f: Filter) -> pd.Series:
    col = _column(df, f.column)
    s = df[col]
    is_text = not (_is_number(s) or _is_date(s))
    if f.op in (">", ">=", "<", "<=", "between") and is_text:
        raise ToolError(f"Operator {f.op!r} needs a numeric or date column; {col!r} is text.")
    target = _text(s) if is_text else s

    if f.op == "in":
        values = [_coerce(s, v, col) for v in f.value]
        return target.isin(values).fillna(False)
    if f.op == "between":
        low, high = (_coerce(s, v, col) for v in f.value)
        return target.between(low, high).fillna(False)

    value = _coerce(s, f.value, col)
    ops: dict[str, Callable[[pd.Series], pd.Series]] = {
        "==": lambda t: t == value,
        "!=": lambda t: t != value,
        ">": lambda t: t > value,
        ">=": lambda t: t >= value,
        "<": lambda t: t < value,
        "<=": lambda t: t <= value,
    }
    return ops[f.op](target).fillna(False).astype(bool)


def apply_filters(df: pd.DataFrame, filters: list[Filter]) -> pd.DataFrame:
    if not filters:
        return df
    mask = pd.Series(True, index=df.index)
    for f in filters:
        mask &= _mask(df, f)
    out = df[mask]
    if out.empty:
        raise ToolError("No rows match these filters.")
    return out


def _aggregate(grouped, metric: str | None, agg: str) -> pd.Series:
    if agg == "count":
        return grouped.size() if metric is None else grouped[metric].count()
    if metric is None:
        raise ToolError(f"agg {agg!r} needs a metric column.")
    return grouped[metric].agg(agg)


def _agg_label(agg: str, metric: str | None) -> str:
    return "count" if agg == "count" and metric is None else f"{agg}({metric})"


def _match_group(df: pd.DataFrame, col: str, value) -> pd.DataFrame:
    rows = df[_mask(df, Filter(column=col, op="==", value=value))]
    if rows.empty:
        sample = ", ".join(map(str, df[col].dropna().astype(str).value_counts().index[:20]))
        raise ToolError(f"No rows where {col!r} == {value!r}. Some values: {sample}")
    return rows


# --- tools ----------------------------------------------------------------


def describe_column(df: pd.DataFrame, args: DescribeColumnArgs) -> dict:
    col = _column(df, args.column)
    data = apply_filters(df, args.filters)
    s = data[col]
    stats: list[list] = [["rows", len(s)], ["missing", int(s.isna().sum())]]
    if _is_number(s):
        x = s.dropna()
        stats += [
            ["sum", x.sum()], ["mean", x.mean()], ["median", x.median()], ["std", x.std()],
            ["min", x.min()], ["max", x.max()], ["q1", x.quantile(0.25)], ["q3", x.quantile(0.75)],
        ]
    elif _is_date(s):
        x = s.dropna()
        stats += [["min", x.min()], ["max", x.max()], ["unique", x.nunique()]]
    else:
        counts = s.dropna().astype(str).value_counts()
        stats += [["unique", counts.size]] + [[f"top: {k}", v] for k, v in counts.head(10).items()]
    return _table(["stat", col], stats)


def group_by(df: pd.DataFrame, args: GroupByArgs) -> dict:
    col = _column(df, args.column)
    metric = _numeric(df, args.metric) if args.metric else None
    data = apply_filters(df, args.filters)
    result = _aggregate(data.groupby(col, sort=False), metric, args.agg).sort_values(ascending=False)
    label = _agg_label(args.agg, metric)
    rows = [[k, v] for k, v in result.items()]
    columns = [col, label]
    if args.agg in ("sum", "count") and result.sum():
        total = result.sum()
        rows = [[k, v, v / total * 100] for k, v in result.items()]
        columns.append("share_pct")
    return _table(columns, rows, note=f"{len(result)} groups")


def top_n(df: pd.DataFrame, args: TopNArgs) -> dict:
    col = _column(df, args.column)
    metric = _numeric(df, args.metric)
    data = apply_filters(df, args.filters)
    result = _aggregate(data.groupby(col, sort=False), metric, args.agg)
    result = result.sort_values(ascending=args.order == "asc").head(args.n)
    return _table([col, _agg_label(args.agg, metric)], [[k, v] for k, v in result.items()])


def trend(df: pd.DataFrame, args: TrendArgs) -> dict:
    date_col = _column(df, args.date_column)
    if not _is_date(df[date_col]):
        raise ToolError(f"Column {date_col!r} is not a date column.")
    metric = _numeric(df, args.metric) if args.metric else None
    data = apply_filters(df, args.filters).dropna(subset=[date_col])
    periods = data[date_col].dt.to_period(args.freq)
    result = _aggregate(data.groupby(periods), metric, args.agg).sort_index()
    if result.empty:
        raise ToolError("No dated rows to build a trend from.")

    labels = [p.start_time.strftime("%Y-%m-%d") if args.freq == "W" else str(p) for p in result.index]
    rows = [[label, v] for label, v in zip(labels, result.values)]
    first, last = result.iloc[0], result.iloc[-1]
    note = f"{len(result)} periods; first {_py(first)} ({labels[0]}), last {_py(last)} ({labels[-1]})"
    if first:
        note += f", change {_py((last - first) / abs(first) * 100)}%"
    if len(rows) > MAX_RESULT_ROWS:
        rows = rows[-MAX_RESULT_ROWS:]
        note += f"; showing the last {MAX_RESULT_ROWS} periods"
    return _table(["period", _agg_label(args.agg, metric)], rows, note=note)


def compare(df: pd.DataFrame, args: CompareArgs) -> dict:
    col = _column(df, args.column)
    metric = _numeric(df, args.metric)
    data = apply_filters(df, args.filters)
    a = _match_group(data, col, args.group_a)[metric].dropna()
    b = _match_group(data, col, args.group_b)[metric].dropna()
    rows = []
    for stat in ("count", "sum", "mean", "median", "min", "max"):
        va, vb = getattr(a, stat)(), getattr(b, stat)()
        pct = (va - vb) / abs(vb) * 100 if vb else None
        rows.append([stat, va, vb, va - vb, pct])
    return _table(["stat", str(args.group_a), str(args.group_b), "difference (a-b)", "difference_pct"], rows)


def correlation(df: pd.DataFrame, args: CorrelationArgs) -> dict:
    col_a, col_b = _numeric(df, args.col_a), _numeric(df, args.col_b)
    data = apply_filters(df, args.filters)[[col_a, col_b]].dropna()
    if len(data) < 3:
        raise ToolError("Need at least 3 rows with both values.")
    pearson = data[col_a].corr(data[col_b])
    spearman = data[col_a].rank().corr(data[col_b].rank())  # Spearman = Pearson on ranks
    strength = "none" if pd.isna(pearson) else "weak" if abs(pearson) < 0.3 else "moderate" if abs(pearson) < 0.7 else "strong"
    direction = "positive" if pearson and pearson > 0 else "negative" if pearson and pearson < 0 else ""
    rows = [["pearson_r", pearson], ["spearman_rho", spearman], ["n", len(data)], ["strength", f"{strength} {direction}".strip()]]
    return _table(["stat", f"{col_a} vs {col_b}"], rows)


def outliers(df: pd.DataFrame, args: OutliersArgs) -> dict:
    col = _numeric(df, args.column)
    data = apply_filters(df, args.filters)
    x = data[col].dropna()
    if len(x) < 4:
        raise ToolError("Need at least 4 values to look for outliers.")
    if args.method == "iqr":
        q1, q3 = x.quantile(0.25), x.quantile(0.75)
        low, high = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
        flagged = x[(x < low) | (x > high)]
        score = (flagged - x.median()).abs()
        note = f"IQR bounds [{_py(low)}, {_py(high)}]"
    else:
        std = x.std()
        if not std:
            raise ToolError(f"{col!r} has no variation.")
        z = (x - x.mean()) / std
        flagged = x[z.abs() > 3]
        score = z[flagged.index]
        note = "|z| > 3"
    flagged = flagged.loc[score.abs().sort_values(ascending=False).index]

    # Show one descriptive column (first text column, else first date column) so rows are identifiable
    text_cols = [c for c in df.columns if not (_is_number(df[c]) or _is_date(df[c]))]
    date_cols = [c for c in df.columns if _is_date(df[c])]
    label_col = (text_cols or date_cols or [None])[0]
    columns = ["row", col, "z" if args.method == "zscore" else "distance_from_median"]
    rows = []
    for idx, value in flagged.items():
        row = [int(idx) + 1, value, score[idx]]
        if label_col is not None:
            row.insert(1, data.at[idx, label_col])
        rows.append(row)
    if label_col is not None:
        columns.insert(1, label_col)
    return _table(columns, rows, note=f"{len(flagged)} outliers of {len(x)} values; {note}")


# --- registry -------------------------------------------------------------


TOOLS: dict[str, tuple[type[_Args], Callable[[pd.DataFrame, Any], dict], str]] = {
    "describe_column": (DescribeColumnArgs, describe_column, "Summary statistics for one column (optionally filtered)."),
    "group_by": (GroupByArgs, group_by, "Aggregate a numeric metric per value of a column, largest first, with share %."),
    "top_n": (TopNArgs, top_n, "Top (or bottom) n values of a column ranked by an aggregated metric."),
    "trend": (TrendArgs, trend, "Aggregate a metric per day/week/month/quarter/year of a date column."),
    "compare": (CompareArgs, compare, "Compare a numeric metric between two values of a column."),
    "correlation": (CorrelationArgs, correlation, "Pearson and Spearman correlation between two numeric columns."),
    "outliers": (OutliersArgs, outliers, "Find outliers in a numeric column with IQR or z-score."),
}


def _inline_refs(schema: dict) -> dict:
    """Inline $defs and drop titles so the schema is small and simple for the model."""
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def tool_specs() -> list[dict]:
    """Groq/OpenAI function-calling definitions."""
    return [
        {
            "type": "function",
            "function": {"name": name, "description": description, "parameters": _inline_refs(model.model_json_schema())},
        }
        for name, (model, _, description) in TOOLS.items()
    ]


def run_tool(df: pd.DataFrame, name: str, raw_args: str | dict | None) -> tuple[dict | None, dict]:
    """Validate and run one tool call.

    Returns (validated_args, result). On any problem result is {"error": ...} and
    validated_args is None, so the model can correct itself.
    """
    if name not in TOOLS:
        return None, {"error": f"Unknown tool {name!r}. Available: {', '.join(TOOLS)}"}
    model, func, _ = TOOLS[name]
    try:
        payload = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else (raw_args or {})
        args = model.model_validate(payload)
    except json.JSONDecodeError:
        return None, {"error": "Arguments must be a JSON object."}
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'args'}: {e['msg']}" for e in exc.errors())
        return None, {"error": f"Invalid arguments: {problems}"}
    try:
        return args.model_dump(), func(df, args)
    except ToolError as exc:
        return None, {"error": str(exc)}
