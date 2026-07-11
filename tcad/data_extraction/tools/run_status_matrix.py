from __future__ import annotations

import argparse
import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


OK = 0
INITIAL_FAILURE = 1
SWEEP_FAILURE = 2
MISSING = 3


@dataclass(frozen=True)
class Cell:
    category: int
    label: str
    detail: str


def _default_status_csv() -> Path:
    return Path(__file__).resolve().parents[1] / "dataset" / "run_status.csv"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Visualize run_status.csv as a matplotlib status matrix."
    )
    parser.add_argument(
        "status_csv",
        nargs="?",
        type=Path,
        default=_default_status_csv(),
        help="Input run_status CSV (default: dataset/run_status.csv)",
    )
    parser.add_argument(
        "--save",
        type=Path,
        help="Save the figure (for example, run_status_matrix.png)",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="Do not open the interactive window (useful with --save)",
    )
    return parser.parse_args()


def _sweep_failure_detail(row: dict[str, str]) -> tuple[str, str]:
    def point_count(name: str) -> int:
        try:
            return int((row.get(name) or "0").strip())
        except ValueError:
            return 0

    idvd_points = point_count("idvd_points")
    idvg_points = point_count("idvg_points")
    if idvg_points > 0:
        stage = "IdVg"
    elif idvd_points > 0:
        stage = "IdVd"
    else:
        stage = "Sweep"

    if stage == "IdVd":
        voltage_text = (row.get("final_drain_v") or "").strip()
        voltage_name = "Vd"
    elif stage == "IdVg":
        voltage_text = (row.get("final_gate_v") or "").strip()
        voltage_name = "Vg"
    else:
        voltage_text = ""
        voltage_name = "V"

    try:
        voltage = f"{float(voltage_text):g} V"
    except ValueError:
        voltage = "unknown"

    short = f"X\n{stage}\n{voltage}"
    detail = f"Sweep failure: {stage}, last converged {voltage_name}={voltage}"
    return short, detail


def _cell_from_row(row: dict[str, str]) -> Cell:
    status = (row.get("status") or "").strip().lower()
    structure_id = (row.get("structure_id") or "").strip()
    doping_run_id = (row.get("doping_run_id") or "").strip()
    prefix = f"{structure_id} / {doping_run_id}"
    if status == "ok":
        return Cell(OK, "O", f"{prefix}\nStatus: ok")

    try:
        point_count = int((row.get("ivpoint_count") or "0").strip())
    except ValueError:
        point_count = 0
    if point_count == 0:
        return Cell(
            INITIAL_FAILURE,
            "X",
            f"{prefix}\nInitial DC convergence failure (no IV points)",
        )

    label, detail = _sweep_failure_detail(row)
    return Cell(SWEEP_FAILURE, label, f"{prefix}\n{detail}")


