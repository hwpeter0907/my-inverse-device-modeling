from __future__ import annotations

import argparse
import csv
import math
import re
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk

import matplotlib

matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as path_effects
import matplotlib.tri as mtri
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.colors import LogNorm, Normalize, SymLogNorm
from matplotlib.figure import Figure


SURFACE_TOP_VIEW_NM = -200.0
CURRENT_DENSITY_VMIN = 1e-13
FIELD_DISPLAYS = (
    "Mesh",
    "Abs net doping",
    "Net doping",
    "Total current density",
    "Potential",
    "Electric field",
)
FIELD_MAP_CMAP = "viridis"


@dataclass
class Mesh2D:
    points_cm: np.ndarray
    triangles: np.ndarray
    contact_segments_cm: dict[str, list[tuple[np.ndarray, np.ndarray]]]


@dataclass
class ZoneData:
    name: str
    x_nm: np.ndarray
    y_nm: np.ndarray
    triangles: np.ndarray
    net_doping: np.ndarray | None
    abs_net_doping: np.ndarray | None
    potential: np.ndarray | None
    electric_x: np.ndarray | None
    electric_y: np.ndarray | None
    electric_mag: np.ndarray | None
    current_x_nm: np.ndarray | None
    current_y_nm: np.ndarray | None
    j_total: np.ndarray | None


@dataclass
class FieldData:
    zones: list[ZoneData]
    bulk: ZoneData | None


def _root(script_file: Path) -> Path:
    return script_file.resolve().parents[1]


def _parse_args() -> argparse.Namespace:
    root = _root(Path(__file__))
    parser = argparse.ArgumentParser(
        description="Interactive generated mesh, absolute doping, and total current density visualization"
    )
    parser.add_argument("--structure-id", type=str, default="L200T20")
    parser.add_argument("--doping-run-id", type=str, default="B1e16SD1e19")
    parser.add_argument("--geometry-config", type=Path, default=root / "config" / "sweep_geometry.csv")
    parser.add_argument("--doping-config", type=Path, default=root / "config" / "sweep_doping.csv")
    parser.add_argument("--runs-dir", type=Path, default=root / "runs")
    parser.add_argument(
        "--current-file",
        type=Path,
        default=None,
        help="Optional *_final.dat or gmsh_mos2d_dd.dat file",
    )
    return parser.parse_args()


