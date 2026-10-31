"""Read uploaded CSV/XLSX/JSON into a typed DataFrame.

Column types use the frontend's vocabulary ("number", "date", "string") so the
chart builder and preview table work unchanged.
"""

import csv
import io
import json
import math
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

COLUMN_TYPES = ("number", "date", "string")

# A date-like value has a digit and a separator ("2024-01-05", "5/1/24", "Jan 5 2024").
# Bare words such as "Jan" or "Q1" never become dates.
_HAS_DIGIT = re.compile(r"\d")
_HAS_DATE_SEPARATOR = re.compile(r"[-/:.\s]")
_DATE_MIN_PARSED = 0.95


class IngestError(ValueError):
    """The upload can't be turned into a table; the message is shown to the user."""


def load_dataframe(filename: str, content: bytes, first_row_header: bool = True) -> tuple[pd.DataFrame, list[str]]:
    df = read_table(filename, content, first_row_header)
    return infer_types(df)


def read_table(filename: str, content: bytes, first_row_header: bool = True) -> pd.DataFrame:
    if not content:
        raise IngestError("The file is empty.")
    ext = Path(filename or "").suffix.lower()
    if ext == ".csv":
        df = _apply_header(_drop_empty_rows(_read_csv(content)), first_row_header)
    elif ext == ".xlsx":
        df = _apply_header(_drop_empty_rows(_read_xlsx(content)), first_row_header)
    elif ext == ".json":
        df = _read_json(content)
        df.columns = _unique_headers(list(df.columns))
        df = _drop_empty_rows(df)
    elif ext == ".xls":
        raise IngestError("Legacy .xls files aren't supported. Save the sheet as .xlsx or .csv and upload again.")
    else:
        raise IngestError("Unsupported file type. Upload a CSV, XLSX or JSON file.")

    if df.empty or len(df.columns) == 0:
        raise IngestError("No data rows found in the file.")
    return df


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1")


def _read_csv(content: bytes) -> pd.DataFrame:
    text = _decode(content)
    try:
        delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = ","
    try:
        # Header row is handled by _apply_header; everything is read as text and
        # infer_types applies one set of rules to every format
        return pd.read_csv(io.StringIO(text), sep=delimiter, header=None, dtype=str, skip_blank_lines=True)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise IngestError(f"Could not parse the CSV file: {exc}") from exc


def _read_xlsx(content: bytes) -> pd.DataFrame:
    try:
        return pd.read_excel(io.BytesIO(content), sheet_name=0, header=None, engine="openpyxl")
    except Exception as exc:  # openpyxl raises several unrelated types for bad files
        raise IngestError(f"Could not read the Excel file: {exc}") from exc


def _read_json(content: bytes) -> pd.DataFrame:
    try:
        parsed = json.loads(_decode(content))
    except json.JSONDecodeError as exc:
        raise IngestError(f"Invalid JSON: {exc.msg} (line {exc.lineno}).") from exc
    records = parsed.get("data") if isinstance(parsed, dict) else parsed
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise IngestError("JSON must be an array of objects (or an object with a 'data' array).")
    df = pd.DataFrame.from_records(records)
    # Nested objects/arrays become JSON text so every cell is a scalar
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, (dict, list))).any():
            df[col] = df[col].map(lambda v: json.dumps(v) if isinstance(v, (dict, list)) else v)
    return df


def _unique_headers(names: list) -> list[str]:
    """Blank headers become "Field N"; repeats become "name (2)", "name (3)"."""
    headers: list[str] = []
    seen: dict[str, int] = {}
    for idx, value in enumerate(names):
        name = "" if value is None or (isinstance(value, float) and math.isnan(value)) else str(value).strip()
        name = name or f"Field {idx + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        headers.append(name)
    return headers


def _drop_empty_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows where every cell is empty, like the browser parser does."""
    if df.empty:
        return df
    blank = df.apply(lambda s: s.isna() | (s.astype(str).str.strip() == ""))
    return df.loc[~blank.all(axis=1)].reset_index(drop=True)


def _apply_header(df: pd.DataFrame, first_row_header: bool) -> pd.DataFrame:
    if first_row_header and len(df):
        names = df.iloc[0].tolist()
        df = df.iloc[1:].reset_index(drop=True)
    else:
        names = [None] * len(df.columns)
    df = df.copy()
    df.columns = _unique_headers(names)
    # Excel columns were object while the header row was part of them; restore real dtypes
    return df.infer_objects()


def _text_values(series: pd.Series) -> pd.Series:
    values = series.dropna().astype(str).str.strip()
    return values[values != ""]


def _parse_dates(series: pd.Series, values: pd.Series) -> pd.Series | None:
    sample = values.head(1000)
    if not (sample.str.contains(_HAS_DIGIT).all() and sample.str.contains(_HAS_DATE_SEPARATOR).all()):
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if pd.to_datetime(sample, errors="coerce", format="mixed", utc=True).notna().mean() < _DATE_MIN_PARSED:
            return None
        text = series.astype("string").str.strip().replace("", pd.NA)
        # Fast path: one inferred format for the whole column; fall back to per-value parsing
        parsed = pd.to_datetime(text, errors="coerce", utc=True)
        if parsed[text.notna()].notna().mean() < _DATE_MIN_PARSED:
            parsed = pd.to_datetime(text, errors="coerce", format="mixed", utc=True)
    return parsed.dt.tz_convert(None)


def infer_types(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Return a copy with typed columns plus the per-column type names."""
    out = df.copy()
    types: list[str] = []
    for col in out.columns:
        series = out[col]
        if pd.api.types.is_bool_dtype(series):
            out[col] = series.map({True: "true", False: "false"}).astype("string")
            types.append("string")
        elif pd.api.types.is_numeric_dtype(series):
            out[col] = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
            types.append("number")
        elif pd.api.types.is_datetime64_any_dtype(series):
            out[col] = series.dt.tz_convert(None) if series.dt.tz is not None else series
            types.append("date")
        else:
            values = _text_values(series)
            numeric = pd.to_numeric(values, errors="coerce") if not values.empty else values
            if not values.empty and numeric.notna().all():
                text = series.astype("string").str.strip().replace("", pd.NA)
                out[col] = pd.to_numeric(text, errors="coerce").replace([np.inf, -np.inf], np.nan)
                types.append("number")
                continue
            parsed = _parse_dates(series, values) if not values.empty else None
            if parsed is not None:
                out[col] = parsed
                types.append("date")
            else:
                out[col] = series.map(lambda v: v if pd.isna(v) else str(v)).astype("string")
                types.append("string")
    return out, types


def rows_to_json(df: pd.DataFrame) -> str:
    """Serialise rows as a JSON array (NaN -> null, dates -> ISO 8601)."""
    return df.to_json(orient="records", date_format="iso", date_unit="s", force_ascii=False)


def preview_records(df: pd.DataFrame, max_rows: int, max_bytes: int = 400_000) -> list[dict]:
    """First rows as plain Python values, shrunk until they fit comfortably in a Firestore doc."""
    rows = max_rows
    while rows > 0:
        payload = rows_to_json(df.head(rows))
        if len(payload.encode("utf-8")) <= max_bytes:
            return json.loads(payload)
        rows //= 2
    return []
