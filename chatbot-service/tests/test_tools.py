import json
from pathlib import Path

import pandas as pd
import pytest

from app.tools import MAX_RESULT_ROWS, TOOLS, run_tool, tool_specs


@pytest.fixture
def df():
    return pd.DataFrame(
        {
            "Date": pd.to_datetime(
                ["2024-01-05", "2024-01-20", "2024-02-03", "2024-02-18", "2024-03-02", "2024-03-25", "2024-04-10", "2024-04-28"]
            ),
            "Region": ["North", "South", "North", "East", "South", "North", "East", "South"],
            "Product": ["A", "B", "A", "C", "B", "C", "A", "B"],
            "Revenue": [100.0, 200.0, 150.0, 120.0, 260.0, 180.0, 90.0, 1500.0],
            "Units": [10, 18, 14, 11, 25, 17, 9, 30],
        }
    )


def call(df, name, **args):
    validated, result = run_tool(df, name, json.dumps(args))
    assert "error" not in result, result
    assert validated is not None
    return result


def as_dict(result):
    return {row[0]: row[1:] if len(row) > 2 else row[1] for row in result["rows"]}


def test_describe_numeric_and_text(df):
    stats = as_dict(call(df, "describe_column", column="Revenue"))
    assert stats["sum"] == 2600 and stats["max"] == 1500 and stats["rows"] == 8
    assert stats["median"] == df["Revenue"].median()
    text = as_dict(call(df, "describe_column", column="Region"))
    assert text["unique"] == 3 and text["top: North"] == 3 and text["top: South"] == 3


def test_group_by_sum_with_share_and_filters(df):
    result = call(df, "group_by", column="Region", metric="Revenue", agg="sum")
    assert result["columns"] == ["Region", "sum(Revenue)", "share_pct"]
    assert result["rows"][0][:2] == ["South", 1960]  # sorted largest first
    assert round(sum(r[2] for r in result["rows"])) == 100

    filtered = call(
        df, "group_by", column="Product", metric="Revenue", agg="mean",
        filters=[{"column": "Region", "op": "in", "value": ["north", "EAST"]}],  # case-insensitive
    )
    assert as_dict(filtered) == {"A": pytest.approx(113.3333), "C": 150}


def test_group_by_count_without_metric(df):
    result = call(df, "group_by", column="Region", agg="count")
    assert result["columns"][1] == "count" and as_dict(result)["East"] == [2, 25]


def test_filter_operators(df):
    def total(filters):
        return as_dict(call(df, "describe_column", column="Revenue", filters=filters))["sum"]

    assert total([{"column": "Units", "op": ">", "value": 20}]) == 1760
    assert total([{"column": "Units", "op": "<=", "value": 10}]) == 190
    assert total([{"column": "Revenue", "op": "between", "value": [100, 200]}]) == 750
    assert total([{"column": "Region", "op": "!=", "value": "South"}]) == 640
    assert total([{"column": "Date", "op": ">=", "value": "2024-04-01"}]) == 1590
    assert total([{"column": "Region", "op": "==", "value": "North"}, {"column": "Product", "op": "==", "value": "C"}]) == 180


def test_top_n_and_bottom_n(df):
    top = call(df, "top_n", column="Product", metric="Revenue", n=2)
    assert [r[0] for r in top["rows"]] == ["B", "A"]
    bottom = call(df, "top_n", column="Product", metric="Units", n=1, order="asc", agg="sum")
    assert bottom["rows"] == [["C", 28]]


def test_trend_monthly(df):
    result = call(df, "trend", date_column="Date", metric="Revenue", freq="M", agg="sum")
    assert result["rows"] == [["2024-01", 300], ["2024-02", 270], ["2024-03", 440], ["2024-04", 1590]]
    assert "change 430%" in result["note"]
    quarterly = call(df, "trend", date_column="Date", freq="Q", agg="count")
    assert quarterly["rows"] == [["2024Q1", 6], ["2024Q2", 2]]


def test_compare_two_groups(df):
    stats = as_dict(call(df, "compare", column="Region", group_a="North", group_b="south", metric="Revenue"))
    assert stats["count"][:2] == [3, 3]
    assert stats["sum"][:3] == [430, 1960, -1530]
    assert stats["mean"][3] == pytest.approx(-78.0612, abs=1e-3)


