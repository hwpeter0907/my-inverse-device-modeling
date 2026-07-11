from __future__ import annotations

import argparse
import csv
import re
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk

import matplotlib

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure


@dataclass
class CurveSet:
    stem: str
    structure_id: str
    doping_run_id: str
    idvd_csv: Path
    idvg_csv: Path


@dataclass
class CurveParams:
    L: str
    T: str
    B: str
    SD: str
    LDD: str


def _resolve_default_dataset(script_file: Path) -> Path:
    return script_file.resolve().parents[1] / "dataset"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive I-V CSV visualization")
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=_resolve_default_dataset(Path(__file__)),
        help="Directory containing *_IdVd.csv and *_IdVg.csv files",
    )
    return parser.parse_args()


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _split_curve_stem(stem: str) -> tuple[str, str]:
    match = re.match(r"(?P<structure>.+?)(?P<doping>B.+)$", stem)
    if not match:
        return stem, stem
    return match.group("structure"), match.group("doping")


def _discover_curve_sets(dataset_dir: Path) -> list[CurveSet]:
    idvd_files = {p.stem.removesuffix("_IdVd"): p for p in dataset_dir.glob("*_IdVd.csv")}
    idvg_files = {p.stem.removesuffix("_IdVg"): p for p in dataset_dir.glob("*_IdVg.csv")}
    stems = sorted(set(idvd_files) & set(idvg_files))
    curve_sets: list[CurveSet] = []
    for stem in stems:
        idvd_csv = idvd_files[stem]
        idvg_csv = idvg_files[stem]
        rows = _read_rows(idvd_csv) or _read_rows(idvg_csv)
        if not rows:
            continue
        first = rows[0]
        structure_id, doping_run_id = _split_curve_stem(stem)
        curve_sets.append(
            CurveSet(
                stem=stem,
                structure_id=(first.get("structure_id") or "").strip() or structure_id,
                doping_run_id=(first.get("doping_run_id") or "").strip() or doping_run_id,
                idvd_csv=idvd_csv,
                idvg_csv=idvg_csv,
            )
        )
    return curve_sets


def _parse_structure_id(structure_id: str) -> dict[str, str]:
    match = re.search(r"L(?P<L>[^T]+)T(?P<T>[^S]+)(?:S(?P<S>.+))?$", structure_id)
    if not match:
        return {"L": structure_id, "T": ""}
    return {
        "L": match.group("L") or "",
        "T": match.group("T") or "",
    }


def _parse_doping_id(doping_id: str) -> dict[str, str]:
    match = re.search(r"B(?P<B>.+?)SD(?P<SD>.+?)(?:LDD(?P<LDD>.+))?$", doping_id)
    if not match:
        return {"B": doping_id, "SD": "", "LDD": ""}
    return {
        "B": match.group("B") or "",
        "SD": match.group("SD") or "",
        "LDD": match.group("LDD") or "",
    }


def _curve_params(curve_set: CurveSet) -> CurveParams:
    structure = _parse_structure_id(curve_set.structure_id)
    doping = _parse_doping_id(curve_set.doping_run_id)
    return CurveParams(
        L=structure["L"],
        T=structure["T"],
        B=doping["B"],
        SD=doping["SD"],
        LDD=doping["LDD"],
    )


def _group_rows(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["curve_tag"], []).append(row)
    return grouped


def _float(row: dict[str, str], name: str) -> float:
    return float(row[name])


def _positive_abs(values: list[float]) -> list[float]:
    floor = 1e-30
    return [max(abs(value), floor) for value in values]


def _natural_value_key(value: str) -> tuple[int, float | str]:
    try:
        return (0, float(value.replace("p", ".")))
    except ValueError:
        return (1, value)


def _plot_curve(
    axis,
    rows: list[dict[str, str]],
    x_name: str,
    title: str,
    x_label: str,
    log_scale: bool,
    label: str,
    color: str,
) -> None:
    if not rows:
        axis.set_title(title)
        axis.grid(True, alpha=0.3)
        return

    sorted_rows = sorted(rows, key=lambda row: _float(row, x_name))
    xs = [_float(row, x_name) for row in sorted_rows]
    ys = [_float(row, "drain_current") for row in sorted_rows]
    if log_scale:
        ys = _positive_abs(ys)
        axis.set_yscale("log")
        y_label = "|Drain current| (mA/um)"
    else:
        y_label = "Drain current (mA/um)"

    tag = sorted_rows[0].get("curve_tag", "")
    axis.plot(xs, ys, linewidth=1.8, label=f"{label} {tag}", color=color)
    axis.set_title(title)
    axis.set_xlabel(x_label)
    axis.set_ylabel(y_label)
    axis.grid(True, alpha=0.3)


