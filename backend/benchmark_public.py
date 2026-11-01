"""Read-only public benchmark profiler. Never substitutes unknown warehouse facts."""
from __future__ import annotations

import argparse
import json
import time
import zipfile
from pathlib import Path

from .benchmark_adapters import footwear_projection, slaprp_projection


def profile(footwear: Path, slaprp: Path) -> dict:
    from openpyxl import load_workbook

    start = time.perf_counter()
    footwear_runs = []
    for limit in (100, 1000, None):
        before = time.perf_counter()
        mapped = footwear_projection(footwear, order_limit=limit)
        footwear_runs.append({"scope": limit or "all", "counts": mapped["counts"],
                              "runtime_s": round(time.perf_counter() - before, 3),
                              "canonical_full_snapshot_feasible": False,
                              "assignment_coverage": None, "objective_gap": None,
                              "travel_improvement": None, "missing": mapped["unknown"]})
    book = load_workbook(slaprp / "SLAPRP_detailed_results.xlsx", read_only=True, data_only=True)
    published = {row[0]: {"published_upper_bound": row[8], "published_lower_bound": row[9],
                          "published_gap": row[10], "published_runtime_s": row[11]}
                 for row in list(book["benchmark_guo"].values)[1:] if isinstance(row[0], str)}
    published_silva = {row[0]: {"published_upper_bound": row[8], "published_lower_bound": row[9],
                                "published_gap": row[10], "published_runtime_s": row[11]}
                       for row in list(book["benchmark_silva"].values)[1:] if isinstance(row[0], str)}
    book.close()
    runs = []
    with zipfile.ZipFile(slaprp / "SLAPRP_instances.zip") as archive:
        members = sorted(name for name in archive.namelist() if name.endswith(".txt") and "instances_Guo_2021/" in name)
    for member in members:
        before = time.perf_counter()
        mapped = slaprp_projection(slaprp / "SLAPRP_instances.zip", member)
        count = mapped["counts"]
        runs.append({"instance": Path(member).name, "counts": count,
                     "runtime_s": round(time.perf_counter() - before, 4),
                     "fixed_assignment_coverage": round(count["fixed_assignments"] / count["skus"], 4),
                     "canonical_full_snapshot_feasible": False, "slotwise_assignment_coverage": None,
                     "slotwise_objective_gap": None, "slotwise_travel_improvement": None,
                     "published": published.get(Path(member).name)})
    with zipfile.ZipFile(slaprp / "Silva_Instances.zip") as archive:
        selected = []
        for size in ("Small", "Regular"):
            selected.extend(sorted(name for name in archive.namelist()
                                   if name.startswith(f"Instances/{size}/") and name.endswith(".txt"))[:30])
    for member in selected:
        before = time.perf_counter()
        mapped = slaprp_projection(slaprp / "Silva_Instances.zip", member)
        count = mapped["counts"]
        runs.append({"instance": Path(member).name, "family": member.split("/")[1], "counts": count,
                     "runtime_s": round(time.perf_counter() - before, 4),
                     "fixed_assignment_coverage": round(count["fixed_assignments"] / count["skus"], 4),
                     "canonical_full_snapshot_feasible": False, "slotwise_assignment_coverage": None,
                     "slotwise_objective_gap": None, "slotwise_travel_improvement": None,
                     "published": published_silva.get(Path(member).name)})
    return {"footwear": footwear_runs, "slaprp": runs, "total_runtime_s": round(time.perf_counter() - start, 3),
            "comparison_note": "Published SLAPRP routing objective is not the Slotwise depot round-trip proxy. Missing physical and safety fields prevent a valid canonical optimization run; no cross-objective gap is reported."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--footwear", type=Path, default=Path("data/benchmarks/footwear"))
    parser.add_argument("--slaprp", type=Path, default=Path("data/benchmarks/slaprp"))
    parser.add_argument("--output", type=Path, default=Path("data/benchmarks/profile.json"))
    args = parser.parse_args()
    report = profile(args.footwear, args.slaprp)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Profiled {len(report['slaprp'])} SLAPRP instances and {len(report['footwear'])} footwear scopes in {report['total_runtime_s']} s")
    print(report["comparison_note"])
