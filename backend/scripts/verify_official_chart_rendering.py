"""Execute only unchanged scripts returned by this run's local CSV service.

Check the actual Matplotlib objects and saved figures, not only script text.
No external data, user-supplied Python, or cloud calls are executed.
"""

import argparse
import json
import math
import os
import runpy
import warnings
from pathlib import Path

from official_functional_inputs import TEMP_ROOT, digest, write_json


def run(args):
    root = args.inputs.resolve()
    if not root.is_relative_to(TEMP_ROOT) or root == TEMP_ROOT:
        raise ValueError("Requires isolated CSV API acceptance outputs")
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    if report["status"] != "passed":
        raise ValueError("Numerical API acceptance must pass first")
    os.environ["MPLBACKEND"] = "Agg"
    os.environ["MPLCONFIGDIR"] = str(root / "matplotlib-cache")
    import matplotlib.pyplot as plt

    from app.services.csv_visualization import analyze_csv

    cases = []
    for row in report["cases"]:
        name = row["case"]
        accepted = json.loads((root / (name + ".json")).read_text(encoding="utf-8"))
        expected = accepted["chart"]
        reproduced = analyze_csv(
            (root / (name + ".csv")).read_bytes(),
            name + ".csv",
            expected["chart_type"],
            x_column=expected["x_column"],
            y_columns=expected["y_columns"],
            group_column=expected["group_column"],
            aggregation=expected["aggregation"],
            error_mode=expected["error_mode"],
        )
        script = root / (name + ".py")
        if script.read_text(encoding="utf-8") != reproduced.chart.matplotlib_script:
            raise ValueError("Exported script differs from the current local service")
        out = root / "rendered" / name
        out.mkdir(parents=True, exist_ok=False)
        original_cwd = Path.cwd()
        try:
            os.chdir(out)
            with warnings.catch_warnings(record=True) as recorded:
                warnings.simplefilter("always")
                values = runpy.run_path(str(script))
            ax = values["ax"]
            png = out / "scientific_figure.png"
            checks = {
                "actual_png_nonempty": png.is_file() and png.stat().st_size > 10000,
                "title_matches": ax.get_title() == expected["title"],
                "no_missing_glyph_warnings": not any("Glyph" in str(w.message) for w in recorded),
            }
            if expected["chart_type"] == "line":
                line_values = list(ax.get_lines()[0].get_ydata())
                wanted = expected["series"][0]["values"]
                checks["actual_line_values"] = len(line_values) == len(wanted) and all(
                    math.isnan(a) if b is None else math.isclose(a, b)
                    for a, b in zip(line_values, wanted, strict=True)
                )
                if expected.get("x_coordinates"):
                    checks["actual_x_spacing"] = (
                        list(ax.get_lines()[0].get_xdata()) == expected["x_coordinates"]
                    )
            elif expected["chart_type"] == "radar3d":
                data_lines = [
                    line
                    for line in ax.get_lines()
                    if line.get_label() in {s["name"] for s in expected["series"]}
                ]
                checks["all_records_drawn"] = len(data_lines) == len(expected["series"])
                checks["reference_rings_and_spokes"] = len(ax.get_lines()) - len(data_lines) == (
                    len(expected["series"]) * (2 + len(expected["categories"]))
                )
                checks["normalization_and_layer_disclosure"] = any(
                    "min-max" in text.get_text() and "层高仅区分记录" in text.get_text()
                    for text in values["fig"].texts
                )
                checks["no_missing_record_area"] = len(ax.collections) == sum(
                    all(v is not None for v in s["values"]) for s in expected["series"]
                )
                if name == "synthetic-missing-duplicate":
                    checks["actual_nan_gap"] = any(
                        math.isnan(v) for v in data_lines[1].get_data_3d()[0]
                    )
            else:
                plotted = [float(bar.get_height()) for bar in ax.patches]
                wanted = [v for s in expected["series"] for v in s["values"]]
                checks["actual_bar_values"] = len(plotted) == len(wanted) and all(
                    math.isnan(a) if b is None else math.isclose(a, b)
                    for a, b in zip(plotted, wanted, strict=True)
                )
                checks["zero_baseline"] = ax.get_ylim()[0] <= 0 <= ax.get_ylim()[1]
            checks = {key: bool(value) for key, value in checks.items()}
            cases.append(
                {
                    "case": name,
                    "checks": checks,
                    "passed": all(checks.values()),
                    "png_sha256": digest(png),
                    "exported_script_sha256": digest(script),
                }
            )
        finally:
            os.chdir(original_cwd)
            plt.close("all")
    result = {
        "status": "passed" if all(x["passed"] for x in cases) else "failed",
        "cases": cases,
        "scope": "Actual generated Matplotlib objects and PNG; visual review recorded separately",
        "cloud_calls": 0,
    }
    write_json(root / "rendered-report.json", result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    raise SystemExit(run(parser.parse_args()))