def test_correlation(df):
    stats = as_dict(call(df, "correlation", col_a="Units", col_b="Revenue"))
    assert stats["pearson_r"] == pytest.approx(df["Units"].corr(df["Revenue"]), abs=1e-4)
    assert stats["n"] == 8 and stats["strength"].endswith("positive")


def test_outliers_iqr_and_zscore(df):
    result = call(df, "outliers", column="Revenue", method="iqr")
    assert result["columns"] == ["row", "Region", "Revenue", "distance_from_median"]
    assert result["rows"][0][:3] == [8, "South", 1500]
    assert result["note"].startswith("1 outliers of 8 values")

    big = pd.DataFrame({"x": [10.0] * 30 + [11.0] * 30 + [500.0]})
    z = call(big, "outliers", column="x", method="zscore")
    assert len(z["rows"]) == 1 and z["rows"][0][1] == 500


def test_results_are_capped_at_50_rows():
    wide = pd.DataFrame({"k": [f"g{i}" for i in range(80)], "v": range(80)})
    result = call(wide, "group_by", column="k", metric="v")
    assert len(result["rows"]) == MAX_RESULT_ROWS and "showing 50 of 80 rows" in result["note"]


def chart(df, **args):
    result = call(df, "create_chart", **args)
    return result.pop("chart"), result


def test_create_chart_bar_matches_the_saved_chart_shape(df):
    config, result = chart(df, title=" Revenue by region ", chart_type="bar", x_field="region", y_fields=["Revenue", "Units"])
    assert config == {
        "title": "Revenue by region",
        "chartType": "bar",
        "mappings": {"xField": "Region", "yFields": ["Revenue", "Units"], "stacked": False},
        "options": {"aggregation": "sum", "topN": 0},
    }
    # Groups keep first-appearance order, like buildChartData on the dashboard
    assert result["columns"] == ["Region", "sum(Revenue)", "sum(Units)"]
    assert result["rows"] == [["North", 430, 41], ["South", 1960, 73], ["East", 210, 20]]
    assert result["note"].startswith("bar chart ready (3 Region groups)")


def test_create_chart_line_with_avg_top_n_and_raw_rows(df):
    config, result = chart(
        df, title="Top regions", chart_type="line", x_field="Region", y_fields=["Revenue"], aggregation="avg", top_n=2, stacked=True
    )
    assert config["mappings"]["stacked"] is False  # stacking is only for bar/area
    assert config["options"] == {"aggregation": "avg", "topN": 2}
    assert result["rows"] == [["South", pytest.approx(653.3333)], ["North", pytest.approx(143.3333)]]

    config, result = chart(df, title="Revenue over time", chart_type="area", x_field="Date", y_fields=["Revenue"], aggregation="none")
    assert config["options"]["aggregation"] == "none"
    assert result["rows"][0] == ["2024-01-05", 100] and len(result["rows"]) == 8


def test_create_chart_scatter_donut_and_radar(df):
    config, result = chart(df, chart_type="scatter", x_field="Units", y_fields=["Revenue"], aggregation="sum")
    assert config["mappings"] == {"xField": "Units", "yField": "Revenue"} and config["title"] == "Revenue vs Units"
    assert config["options"]["aggregation"] == "none" and len(result["rows"]) == 8

    config, result = chart(df, title="Share", chart_type="donut", x_field="Product", y_fields=["Revenue"], aggregation="none")
    assert config["mappings"] == {"categoryField": "Product", "valueField": "Revenue", "donut": True}
    assert config["options"]["aggregation"] == "sum"  # pie slices are always combined
    assert as_dict(result) == {"A": 340, "B": 1960, "C": 300}

    config, _ = chart(df, chart_type="radar", x_field="Product", y_fields=["Units"])
    assert config["mappings"] == {"angleField": "Product", "radiusField": "Units"} and config["title"] == "Units by Product"


def test_create_chart_rejects_pies_with_too_many_slices():
    wide = pd.DataFrame({"k": [f"g{i}" for i in range(30)], "v": range(30)})
    _, result = run_tool(wide, "create_chart", {"chart_type": "pie", "x_field": "k", "y_fields": ["v"]})
    assert "30 distinct values" in result["error"]
    config, _ = chart(wide, chart_type="pie", x_field="k", y_fields=["v"], top_n=10)
    assert config["options"]["topN"] == 10