class IVVisualizationApp:
    PARAMS = ("L", "T", "B", "SD", "LDD")

    def __init__(self, root: tk.Tk, dataset_dir: Path) -> None:
        self.root = root
        self.dataset_dir = dataset_dir
        self.curve_sets: list[CurveSet] = []
        self.param_cache: dict[str, CurveParams] = {}
        self.curve1_vars = {name: tk.StringVar() for name in self.PARAMS}
        self.curve2_vars = {name: tk.StringVar() for name in self.PARAMS}
        self.axis_mode = tk.StringVar(value="Single Y")
        self.show_curve2 = False
        self.status_text = tk.StringVar()

        root.title("I-V Visualization")
        root.geometry("1700x900")

        controls = ttk.Frame(root, padding=(10, 8))
        controls.pack(side=tk.TOP, fill=tk.X)

        curve1_row = ttk.Frame(controls)
        curve1_row.pack(side=tk.TOP, fill=tk.X, pady=(0, 5))
        curve2_row = ttk.Frame(controls)
        curve2_row.pack(side=tk.TOP, fill=tk.X)

        self.curve1_combos = self._build_curve_controls(curve1_row, "Curve 1", self.curve1_vars)
        self.curve2_combos = self._build_curve_controls(curve2_row, "Curve 2", self.curve2_vars)

        ttk.Button(curve2_row, text="Visualize", command=self.visualize).pack(side=tk.LEFT, padx=(8, 8))
        self.axis_combo = ttk.Combobox(
            curve2_row,
            textvariable=self.axis_mode,
            values=["Single Y", "Dual Y"],
            state="readonly",
            width=9,
        )
        self.axis_combo.pack(side=tk.LEFT, padx=(0, 8))
        self.axis_combo.bind("<<ComboboxSelected>>", lambda _event: self.plot_selected())
        ttk.Label(curve2_row, textvariable=self.status_text).pack(side=tk.LEFT, padx=(4, 0))

        self.figure = Figure(figsize=(15, 8), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=root)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        toolbar_frame = ttk.Frame(root)
        toolbar_frame.pack(side=tk.BOTTOM, fill=tk.X)
        NavigationToolbar2Tk(self.canvas, toolbar_frame)

        self.refresh()

    def _build_curve_controls(
        self,
        parent,
        label: str,
        vars_by_name: dict[str, tk.StringVar],
    ) -> dict[str, ttk.Combobox]:
        ttk.Label(parent, text=label, width=8).pack(side=tk.LEFT, padx=(0, 4))
        combos: dict[str, ttk.Combobox] = {}
        for name in self.PARAMS:
            ttk.Label(parent, text=name).pack(side=tk.LEFT)
            combo = ttk.Combobox(
                parent,
                textvariable=vars_by_name[name],
                state="readonly",
                width=7,
            )
            combo.pack(side=tk.LEFT, padx=(3, 6))
            combo.bind("<<ComboboxSelected>>", lambda _event: self._selection_changed())
            combos[name] = combo
        return combos

    def refresh(self) -> None:
        self.curve_sets = _discover_curve_sets(self.dataset_dir)
        self.param_cache = {curve_set.stem: _curve_params(curve_set) for curve_set in self.curve_sets}
        self._refresh_param_values(self.curve1_vars, self.curve1_combos)
        self._refresh_param_values(self.curve2_vars, self.curve2_combos)
        self.show_curve2 = False
        self.status_text.set(f"{len(self.curve_sets)} set(s) in {self.dataset_dir}")
        self.plot_selected()

    def _refresh_param_values(
        self,
        vars_by_name: dict[str, tk.StringVar],
        combos_by_name: dict[str, ttk.Combobox],
    ) -> None:
        for name in self.PARAMS:
            values = sorted(
                {getattr(params, name) for params in self.param_cache.values() if getattr(params, name)},
                key=_natural_value_key,
            )
            combos_by_name[name]["values"] = values
            if vars_by_name[name].get() not in values:
                vars_by_name[name].set(values[0] if values else "")

    def _selection_changed(self) -> None:
        self.show_curve2 = False
        self.plot_selected()

    def _selected_curve_set(self, vars_by_name: dict[str, tk.StringVar]) -> CurveSet | None:
        selected = {name: vars_by_name[name].get() for name in self.PARAMS}
        for curve_set in self.curve_sets:
            params = self.param_cache[curve_set.stem]
            if all(getattr(params, name) == selected[name] for name in self.PARAMS):
                return curve_set
        return None

    def visualize(self) -> None:
        self.show_curve2 = self._selected_curve_set(self.curve2_vars) is not None
        self.plot_selected()

    def _plot_curve_set(self, curve_set: CurveSet, axes, label: str, color: str, twin: bool) -> None:
        idvd_groups = _group_rows(_read_rows(curve_set.idvd_csv))
        idvg_groups = _group_rows(_read_rows(curve_set.idvg_csv))

        idvd_tags = sorted(idvd_groups)
        idvd_tag_0 = idvd_tags[0] if len(idvd_tags) > 0 else ""
        idvd_tag_1 = idvd_tags[1] if len(idvd_tags) > 1 else ""
        idvg_tags = sorted(idvg_groups)
        idvg_tag_0 = idvg_tags[0] if len(idvg_tags) > 0 else ""
        idvg_tag_1 = idvg_tags[1] if len(idvg_tags) > 1 else ""

        plots = [
            (idvd_groups.get(idvd_tag_0, []), "drain_v", f"IdVd {idvd_tag_0}", "Vd (V)"),
            (idvd_groups.get(idvd_tag_1, []), "drain_v", f"IdVd {idvd_tag_1}", "Vd (V)"),
            (idvg_groups.get(idvg_tag_0, []), "gate_v", f"IdVg {idvg_tag_0}", "Vg (V)"),
            (idvg_groups.get(idvg_tag_1, []), "gate_v", f"IdVg {idvg_tag_1}", "Vg (V)"),
        ]

        for col, (rows, x_name, title, x_label) in enumerate(plots):
            top_axis = axes[0][col].twinx() if twin else axes[0][col]
            bottom_axis = axes[1][col].twinx() if twin else axes[1][col]
            _plot_curve(top_axis, rows, x_name, title, x_label, log_scale=False, label=label, color=color)
            _plot_curve(bottom_axis, rows, x_name, f"{title} log scale", x_label, log_scale=True, label=label, color=color)

    def plot_selected(self) -> None:
        self.figure.clear()
        axes = self.figure.subplots(2, 4)

        curve1 = self._selected_curve_set(self.curve1_vars)
        curve2 = self._selected_curve_set(self.curve2_vars) if self.show_curve2 else None
        if curve1 is None:
            axes[0][1].text(
                0.5,
                0.5,
                "No matching Curve 1 CSV pair found",
                ha="center",
                va="center",
                transform=axes[0][1].transAxes,
            )
            self.figure.tight_layout()
            self.canvas.draw()
            return

        self._plot_curve_set(curve1, axes, "Curve 1", "#1f77b4", twin=False)
        if curve2 is not None:
            self._plot_curve_set(curve2, axes, "Curve 2", "#d62728", twin=self.axis_mode.get() == "Dual Y")

        for axis in axes.ravel():
            handles, labels = axis.get_legend_handles_labels()
            for twin_axis in axis.figure.axes:
                if twin_axis is not axis and twin_axis.bbox.bounds == axis.bbox.bounds:
                    twin_handles, twin_labels = twin_axis.get_legend_handles_labels()
                    handles.extend(twin_handles)
                    labels.extend(twin_labels)
            if handles:
                axis.legend(handles, labels, fontsize=8)

        title = curve1.stem if curve2 is None else f"{curve1.stem} vs {curve2.stem}"
        self.figure.suptitle(title, fontsize=14)
        self.figure.tight_layout()
        self.canvas.draw()


def main() -> None:
    args = _parse_args()
    root = tk.Tk()
    IVVisualizationApp(root, args.dataset_dir.resolve())
    root.mainloop()


if __name__ == "__main__":
    main()