def _load_cells(
    path: Path,
) -> tuple[list[str], list[str], dict[tuple[str, str], Cell], Counter[int]]:
    structures: list[str] = []
    doping_runs: list[str] = []
    cells: dict[tuple[str, str], Cell] = {}
    counts: Counter[int] = Counter()

    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        required = {"structure_id", "doping_run_id", "status"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")

        for line_number, row in enumerate(reader, start=2):
            structure_id = (row.get("structure_id") or "").strip()
            doping_run_id = (row.get("doping_run_id") or "").strip()
            if not structure_id or not doping_run_id:
                raise ValueError(f"Empty ID at line {line_number}")
            key = (structure_id, doping_run_id)
            if key in cells:
                raise ValueError(f"Duplicate pair at line {line_number}: {key}")
            if structure_id not in structures:
                structures.append(structure_id)
            if doping_run_id not in doping_runs:
                doping_runs.append(doping_run_id)
            cell = _cell_from_row(row)
            cells[key] = cell
            counts[cell.category] += 1

    return structures, doping_runs, cells, counts


def _plot_matrix(
    structures: list[str],
    doping_runs: list[str],
    cells: dict[tuple[str, str], Cell],
    counts: Counter[int],
):
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch

    values = np.full((len(structures), len(doping_runs)), MISSING, dtype=int)
    for y, structure_id in enumerate(structures):
        for x, doping_run_id in enumerate(doping_runs):
            cell = cells.get((structure_id, doping_run_id))
            if cell:
                values[y, x] = cell.category

    figure_width = max(18.0, len(doping_runs) * 1.2)
    figure_height = max(10.0, len(structures) * 0.31)
    fig, ax = plt.subplots(figsize=(figure_width, figure_height))
    colors = ["#cfe8ff", "#f6b26b", "#f4cccc", "#eeeeee"]
    ax.imshow(values, cmap=ListedColormap(colors), vmin=0, vmax=3, aspect="auto")

    ax.set_xticks(range(len(doping_runs)), labels=doping_runs, rotation=55, ha="right")
    ax.set_yticks(range(len(structures)), labels=structures)
    ax.set_xlabel("doping_run_id")
    ax.set_ylabel("structure_id")
    ax.set_title(
        "Run status matrix\n"
        f"OK {counts[OK]} | initial convergence failure {counts[INITIAL_FAILURE]} | "
        f"sweep failure {counts[SWEEP_FAILURE]}"
    )

    ax.set_xticks(np.arange(-0.5, len(doping_runs), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(structures), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=0.45)
    ax.tick_params(which="minor", bottom=False, left=False)

    for y, structure_id in enumerate(structures):
        for x, doping_run_id in enumerate(doping_runs):
            cell = cells.get((structure_id, doping_run_id))
            if not cell:
                continue
            if cell.category == SWEEP_FAILURE:
                ax.text(x, y, cell.label, ha="center", va="center", fontsize=5.5, color="#8b0000")
            else:
                ax.text(x, y, cell.label, ha="center", va="center", fontsize=5.5, color="#333333")

    legend = [
        Patch(facecolor=colors[OK], label="O: OK"),
        Patch(facecolor=colors[INITIAL_FAILURE], label="X: initial convergence failure"),
        Patch(facecolor=colors[SWEEP_FAILURE], label="X: sweep failure (last converged voltage shown)"),
        Patch(facecolor=colors[MISSING], label="Missing pair"),
    ]
    ax.legend(handles=legend, loc="upper left", bbox_to_anchor=(1.005, 1.0))

    annotation = ax.annotate(
        "",
        xy=(0, 0),
        xytext=(12, 12),
        textcoords="offset points",
        bbox={"boxstyle": "round", "fc": "white", "alpha": 0.95},
        arrowprops={"arrowstyle": "->"},
    )
    annotation.set_visible(False)

    def on_move(event) -> None:
        if event.inaxes is not ax or event.xdata is None or event.ydata is None:
            if annotation.get_visible():
                annotation.set_visible(False)
                fig.canvas.draw_idle()
            return
        x, y = int(round(event.xdata)), int(round(event.ydata))
        if not (0 <= x < len(doping_runs) and 0 <= y < len(structures)):
            return
        cell = cells.get((structures[y], doping_runs[x]))
        if cell is None:
            return
        annotation.xy = (x, y)
        annotation.set_text(cell.detail)
        annotation.set_visible(True)
        fig.canvas.draw_idle()

    fig.canvas.mpl_connect("motion_notify_event", on_move)
    fig.tight_layout()
    return fig


def main() -> None:
    args = _parse_args()
    structures, doping_runs, cells, counts = _load_cells(args.status_csv)
    fig = _plot_matrix(structures, doping_runs, cells, counts)

    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(args.save, dpi=180, bbox_inches="tight")
        print(f"Saved: {args.save.resolve()}")
    if not args.no_show:
        import matplotlib.pyplot as plt

        plt.show()


if __name__ == "__main__":
    main()