def _first_matching_row(path: Path, key: str, value: str) -> dict[str, str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        first: dict[str, str] | None = None
        for row in reader:
            if first is None:
                first = row
            if (row.get(key) or "").strip() == value:
                return row
    if first is not None:
        return first
    raise ValueError(f"No rows found in {path}")


def _config_run_ids(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [
            (row.get("run_id") or "").strip()
            for row in csv.DictReader(handle)
            if (row.get("run_id") or "").strip()
        ]


def _config_rows_by_run_id(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return {
            (row.get("run_id") or "").strip(): row
            for row in csv.DictReader(handle)
            if (row.get("run_id") or "").strip()
        }


def _run_status_row(root: Path, structure_id: str, doping_run_id: str) -> dict[str, str] | None:
    path = root / "dataset" / "run_status.csv"
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if (
                (row.get("structure_id") or "").strip() == structure_id
                and (row.get("doping_run_id") or "").strip() == doping_run_id
            ):
                return row
    return None


def _format_voltage(value_text: str) -> str:
    try:
        value = float(value_text)
    except ValueError:
        return value_text
    return f"{value:.3g}"


def _bias_label(root: Path, structure_id: str, doping_run_id: str) -> str:
    row = _run_status_row(root, structure_id, doping_run_id)
    if row is None:
        return ""

    gate_v = (row.get("final_gate_v") or "").strip()
    drain_v = (row.get("final_drain_v") or "").strip()
    if not gate_v or not drain_v:
        return ""
    return f"Vg={_format_voltage(gate_v)} V, Vd={_format_voltage(drain_v)} V"


def _title_with_bias(title: str, bias_label: str) -> str:
    return f"{title} ({bias_label})" if bias_label else title


def _discover_result_pairs(
    root: Path,
    runs_dir: Path,
) -> tuple[dict[str, set[str]], dict[tuple[str, str], Path]]:
    final_fields = root / "dataset" / "final_fields"
    pairs: dict[str, set[str]] = {}
    files: dict[tuple[str, str], Path] = {}
    if not final_fields.exists():
        return pairs, files

    pattern = re.compile(r"^(?P<structure>.+?)(?P<doping>B.+)_final\.dat$")
    for path in sorted(final_fields.glob("*_final.dat")):
        match = pattern.match(path.name)
        if not match:
            continue
        structure_id = match.group("structure")
        doping_run_id = match.group("doping")
        mesh_path = runs_dir / structure_id / "gmsh_mos2d.msh"
        if not mesh_path.exists():
            continue
        pairs.setdefault(structure_id, set()).add(doping_run_id)
        files[(structure_id, doping_run_id)] = path
    return pairs, files


def _parse_geo_parameters(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    out: dict[str, str] = {}
    pattern = re.compile(
        r"^\s*(device_width|gate_width|oxide_thickness|spacer_width|contact_gap|contact_width|LDD_thickness|gate_thickness)\s*=\s*([^;]+);"
    )
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = pattern.match(line)
        if match:
            out[match.group(1)] = match.group(2).strip()
    return out


def _geometry_row_for_structure(
    geometry_rows: dict[str, dict[str, str]],
    runs_dir: Path,
    structure_id: str,
) -> dict[str, str] | None:
    geo_values = _parse_geo_parameters(runs_dir / structure_id / "gmsh_mos2d.geo")
    row = geometry_rows.get(structure_id)
    if row is None and not geo_values:
        return None

    merged = dict(row or {"run_id": structure_id})
    for name in (
        "device_width",
        "gate_width",
        "oxide_thickness",
        "spacer_width",
        "contact_gap",
        "contact_width",
        "LDD_thickness",
        "gate_thickness",
    ):
        if not (merged.get(name) or "").strip() and geo_values.get(name):
            merged[name] = geo_values[name]
    return merged


def _float_value(value_text: str) -> float | None:
    try:
        return float(value_text)
    except ValueError:
        return None


def _device_width_cm(geometry: dict[str, str]) -> float:
    device_width = _float_value((geometry.get("device_width") or "").strip())
    if device_width is not None:
        return device_width

    gate_width = float(geometry["gate_width"])
    spacer_width = float(geometry.get("spacer_width") or 0.0)
    contact_width = _float_value((geometry.get("contact_width") or "").strip())
    if contact_width is None:
        contact_width = 3.0e-5
    return gate_width + 2.0 * spacer_width + 2.0 * contact_width


def _value_to_nm_label(value_text: str) -> str:
    if not value_text:
        return ""
    nm = float(value_text) * 1e7
    rounded = round(nm)
    if math.isclose(nm, rounded, rel_tol=0.0, abs_tol=1e-6):
        return str(int(rounded))
    return f"{nm:.3f}".rstrip("0").rstrip(".").replace(".", "p")


def _format_doping_label(value: str) -> str:
    token = value.strip().lower().replace("+", "")
    token = token.replace(".0e", "e")
    token = token.replace(".", "p")
    out = []
    for ch in token:
        if ch.isalnum():
            out.append(ch)
    return "".join(out) or ""


def _natural_value_key(value: str) -> tuple[int, float | str]:
    try:
        return (0, float(value.replace("p", ".")))
    except ValueError:
        return (1, value)


def _doping_row_from_run_id(run_id: str) -> dict[str, str]:
    match = re.fullmatch(r"B(?P<B>.+?)SD(?P<SD>.+?)(?:LDD(?P<LDD>.+))?", run_id)
    if not match:
        return {
            "run_id": run_id,
            "bulk_doping": run_id,
            "source_doping": "",
            "drain_doping": "",
            "LDD_doping": "",
        }

    sd = match.group("SD")
    return {
        "run_id": run_id,
        "bulk_doping": match.group("B"),
        "source_doping": sd,
        "drain_doping": sd,
        "LDD_doping": match.group("LDD") or "",
    }


def _doping_row_for_run_id(
    doping_rows: dict[str, dict[str, str]],
    run_id: str,
) -> dict[str, str]:
    return doping_rows.get(run_id) or _doping_row_from_run_id(run_id)


def _discover_structure_ids(runs_dir: Path) -> list[str]:
    if not runs_dir.exists():
        return []
    return sorted(
        path.name
        for path in runs_dir.iterdir()
        if path.is_dir() and (path / "gmsh_mos2d.msh").exists()
    )


def _read_msh2(path: Path) -> Mesh2D:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    points: dict[int, tuple[float, float]] = {}
    triangles: list[list[int]] = []
    physical_names: dict[int, str] = {}
    contact_node_segments: dict[str, list[tuple[int, int]]] = {}

    index = 0
    while index < len(lines):
        token = lines[index].strip()
        if token == "$PhysicalNames":
            count = int(lines[index + 1].strip())
            for offset in range(count):
                parts = lines[index + 2 + offset].split(maxsplit=2)
                if len(parts) == 3 and parts[0] == "1":
                    physical_names[int(parts[1])] = parts[2].strip().strip('"')
            index += count + 2
        elif token == "$Nodes":
            count = int(lines[index + 1].strip())
            for offset in range(count):
                parts = lines[index + 2 + offset].split()
                node_id = int(parts[0])
                points[node_id] = (float(parts[1]), float(parts[2]))
            index += count + 2
        elif token == "$Elements":
            count = int(lines[index + 1].strip())
            for offset in range(count):
                parts = lines[index + 2 + offset].split()
                element_type = int(parts[1])
                tag_count = int(parts[2])
                tags = [int(value) for value in parts[3 : 3 + tag_count]]
                node_ids = [int(value) for value in parts[3 + tag_count :]]
                if element_type == 2 and len(node_ids) == 3:
                    triangles.append(node_ids)
                elif element_type == 1 and len(node_ids) == 2 and tags:
                    physical_name = physical_names.get(tags[0], "")
                    if physical_name.endswith("_contact"):
                        contact_node_segments.setdefault(physical_name, []).append(
                            (node_ids[0], node_ids[1])
                        )
            index += count + 2
        else:
            index += 1

    if not points or not triangles:
        raise ValueError(f"Could not parse 2D triangles from {path}")

    ordered_ids = sorted(points)
    id_to_index = {node_id: idx for idx, node_id in enumerate(ordered_ids)}
    point_array = np.array([points[node_id] for node_id in ordered_ids], dtype=float)
    tri_array = np.array(
        [[id_to_index[node_id] for node_id in tri] for tri in triangles],
        dtype=int,
    )
    contact_segments_cm = {
        name: [
            (np.array(points[start], dtype=float), np.array(points[end], dtype=float))
            for start, end in segments
            if start in points and end in points
        ]
        for name, segments in contact_node_segments.items()
    }
    return Mesh2D(
        points_cm=point_array,
        triangles=tri_array,
        contact_segments_cm=contact_segments_cm,
    )


def _erfc(values: np.ndarray) -> np.ndarray:
    return np.vectorize(math.erfc, otypes=[float])(values)


def _net_doping_profile(
    geometry: dict[str, str],
    doping: dict[str, str],
    x_nm: np.ndarray,
    y_nm: np.ndarray,
) -> np.ndarray:
    device_width = _device_width_cm(geometry)
    gate_width = float(geometry["gate_width"])
    spacer_width = float(geometry.get("spacer_width") or 0.0)
    diffusion_thickness = 5e-6
    LDD_thickness = float(geometry.get("LDD_thickness") or 2.5e-6)
    device_thickness = 1e-4
    x_diffusion_decay = 2e-7
    y_diffusion_decay = 5e-7
    body_doping = 1e19

    bulk_doping = float(doping["bulk_doping"])
    source_doping = float(doping["source_doping"])
    drain_doping = float(doping["drain_doping"])
    LDD_doping = float(doping.get("LDD_doping") or 0.0)

    x_cm = x_nm * 1e-7
    y_cm = y_nm * 1e-7
    x_grid, y_grid = np.meshgrid(x_cm, y_cm)

    x_center = 0.5 * device_width
    x_gate_left = x_center - 0.5 * gate_width
    x_gate_right = x_center + 0.5 * gate_width
    x_spacer_left = x_gate_left - spacer_width
    x_spacer_right = x_gate_right + spacer_width
    y_bulk_top = 0.0
    y_bulk_bottom = y_bulk_top + device_thickness
    y_diffusion = y_bulk_top + diffusion_thickness
    y_LDD = y_bulk_top + LDD_thickness

    drain = (
        0.25
        * drain_doping
        * _erfc((x_grid - x_spacer_left) / x_diffusion_decay)
        * _erfc((y_grid - y_diffusion) / y_diffusion_decay)
    )
    source = (
        0.25
        * source_doping
        * _erfc(-(x_grid - x_spacer_right) / x_diffusion_decay)
        * _erfc((y_grid - y_diffusion) / y_diffusion_decay)
    )
    left_LDD = (
        0.25
        * LDD_doping
        * _erfc((x_grid - x_gate_left) / x_diffusion_decay)
        * _erfc((y_grid - y_LDD) / y_diffusion_decay)
    )
    right_LDD = (
        0.25
        * LDD_doping
        * _erfc(-(x_grid - x_gate_right) / x_diffusion_decay)
        * _erfc((y_grid - y_LDD) / y_diffusion_decay)
    )
    body = 0.5 * body_doping * _erfc(-(y_grid - y_bulk_bottom) / y_diffusion_decay)

    donors = drain + source + left_LDD + right_LDD + 1.0
    acceptors = bulk_doping + body
    return donors - acceptors


def _geometry_markers_nm(geometry: dict[str, str]) -> dict[str, float]:
    device_width = _device_width_cm(geometry)
    gate_width = float(geometry["gate_width"])
    oxide_thickness = float(geometry["oxide_thickness"])
    spacer_width = float(geometry.get("spacer_width") or 0.0)
    contact_gap = float(geometry.get("contact_gap") or 0.0)
    gate_thickness = float(geometry.get("gate_thickness") or 1e-5)
    device_thickness = 1e-4
    diffusion_thickness = 5e-6

    x_center = 0.5 * device_width
    x_gate_left = x_center - 0.5 * gate_width
    x_gate_right = x_center + 0.5 * gate_width
    x_spacer_left = x_gate_left - spacer_width
    x_spacer_right = x_gate_right + spacer_width
    x_source_contact_right = x_spacer_left - contact_gap
    x_drain_contact_left = x_spacer_right + contact_gap

    return {
        "gate_left_nm": x_gate_left * 1e7,
        "gate_right_nm": x_gate_right * 1e7,
        "spacer_left_nm": x_spacer_left * 1e7,
        "spacer_right_nm": x_spacer_right * 1e7,
        "source_contact_right_nm": x_source_contact_right * 1e7,
        "drain_contact_left_nm": x_drain_contact_left * 1e7,
        "oxide_top_nm": -oxide_thickness * 1e7,
        "surface_nm": 0.0,
        "gate_top_nm": -(oxide_thickness + gate_thickness) * 1e7,
        "diffusion_nm": diffusion_thickness * 1e7,
        "bulk_left_nm": 0.0,
        "bulk_right_nm": device_width * 1e7,
        "body_contact_nm": device_thickness * 1e7,
    }


def _contact_color(text_color: str, preferred: str) -> str:
    return "white" if text_color == "white" else preferred


def _draw_contact_bar(
    axis,
    x_left: float,
    x_right: float,
    y_nm: float,
    label: str,
    color: str,
    text_color: str,
    label_offset_nm: float,
) -> None:
    line = axis.plot(
        [x_left, x_right],
        [y_nm, y_nm],
        color=color,
        linewidth=4.0,
        solid_capstyle="butt",
        zorder=12,
    )[0]
    line.set_path_effects(
        [
            path_effects.Stroke(linewidth=6.0, foreground="white"),
            path_effects.Normal(),
        ]
    )
    text = axis.text(
        0.5 * (x_left + x_right),
        y_nm + label_offset_nm,
        label,
        color=text_color,
        ha="center",
        va="center",
        fontsize=8,
        zorder=13,
    )
    text.set_path_effects(
        [
            path_effects.Stroke(linewidth=2.6, foreground="white"),
            path_effects.Normal(),
        ]
    )


def _draw_contact_segments(
    axis,
    segments_cm: list[tuple[np.ndarray, np.ndarray]],
    label: str,
    color: str,
    text_color: str,
    label_offset_nm: float,
) -> bool:
    if not segments_cm:
        return False

    points_nm = []
    for start_cm, end_cm in segments_cm:
        start_nm = start_cm * 1e7
        end_nm = end_cm * 1e7
        points_nm.extend([start_nm, end_nm])
        line = axis.plot(
            [start_nm[0], end_nm[0]],
            [start_nm[1], end_nm[1]],
            color=color,
            linewidth=4.0,
            solid_capstyle="butt",
            zorder=12,
        )[0]
        line.set_path_effects(
            [
                path_effects.Stroke(linewidth=6.0, foreground="white"),
                path_effects.Normal(),
            ]
        )

    point_array = np.array(points_nm)
    x_mid = 0.5 * (float(point_array[:, 0].min()) + float(point_array[:, 0].max()))
    y_mid = 0.5 * (float(point_array[:, 1].min()) + float(point_array[:, 1].max()))
    text = axis.text(
        x_mid,
        y_mid + label_offset_nm,
        label,
        color=text_color,
        ha="center",
        va="center",
        fontsize=8,
        zorder=13,
    )
    text.set_path_effects(
        [
            path_effects.Stroke(linewidth=2.6, foreground="white"),
            path_effects.Normal(),
        ]
    )
    return True


def _contrast_line(axis, orientation: str, value: float, color: str, linewidth: float, linestyle: str, zorder: int) -> None:
    if orientation == "h":
        line = axis.axhline(value, color=color, linewidth=linewidth, linestyle=linestyle, zorder=zorder)
    else:
        line = axis.axvline(value, color=color, linewidth=linewidth, linestyle=linestyle, zorder=zorder)
    line.set_path_effects(
        [
            path_effects.Stroke(linewidth=linewidth + 1.8, foreground="white"),
            path_effects.Normal(),
        ]
    )


def _draw_structure_overlay(
    axis,
    geometry: dict[str, str],
    mesh: Mesh2D | None = None,
    text_color: str = "black",
    filled: bool = False,
) -> None:
    markers = _geometry_markers_nm(geometry)
    gate_left = markers["gate_left_nm"]
    gate_right = markers["gate_right_nm"]
    spacer_left = markers["spacer_left_nm"]
    spacer_right = markers["spacer_right_nm"]
    source_contact_right = markers["source_contact_right_nm"]
    drain_contact_left = markers["drain_contact_left_nm"]
    gate_width = gate_right - gate_left
    gate_top = markers["gate_top_nm"]
    oxide_top = markers["oxide_top_nm"]
    surface = markers["surface_nm"]
    diffusion = markers["diffusion_nm"]
    bulk_left = markers["bulk_left_nm"]
    bulk_right = markers["bulk_right_nm"]
    body_contact = markers["body_contact_nm"]

    gate = plt.Rectangle(
        (gate_left, gate_top),
        gate_width,
        oxide_top - gate_top,
        facecolor="#d9d9d9" if filled else "none",
        edgecolor="black",
        alpha=0.7 if filled else 1.0,
        linewidth=1.4,
        zorder=8,
    )
    oxide = plt.Rectangle(
        (gate_left, oxide_top),
        gate_width,
        surface - oxide_top,
        facecolor="#9bd7ff" if filled else "none",
        edgecolor="black",
        alpha=0.45 if filled else 1.0,
        linewidth=1.1,
        linestyle="--",
        zorder=8,
    )
    left_spacer = plt.Rectangle(
        (spacer_left, gate_top),
        gate_left - spacer_left,
        surface - gate_top,
        facecolor="#9bd7ff" if filled else "none",
        edgecolor="black",
        alpha=0.32 if filled else 1.0,
        linewidth=0.9,
        linestyle=":",
        zorder=8,
    )
    right_spacer = plt.Rectangle(
        (gate_right, gate_top),
        spacer_right - gate_right,
        surface - gate_top,
        facecolor="#9bd7ff" if filled else "none",
        edgecolor="black",
        alpha=0.32 if filled else 1.0,
        linewidth=0.9,
        linestyle=":",
        zorder=8,
    )
    axis.add_patch(gate)
    axis.add_patch(left_spacer)
    axis.add_patch(oxide)
    axis.add_patch(right_spacer)
    _contrast_line(axis, "h", surface, text_color, 1.0, "-", 9)
    _contrast_line(axis, "h", diffusion, text_color, 1.0, ":", 9)
    _contrast_line(axis, "v", gate_left, text_color, 1.0, "--", 9)
    _contrast_line(axis, "v", gate_right, text_color, 1.0, "--", 9)
    if spacer_left < gate_left:
        _contrast_line(axis, "v", spacer_left, text_color, 0.8, ":", 9)
    if spacer_right > gate_right:
        _contrast_line(axis, "v", spacer_right, text_color, 0.8, ":", 9)
    contact_color = _contact_color(text_color, "#265d9b")
    gate_color = _contact_color(text_color, "#7a4a19")
    body_color = _contact_color(text_color, "#2c6b2f")
    contact_segments = mesh.contact_segments_cm if mesh is not None else {}
    if not _draw_contact_segments(
        axis,
        contact_segments.get("source_contact", []),
        "source contact",
        contact_color,
        text_color,
        -12.0,
    ):
        _draw_contact_bar(
            axis,
            bulk_left,
            source_contact_right,
            surface,
            "source contact",
            contact_color,
            text_color,
            -12.0,
        )
    if not _draw_contact_segments(
        axis,
        contact_segments.get("drain_contact", []),
        "drain contact",
        contact_color,
        text_color,
        -12.0,
    ):
        _draw_contact_bar(
            axis,
            drain_contact_left,
            bulk_right,
            surface,
            "drain contact",
            contact_color,
            text_color,
            -12.0,
        )
    if not _draw_contact_segments(
        axis,
        contact_segments.get("gate_contact", []),
        "gate contact",
        gate_color,
        text_color,
        -12.0,
    ):
        _draw_contact_bar(
            axis,
            gate_left,
            gate_right,
            gate_top,
            "gate contact",
            gate_color,
            text_color,
            -12.0,
        )
    if not _draw_contact_segments(
        axis,
        contact_segments.get("body_contact", []),
        "body contact",
        body_color,
        text_color,
        -14.0,
    ):
        _draw_contact_bar(
            axis,
            bulk_left,
            bulk_right,
            body_contact,
            "body contact",
            body_color,
            text_color,
            -14.0,
        )


def _draw_material_background(axis, mesh: Mesh2D, geometry: dict[str, str]) -> None:
    xmin, xmax, _ymin, ymax = _mesh_limits_nm(mesh)
    markers = _geometry_markers_nm(geometry)
    gate_left = markers["gate_left_nm"]
    gate_right = markers["gate_right_nm"]
    spacer_left = markers["spacer_left_nm"]
    spacer_right = markers["spacer_right_nm"]
    gate_width = gate_right - gate_left
    gate_top = markers["gate_top_nm"]
    oxide_top = markers["oxide_top_nm"]
    surface = markers["surface_nm"]

    bulk = plt.Rectangle(
        (xmin, surface),
        xmax - xmin,
        ymax - surface,
        facecolor="#cfe8d3",
        edgecolor="none",
        alpha=0.32,
        zorder=-20,
    )
    oxide = plt.Rectangle(
        (spacer_left, gate_top),
        spacer_right - spacer_left,
        surface - gate_top,
        facecolor="#ccecff",
        edgecolor="none",
        alpha=0.36,
        zorder=-19,
    )
    gate = plt.Rectangle(
        (gate_left, gate_top),
        gate_width,
        oxide_top - gate_top,
        facecolor="#d8d8d8",
        edgecolor="none",
        alpha=0.34,
        zorder=-18,
    )
    axis.add_patch(bulk)
    axis.add_patch(oxide)
    axis.add_patch(gate)


def _mesh_limits_nm(mesh: Mesh2D) -> tuple[float, float, float, float]:
    points_nm = mesh.points_cm * 1e7
    return (
        float(points_nm[:, 0].min()),
        float(points_nm[:, 0].max()),
        float(points_nm[:, 1].min()),
        float(points_nm[:, 1].max()),
    )


def _apply_mesh_limits(axis, mesh: Mesh2D) -> None:
    xmin, xmax, ymin, ymax = _mesh_limits_nm(mesh)
    axis.set_xlim(xmin, xmax)
    axis.set_ylim(ymax, min(ymin, SURFACE_TOP_VIEW_NM))
    axis.set_aspect("equal", adjustable="box")


def _add_side_colorbar(fig: Figure, cbar_axis, image, label: str) -> None:
    colorbar = fig.colorbar(image, cax=cbar_axis)
    colorbar.set_label(label, labelpad=8)


def _place_colorbar_axis(fig: Figure, main_axis, cbar_axis, gap_px: float = 15.0, width_px: float = 18.0) -> None:
    fig_width_px = fig.get_figwidth() * fig.dpi
    gap = gap_px / fig_width_px
    width = width_px / fig_width_px
    box = main_axis.get_position()
    cbar_axis.set_position([box.x1 + gap, box.y0, width, box.height])


def _find_current_file(root: Path, structure_id: str, doping_run_id: str) -> Path | None:
    final_fields = root / "dataset" / "final_fields"
    if final_fields.exists():
        exact = final_fields / f"{structure_id}{doping_run_id}_final.dat"
        if exact.exists():
            return exact

    candidates = [
        root / "runs" / "_tmp_work" / structure_id / doping_run_id / "gmsh_mos2d_dd.dat",
        root / "runs" / structure_id / "gmsh_mos2d_dd.dat",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    matches = sorted((root / "runs").glob(f"**/{structure_id}/{doping_run_id}/gmsh_mos2d_dd.dat"))
    return matches[0] if matches else None


def _parse_varlocation(zone_line: str) -> set[int]:
    match = re.search(r"VARLOCATION\s*=\s*\(\s*\[([^\]]+)\]\s*=\s*CELLCENTERED", zone_line, re.IGNORECASE)
    if not match:
        return set()

    out: set[int] = set()
    for part in match.group(1).split(","):
        text = part.strip()
        if "-" in text:
            start, end = [int(value.strip()) for value in text.split("-", 1)]
            out.update(range(start - 1, end))
        else:
            out.add(int(text) - 1)
    return out


def _parse_tecplot_fields(path: Path) -> FieldData | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    var_match = re.search(r"VARIABLES\s*=\s*(.*?)(?:\n\s*ZONE|\Z)", text, re.IGNORECASE | re.DOTALL)
    if not var_match:
        return None

    variables = re.findall(r'"([^"]+)"', var_match.group(1))
    if not variables:
        return None

    lower_vars = [name.lower() for name in variables]

    def find_index(*needles: str) -> int | None:
        if len(needles) == 1:
            exact = needles[0].lower()
            for idx, name in enumerate(lower_vars):
                if name == exact:
                    return idx
        for idx, name in enumerate(lower_vars):
            if all(needle in name for needle in needles):
                return idx
        return None

    x_idx = find_index("x")
    y_idx = find_index("y")
    if x_idx is None or y_idx is None:
        return None

    net_doping_idx = find_index("netdoping")
    potential_idx = find_index("potential")
    electric_x_idx = find_index("electricfield_x")
    electric_y_idx = find_index("electricfield_y")
    electron_current_x_idx = find_index("electroncurrent_x")
    electron_current_y_idx = find_index("electroncurrent_y")
    hole_current_x_idx = find_index("holecurrent_x")
    hole_current_y_idx = find_index("holecurrent_y")

    zones: list[ZoneData] = []
    zone_matches = list(
        re.finditer(
            r"ZONE[^\n]*T\s*=\s*\"([^\"]+)\"[^\n]*NODES\s*=\s*(\d+)[^\n]*ELEMENTS\s*=\s*(\d+)[^\n]*",
            text,
            re.IGNORECASE,
        )
    )
    for index, zone_match in enumerate(zone_matches):
        zone_name = zone_match.group(1)
        nodes = int(zone_match.group(2))
        elements = int(zone_match.group(3))
        zone_line = zone_match.group(0)
        cell_centered = _parse_varlocation(zone_line)
        zone_data_end = zone_matches[index + 1].start() if index + 1 < len(zone_matches) else len(text)
        zone_data = text[zone_match.end() : zone_data_end]
        tokens = zone_data.split()

        blocks: list[np.ndarray] = []
        cursor = 0
        failed = False
        for var_index in range(len(variables)):
            count = elements if var_index in cell_centered else nodes
            if cursor + count > len(tokens):
                failed = True
                break
            try:
                values = np.array([float(value) for value in tokens[cursor : cursor + count]], dtype=float)
            except ValueError:
                failed = True
                break
            blocks.append(values)
            cursor += count
        if failed or cursor + elements * 3 > len(tokens):
            continue

        try:
            triangles = np.array(
                [int(value) - 1 for value in tokens[cursor : cursor + elements * 3]],
                dtype=int,
            ).reshape(elements, 3)
        except ValueError:
            continue

        x_nm = blocks[x_idx] * 1e7
        y_nm = blocks[y_idx] * 1e7
        net_doping = blocks[net_doping_idx] if net_doping_idx is not None else None
        abs_net_doping = np.abs(net_doping) if net_doping is not None else None
        potential = blocks[potential_idx] if potential_idx is not None else None
        electric_x = blocks[electric_x_idx] if electric_x_idx is not None else None
        electric_y = blocks[electric_y_idx] if electric_y_idx is not None else None
        if electric_x is not None and electric_y is not None:
            electric_mag = np.sqrt(electric_x**2 + electric_y**2)
        else:
            electric_mag = None

        current_indices = [
            electron_current_x_idx,
            electron_current_y_idx,
            hole_current_x_idx,
            hole_current_y_idx,
        ]
        if all(current_index is not None for current_index in current_indices):
            ex = blocks[electron_current_x_idx]  # type: ignore[index]
            ey = blocks[electron_current_y_idx]  # type: ignore[index]
            hx = blocks[hole_current_x_idx]  # type: ignore[index]
            hy = blocks[hole_current_y_idx]  # type: ignore[index]
            j_total = np.sqrt((ex + hx) ** 2 + (ey + hy) ** 2)
            centroids_x = x_nm[triangles].mean(axis=1)
            centroids_y = y_nm[triangles].mean(axis=1)
        else:
            j_total = None
            centroids_x = None
            centroids_y = None

        zones.append(
            ZoneData(
                name=zone_name,
                x_nm=x_nm,
                y_nm=y_nm,
                triangles=triangles,
                net_doping=net_doping,
                abs_net_doping=abs_net_doping,
                potential=potential,
                electric_x=electric_x,
                electric_y=electric_y,
                electric_mag=electric_mag,
                current_x_nm=centroids_x,
                current_y_nm=centroids_y,
                j_total=j_total,
            )
        )

    if not zones:
        return None
    bulk = next((zone for zone in zones if zone.name.lower() == "bulk"), None)
    return FieldData(zones=zones, bulk=bulk)


def _plot_mesh(axis, mesh: Mesh2D, geometry: dict[str, str]) -> None:
    points_nm = mesh.points_cm * 1e7
    triangulation = mtri.Triangulation(points_nm[:, 0], points_nm[:, 1], mesh.triangles)
    _draw_material_background(axis, mesh, geometry)
    axis.triplot(triangulation, color="black", linewidth=0.25, alpha=0.88)
    _draw_structure_overlay(axis, geometry, mesh=mesh, text_color="black", filled=False)
    axis.set_title("Generated mesh")
    axis.set_xlabel("x (nm)")
    axis.set_ylabel("y (nm)")
    _apply_mesh_limits(axis, mesh)


def _plot_doping(
    fig: Figure,
    axis,
    cbar_axis,
    geometry: dict[str, str],
    doping: dict[str, str],
    mesh: Mesh2D,
    fields: FieldData | None,
    doping_display: str,
) -> None:
    show_abs = doping_display == "Abs net doping"
    field_name = "abs_net_doping" if show_abs else "net_doping"
    if fields is not None and any(getattr(zone, field_name) is not None for zone in fields.zones):
        image = None
        for zone in fields.zones:
            zone_values = getattr(zone, field_name)
            if zone_values is None:
                continue
            triangulation = mtri.Triangulation(zone.x_nm, zone.y_nm, zone.triangles)
            if show_abs:
                values = np.maximum(zone_values, 1e14)
                cmap = "viridis"
                norm = LogNorm(vmin=1e14, vmax=1e20)
            else:
                values = zone_values
                cmap = "seismic"
                norm = SymLogNorm(linthresh=1e14, vmin=-1e20, vmax=1e20)
            image = axis.tripcolor(
                triangulation,
                values,
                shading="flat",
                cmap=cmap,
                norm=norm,
            )
    else:
        x_nm = np.linspace(0, 1000, 700)
        y_nm = np.linspace(0, 1000, 500)
        net_doping = _net_doping_profile(geometry, doping, x_nm, y_nm)
        if show_abs:
            values = np.maximum(np.abs(net_doping), 1e14)
            cmap = "viridis"
            norm = LogNorm(vmin=1e14, vmax=1e20)
        else:
            values = net_doping
            cmap = "seismic"
            norm = SymLogNorm(linthresh=1e14, vmin=-1e20, vmax=1e20)
        image = axis.imshow(
            values,
            extent=[0, 1000, 1000, 0],
            aspect="auto",
            cmap=cmap,
            norm=norm,
        )

    _draw_structure_overlay(axis, geometry, mesh=mesh, text_color="black", filled=False)
    axis.set_title("|Net doping|" if show_abs else "Net doping")
    axis.set_xlabel("x (nm)")
    axis.set_ylabel("y (nm)")
    _apply_mesh_limits(axis, mesh)
    if image is not None:
        _add_side_colorbar(fig, cbar_axis, image, "Doping (cm$^{-3}$)")
    else:
        cbar_axis.set_axis_off()


def _field_error_message(axis, cbar_axis, label: str, current_file: Path | None) -> None:
    axis.set_axis_off()
    cbar_axis.set_axis_off()
    message = (
        f"{label} data not found or could not be parsed.\n\n"
        "Expected file:\n"
        "dataset/final_fields/<parameter_set>_final.dat"
    )
    if current_file is not None:
        message += f"\n\nFound but could not parse:\n{current_file}"
    axis.text(0.5, 0.5, message, ha="center", va="center", transform=axis.transAxes)


def _finite_zone_values(fields: FieldData | None, field_name: str) -> list[np.ndarray]:
    if fields is None:
        return []
    values: list[np.ndarray] = []
    for zone in fields.zones:
        zone_values = getattr(zone, field_name)
        if zone_values is None:
            continue
        finite = zone_values[np.isfinite(zone_values)]
        if finite.size:
            values.append(finite)
    return values


def _plot_zone_scalar_field(
    fig: Figure,
    axis,
    cbar_axis,
    fields: FieldData | None,
    current_file: Path | None,
    mesh: Mesh2D,
    geometry: dict[str, str],
    field_name: str,
    title: str,
    colorbar_label: str,
    cmap: str,
    log_scale: bool,
    bias_label: str,
) -> None:
    finite_values = _finite_zone_values(fields, field_name)
    if not finite_values:
        _field_error_message(axis, cbar_axis, title, current_file)
        return

    all_values = np.concatenate(finite_values)
    if log_scale:
        positive = all_values[all_values > 0.0]
        if not positive.size:
            norm = Normalize(vmin=0.0, vmax=1.0)
        else:
            vmin = max(float(np.nanmin(positive)), 1e-12)
            vmax = max(float(np.nanmax(positive)), vmin * 10.0)
            norm = LogNorm(vmin=vmin, vmax=vmax)
    else:
        vmin = float(np.nanmin(all_values))
        vmax = float(np.nanmax(all_values))
        if math.isclose(vmin, vmax, rel_tol=0.0, abs_tol=1e-15):
            delta = max(abs(vmin) * 0.01, 1e-6)
            vmin -= delta
            vmax += delta
        norm = Normalize(vmin=vmin, vmax=vmax)

    image = None
    for zone in fields.zones if fields is not None else []:
        zone_values = getattr(zone, field_name)
        if zone_values is None:
            continue
        values = np.asarray(zone_values, dtype=float)
        if log_scale:
            values = np.maximum(values, norm.vmin)  # type: ignore[arg-type]
        triangulation = mtri.Triangulation(zone.x_nm, zone.y_nm, zone.triangles)
        image = axis.tripcolor(
            triangulation,
            values,
            shading="flat",
            cmap=cmap,
            norm=norm,
        )

    _draw_structure_overlay(axis, geometry, mesh=mesh, text_color="black", filled=False)
    axis.set_title(_title_with_bias(title, bias_label))
    axis.set_xlabel("x (nm)")
    axis.set_ylabel("y (nm)")
    _apply_mesh_limits(axis, mesh)
    if image is not None:
        _add_side_colorbar(fig, cbar_axis, image, colorbar_label)
    else:
        cbar_axis.set_axis_off()


def _plot_current(
    fig: Figure,
    axis,
    cbar_axis,
    fields: FieldData | None,
    current_file: Path | None,
    mesh: Mesh2D,
    geometry: dict[str, str],
    bias_label: str,
) -> None:
    bulk = fields.bulk if fields is not None else None
    if bulk is None or bulk.j_total is None:
        _field_error_message(axis, cbar_axis, "Total current density", current_file)
        return

    values = np.maximum(bulk.j_total, CURRENT_DENSITY_VMIN)
    vmax = max(float(np.nanmax(values)), CURRENT_DENSITY_VMIN * 10.0)
    levels = np.logspace(math.log10(CURRENT_DENSITY_VMIN), math.log10(vmax), 60)
    image = axis.tricontourf(
        bulk.current_x_nm,
        bulk.current_y_nm,
        values,
        levels=levels,
        norm=LogNorm(vmin=CURRENT_DENSITY_VMIN, vmax=vmax),
        cmap=FIELD_MAP_CMAP,
    )
    _draw_structure_overlay(axis, geometry, mesh=mesh, text_color="black", filled=False)
    axis.set_title(_title_with_bias("Total current density |Jn + Jp|", bias_label))
    axis.set_xlabel("x (nm)")
    axis.set_ylabel("y (nm)")
    _apply_mesh_limits(axis, mesh)
    _add_side_colorbar(fig, cbar_axis, image, "Current density (A/cm$^2$)")


def _plot_field_map(
    fig: Figure,
    axis,
    cbar_axis,
    fields: FieldData | None,
    current_file: Path | None,
    mesh: Mesh2D,
    geometry: dict[str, str],
    doping: dict[str, str],
    field_display: str,
    bias_label: str,
) -> None:
    if field_display == "Mesh":
        cbar_axis.set_axis_off()
        _plot_mesh(axis, mesh, geometry)
    elif field_display in ("Abs net doping", "Net doping"):
        _plot_doping(fig, axis, cbar_axis, geometry, doping, mesh, fields, field_display)
    elif field_display == "Potential":
        _plot_zone_scalar_field(
            fig,
            axis,
            cbar_axis,
            fields,
            current_file,
            mesh,
            geometry,
            "potential",
            "Potential",
            "Potential (V)",
            FIELD_MAP_CMAP,
            log_scale=False,
            bias_label=bias_label,
        )
    elif field_display == "Electric field":
        _plot_zone_scalar_field(
            fig,
            axis,
            cbar_axis,
            fields,
            current_file,
            mesh,
            geometry,
            "electric_mag",
            "|Electric field|",
            "Electric field (V/cm)",
            FIELD_MAP_CMAP,
            log_scale=True,
            bias_label=bias_label,
        )
    else:
        _plot_current(fig, axis, cbar_axis, fields, current_file, mesh, geometry, bias_label)


def _plot_structure_figure(
    fig: Figure,
    root: Path,
    runs_dir: Path,
    geometry_config: Path,
    doping_config: Path,
    structure_id: str,
    doping_run_id: str,
    field_display: str,
    current_file_arg: Path | None,
) -> Path | None:
    fig.clear()
    structure_dir = runs_dir / structure_id
    mesh_path = structure_dir / "gmsh_mos2d.msh"
    mesh = _read_msh2(mesh_path)

    geometry_rows = _config_rows_by_run_id(geometry_config)
    geometry = _geometry_row_for_structure(geometry_rows, runs_dir, structure_id)
    if geometry is None:
        raise ValueError(
            f"Geometry row not found in {geometry_config} and could not parse runs/{structure_id}/gmsh_mos2d.geo"
        )
    doping_rows = _config_rows_by_run_id(doping_config)
    doping = _doping_row_for_run_id(doping_rows, doping_run_id)

    current_file = current_file_arg
    if current_file is None:
        current_file = _find_current_file(root, structure_id, doping_run_id)
    fields = _parse_tecplot_fields(current_file) if current_file and current_file.exists() else None
    bias_label = _bias_label(root, structure_id, doping_run_id)

    grid = fig.add_gridspec(1, 2, width_ratios=[1.0, 0.035])
    field_axis = fig.add_subplot(grid[0, 0])
    field_cbar_axis = fig.add_subplot(grid[0, 1])

    _plot_field_map(
        fig,
        field_axis,
        field_cbar_axis,
        fields,
        current_file,
        mesh,
        geometry,
        doping,
        field_display,
        bias_label,
    )

    title = (
        f"{structure_id}/{doping_run_id}  "
        f"B={doping['bulk_doping']} S={doping['source_doping']} D={doping['drain_doping']}"
    )
    fig.suptitle(title)
    fig.subplots_adjust(left=0.08, right=0.92, bottom=0.08, top=0.9, wspace=0.08)
    _place_colorbar_axis(fig, field_axis, field_cbar_axis, gap_px=15.0)
    return current_file


class StructureVisualizationApp:
    GEOMETRY_PARAMS = ("L", "T")
    DOPING_PARAMS = ("B", "SD", "LDD")

    def __init__(
        self,
        root_window: tk.Tk,
        root: Path,
        runs_dir: Path,
        geometry_config: Path,
        doping_config: Path,
        initial_structure_id: str,
        initial_doping_run_id: str,
        current_file: Path | None,
    ) -> None:
        self.root_window = root_window
        self.root = root
        self.runs_dir = runs_dir
        self.geometry_config = geometry_config
        self.doping_config = doping_config
        self.current_file = current_file

        root_window.title("Structure Visualization")
        root_window.geometry("1500x900")

        self.selected_structure_id = tk.StringVar(value=initial_structure_id)
        self.selected_doping_run_id = tk.StringVar(value=initial_doping_run_id)
        self.selected_field_display = tk.StringVar(value=FIELD_DISPLAYS[0])
        self.geometry_vars = {name: tk.StringVar() for name in self.GEOMETRY_PARAMS}
        self.doping_vars = {name: tk.StringVar() for name in self.DOPING_PARAMS}
        self.geometry_rows: dict[str, dict[str, str]] = {}
        self.doping_rows: dict[str, dict[str, str]] = {}
        self.available_structure_ids: list[str] = []
        self.available_doping_run_ids: list[str] = []
        self.result_pairs: dict[str, set[str]] = {}
        self.result_files: dict[tuple[str, str], Path] = {}
        self.status_text = tk.StringVar()

        controls = ttk.Frame(root_window, padding=(10, 8))
        controls.pack(side=tk.TOP, fill=tk.X)

        self.geometry_combos = self._build_param_controls(controls, "Geometry", self.GEOMETRY_PARAMS, self.geometry_vars)
        self.doping_combos = self._build_param_controls(controls, "Doping", self.DOPING_PARAMS, self.doping_vars)

        ttk.Label(controls, text="Field map").pack(side=tk.LEFT)
        self.field_display_combo = ttk.Combobox(
            controls,
            textvariable=self.selected_field_display,
            state="readonly",
            values=FIELD_DISPLAYS,
            width=22,
        )
        self.field_display_combo.pack(side=tk.LEFT, padx=(8, 12))
        self.field_display_combo.bind("<<ComboboxSelected>>", lambda _event: self.plot_selected())

        ttk.Button(controls, text="Refresh", command=self.refresh).pack(side=tk.LEFT)
        ttk.Label(controls, textvariable=self.status_text).pack(side=tk.LEFT, padx=(12, 0))

        self.figure = Figure(figsize=(15, 8), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.figure, master=root_window)
        self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        toolbar_frame = ttk.Frame(root_window)
        toolbar_frame.pack(side=tk.BOTTOM, fill=tk.X)
        NavigationToolbar2Tk(self.canvas, toolbar_frame)

        self.refresh()

    def _build_param_controls(
        self,
        parent,
        label: str,
        names: tuple[str, ...],
        vars_by_name: dict[str, tk.StringVar],
    ) -> dict[str, ttk.Combobox]:
        ttk.Label(parent, text=label).pack(side=tk.LEFT, padx=(0, 4))
        combos: dict[str, ttk.Combobox] = {}
        for name in names:
            ttk.Label(parent, text=name).pack(side=tk.LEFT)
            combo = ttk.Combobox(
                parent,
                textvariable=vars_by_name[name],
                state="readonly",
                width=7,
            )
            combo.pack(side=tk.LEFT, padx=(3, 6))
            combo.bind("<<ComboboxSelected>>", lambda _event: self.plot_selected())
            combos[name] = combo
        return combos

    def _geometry_param_values(self, row: dict[str, str]) -> dict[str, str]:
        return {
            "L": _value_to_nm_label((row.get("gate_width") or "").strip()),
            "T": _value_to_nm_label((row.get("oxide_thickness") or "").strip()),
        }

    def _doping_param_values(self, row: dict[str, str]) -> dict[str, str]:
        source = _format_doping_label((row.get("source_doping") or "").strip())
        drain = _format_doping_label((row.get("drain_doping") or "").strip())
        sd = source if source == drain else f"{source}/{drain}"
        return {
            "B": _format_doping_label((row.get("bulk_doping") or "").strip()),
            "SD": sd,
            "LDD": _format_doping_label((row.get("LDD_doping") or "").strip()),
        }

    def _refresh_values(
        self,
        rows: list[dict[str, str]],
        names: tuple[str, ...],
        vars_by_name: dict[str, tk.StringVar],
        combos_by_name: dict[str, ttk.Combobox],
        value_fn,
    ) -> None:
        parsed = [value_fn(row) for row in rows]
        for name in names:
            values = sorted({item[name] for item in parsed if item[name]}, key=_natural_value_key)
            combos_by_name[name]["values"] = values
            if vars_by_name[name].get() not in values:
                vars_by_name[name].set(values[0] if values else "")

    def refresh(self) -> None:
        self.geometry_rows = _config_rows_by_run_id(self.geometry_config)
        self.doping_rows = _config_rows_by_run_id(self.doping_config)
        self.result_pairs, self.result_files = _discover_result_pairs(self.root, self.runs_dir)
        if self.result_pairs:
            self.available_structure_ids = sorted(self.result_pairs)
            self.available_doping_run_ids = sorted(
                {run_id for run_ids in self.result_pairs.values() for run_id in run_ids}
            )
        else:
            self.available_structure_ids = _discover_structure_ids(self.runs_dir)
            self.available_doping_run_ids = list(self.doping_rows)

        geometry_rows = [
            row
            for structure_id in self.available_structure_ids
            for row in [_geometry_row_for_structure(self.geometry_rows, self.runs_dir, structure_id)]
            if row is not None
        ]
        doping_rows = [
            _doping_row_for_run_id(self.doping_rows, run_id)
            for run_id in self.available_doping_run_ids
        ]
        self._refresh_values(
            geometry_rows,
            self.GEOMETRY_PARAMS,
            self.geometry_vars,
            self.geometry_combos,
            self._geometry_param_values,
        )
        self._refresh_values(
            doping_rows,
            self.DOPING_PARAMS,
            self.doping_vars,
            self.doping_combos,
            self._doping_param_values,
        )

        self.plot_selected()

    def _selected_structure_id(self) -> str:
        selected = {name: self.geometry_vars[name].get() for name in self.GEOMETRY_PARAMS}
        for structure_id in self.available_structure_ids:
            row = _geometry_row_for_structure(self.geometry_rows, self.runs_dir, structure_id)
            if row is None:
                continue
            values = self._geometry_param_values(row)
            if all(values[name] == selected[name] for name in self.GEOMETRY_PARAMS):
                return structure_id
        return ""

    def _selected_doping_run_id(self) -> str:
        selected = {name: self.doping_vars[name].get() for name in self.DOPING_PARAMS}
        for run_id in self.available_doping_run_ids:
            row = _doping_row_for_run_id(self.doping_rows, run_id)
            values = self._doping_param_values(row)
            if all(values[name] == selected[name] for name in self.DOPING_PARAMS):
                return run_id
        return ""

    def plot_selected(self) -> None:
        structure_id = self._selected_structure_id()
        doping_run_id = self._selected_doping_run_id()
        field_display = self.selected_field_display.get()
        self.figure.clear()

        if not structure_id or not doping_run_id:
            axis = self.figure.add_subplot(111)
            axis.set_axis_off()
            axis.text(
                0.5,
                0.5,
                "No structure mesh or doping config rows found",
                ha="center",
                va="center",
                transform=axis.transAxes,
            )
            self.status_text.set("No selectable data")
            self.canvas.draw()
            return

        try:
            current_file = self.result_files.get((structure_id, doping_run_id), self.current_file)
            current_file = _plot_structure_figure(
                self.figure,
                self.root,
                self.runs_dir,
                self.geometry_config,
                self.doping_config,
                structure_id,
                doping_run_id,
                field_display,
                current_file,
            )
        except Exception as exc:
            axis = self.figure.add_subplot(111)
            axis.set_axis_off()
            axis.text(0.5, 0.5, str(exc), ha="center", va="center", transform=axis.transAxes)
            self.status_text.set("Plot failed")
        else:
            if current_file is None:
                current_status = "current field: not found"
            else:
                current_status = f"current field: {current_file}"
            self.status_text.set(current_status)

        self.canvas.draw()


def main() -> None:
    args = _parse_args()
    root = _root(Path(__file__))
    window = tk.Tk()
    StructureVisualizationApp(
        root_window=window,
        root=root,
        runs_dir=args.runs_dir.resolve(),
        geometry_config=args.geometry_config.resolve(),
        doping_config=args.doping_config.resolve(),
        initial_structure_id=args.structure_id,
        initial_doping_run_id=args.doping_run_id,
        current_file=args.current_file.resolve() if args.current_file is not None else None,
    )
    window.mainloop()


if __name__ == "__main__":
    main()