@pytest.mark.parametrize(
    "name,args,message",
    [
        ("group_by", {"column": "Regoin", "metric": "Revenue"}, "Did you mean 'Region'"),
        ("group_by", {"column": "Region", "metric": "Product"}, "not numeric"),
        ("group_by", {"column": "Region", "agg": "mean"}, "needs a metric"),
        ("trend", {"date_column": "Region", "metric": "Revenue"}, "not a date"),
        ("describe_column", {"column": "Revenue", "filters": [{"column": "Region", "op": ">", "value": "N"}]}, "text"),
        ("describe_column", {"column": "Revenue", "filters": [{"column": "Units", "op": "==", "value": "lots"}]}, "must be a number"),
        ("describe_column", {"column": "Revenue", "filters": [{"column": "Units", "op": "like", "value": 1}]}, "Invalid arguments"),
        ("describe_column", {"column": "Revenue", "filters": [{"column": "Units", "op": "between", "value": [1]}]}, "between"),
        ("describe_column", {"column": "Revenue", "filters": [{"column": "Units", "op": ">", "value": 999}]}, "No rows match"),
        ("compare", {"column": "Region", "group_a": "North", "group_b": "Mars", "metric": "Revenue"}, "Some values"),
        ("group_by", {"column": "Region", "metric": "Revenue", "code": "import os"}, "Extra inputs"),
        ("create_chart", {"chart_type": "bar", "x_field": "Region", "y_fields": []}, "Invalid arguments"),
        ("create_chart", {"title": "t", "chart_type": "bar", "x_field": "Regoin", "y_fields": ["Revenue"]}, "Did you mean"),
        ("create_chart", {"title": "t", "chart_type": "bar", "x_field": "Revenue", "y_fields": ["Revenue"]}, "can't be both"),
        ("create_chart", {"chart_type": "pie", "x_field": "Region", "y_fields": ["Product"]}, "not numeric"),
        ("create_chart", {"chart_type": "pie", "x_field": "Region", "y_fields": ["Revenue", "Units"]}, "exactly one"),
        ("create_chart", {"chart_type": "scatter", "x_field": "Region", "y_fields": ["Revenue"]}, "not numeric"),
        ("create_chart", {"chart_type": "heatmap", "x_field": "Region", "y_fields": ["Revenue"]}, "Invalid arguments"),
        (
            "create_chart",
            {"title": "t", "chart_type": "bar", "x_field": "Region", "y_fields": ["Revenue"], "aggregation": "none", "top_n": 2},
            "top_n only works",
        ),
        ("drop_table", {}, "Unknown tool"),
    ],
)
def test_invalid_calls_return_errors_for_the_model(df, name, args, message):
    validated, result = run_tool(df, name, json.dumps(args))
    assert validated is None and message in result["error"]


def test_malformed_json_arguments(df):
    assert run_tool(df, "group_by", "{not json")[1] == {"error": "Arguments must be a JSON object."}


def test_injection_strings_are_just_values(df):
    payload = "__import__('os').system('echo hacked') or True"
    validated, result = run_tool(
        df, "describe_column", json.dumps({"column": "Revenue", "filters": [{"column": "Region", "op": "==", "value": payload}]})
    )
    assert result == {"error": "No rows match these filters."}
    validated, result = run_tool(df, "group_by", json.dumps({"column": payload, "metric": "Revenue"}))
    assert "Unknown column" in result["error"]


def test_tools_module_never_uses_eval_exec_or_query():
    source = (Path(__file__).parents[1] / "app" / "tools.py").read_text(encoding="utf-8")
    for banned in ("eval(", "exec(", ".query(", ".eval("):
        assert banned not in source


def test_tool_specs_are_plain_json_schema():
    specs = tool_specs()
    assert [s["function"]["name"] for s in specs] == list(TOOLS)
    text = json.dumps(specs)
    assert "$ref" not in text and "$defs" not in text
    filters = specs[1]["function"]["parameters"]["properties"]["filters"]
    assert filters["items"]["properties"]["op"]["enum"] == ["==", "!=", ">", ">=", "<", "<=", "in", "between"]
