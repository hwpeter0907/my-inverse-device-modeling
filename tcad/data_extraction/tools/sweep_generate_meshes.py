from __future__ import annotations

import argparse
import csv
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from env_config import build_runtime_env, resolve_gmsh_exe  # type: ignore[import-not-found]


@dataclass
class SweepRow:
    gate_width: str
    oxide_thickness: str
    spacer_width: str = ""
    LDD_thickness: str = ""
    run_id: str = ""


def _load_rows(csv_path: Path) -> list[SweepRow]:
    rows: list[SweepRow] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"gate_width", "oxide_thickness"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError(
                "CSV must contain columns: gate_width, oxide_thickness"
            )

        for idx, row in enumerate(reader, start=2):
            run_id = (row.get("run_id") or "").strip()
            gate_width = (row.get("gate_width") or "").strip()
            oxide_thickness = (row.get("oxide_thickness") or "").strip()
            spacer_width = (row.get("spacer_width") or "").strip()
            LDD_thickness = (row.get("LDD_thickness") or "").strip()
            if not gate_width or not oxide_thickness:
                raise ValueError(
                    f"Missing required value at {csv_path}:{idx} " "(gate_width, oxide_thickness)"
                )
            rows.append(
                SweepRow(
                    gate_width=gate_width,
                    oxide_thickness=oxide_thickness,
                    spacer_width=spacer_width,
                    LDD_thickness=LDD_thickness,
                    run_id=run_id,
                )
            )

    if not rows:
        raise ValueError(f"No sweep rows found in {csv_path}")
    return rows


def _replace_param(text: str, name: str, value: str) -> str:
    pattern = re.compile(rf"^(\s*{re.escape(name)}\s*=\s*)([^;]+)(;\s*)$", re.MULTILINE)
    replaced, count = pattern.subn(rf"\g<1>{value}\g<3>", text, count=1)
    if count != 1:
        raise ValueError(f"Failed to find unique parameter assignment for '{name}'")
    return replaced


def _value_to_nm_label(value_text: str) -> str:
    nm = float(value_text) * 1e7
    rounded = round(nm)
    if math.isclose(nm, rounded, rel_tol=0.0, abs_tol=1e-6):
        return str(int(rounded))
    return f"{nm:.3f}".rstrip("0").rstrip(".").replace(".", "p")


def _run_name(row: SweepRow) -> str:
    if row.run_id:
        return row.run_id
    gate_nm = _value_to_nm_label(row.gate_width)
    tox_nm = _value_to_nm_label(row.oxide_thickness)
    if row.spacer_width:
        spacer_nm = _value_to_nm_label(row.spacer_width)
        return f"L{gate_nm}T{tox_nm}S{spacer_nm}"
    return f"L{gate_nm}T{tox_nm}"


def _build_geo(template_geo: Path, row: SweepRow) -> str:
    text = template_geo.read_text(encoding="utf-8")
    text = _replace_param(text, "gate_width", row.gate_width)
    text = _replace_param(text, "oxide_thickness", row.oxide_thickness)
    if row.spacer_width:
        text = _replace_param(text, "spacer_width", row.spacer_width)
    if row.LDD_thickness:
        text = _replace_param(text, "LDD_thickness", row.LDD_thickness)
    return text


def _run_gmsh(gmsh_exe: str, geo_path: Path, msh_path: Path, fmt: str) -> None:
    command = [gmsh_exe, "-2", str(geo_path), "-format", fmt, "-o", str(msh_path)]
    env = _build_gmsh_env(gmsh_exe)
    completed = subprocess.run(command, capture_output=True, text=True, env=env)
    if completed.returncode == 0 and msh_path.exists() and msh_path.stat().st_size > 0:
        return

    try:
        _run_gmsh_python_api(geo_path, msh_path, fmt)
        return
    except Exception as fallback_exc:
        raise RuntimeError(
            "Gmsh failed for run file: "
            f"{geo_path}\n"
            f"Command: {' '.join(command)}\n"
            f"returncode: {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}\n"
            f"Python gmsh fallback failed:\n{fallback_exc}"
        ) from fallback_exc


def _run_gmsh_python_api(geo_path: Path, msh_path: Path, fmt: str) -> None:
    try:
        import gmsh  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "Python package 'gmsh' is not available in this Python environment."
        ) from exc

    gmsh.initialize(["gmsh", "-v", "2"])
    try:
        gmsh.open(str(geo_path))
        gmsh.model.mesh.generate(2)
        if fmt.lower() == "msh2":
            gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
        gmsh.write(str(msh_path))
    finally:
        gmsh.finalize()

    if not msh_path.exists() or msh_path.stat().st_size == 0:
        raise RuntimeError(f"Python gmsh completed but did not write: {msh_path}")


def _build_gmsh_env(gmsh_exe: str) -> dict[str, str]:
    return build_runtime_env(gmsh_exe=gmsh_exe)


def _msh_node_count(msh_path: Path) -> int | None:
    if not msh_path.exists() or msh_path.stat().st_size == 0:
        return None

    with msh_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip() != "$Nodes":
                continue
            count_line = next(handle, "").strip()
            try:
                return int(count_line)
            except ValueError:
                return None
    return None


def _resolve_gmsh_exe(explicit: str) -> str:
    return resolve_gmsh_exe(explicit)


def _resolve_default_paths(script_file: Path) -> tuple[Path, Path, Path]:
    root = script_file.resolve().parents[1]
    config_csv = root / "config" / "sweep_geometry.csv"
    template_geo = root / "base_case" / "gmsh_mos2d.geo"
    output_dir = root / "runs"
    return config_csv, template_geo, output_dir


def parse_args() -> argparse.Namespace:
    script_file = Path(__file__)
    config_csv, template_geo, output_dir = _resolve_default_paths(script_file)

    parser = argparse.ArgumentParser(
        description="Generate .geo/.msh variants by sweeping gate_width and oxide_thickness"
    )
    parser.add_argument("--config", type=Path, default=config_csv)
    parser.add_argument("--template-geo", type=Path, default=template_geo)
    parser.add_argument("--output-dir", type=Path, default=output_dir)
    parser.add_argument("--gmsh-exe", type=str, default="")
    parser.add_argument("--mesh-format", type=str, default="msh2")
    parser.add_argument("--geo-only", action="store_true")
    parser.add_argument(
        "--overwrite",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Overwrite existing generated files (default: true). Use --no-overwrite to fail if files exist.",
    )
    parser.add_argument(
        "--skip-existing",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Skip rows whose .geo and .msh already exist.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    config_csv = args.config.resolve()
    template_geo = args.template_geo.resolve()
    output_dir = args.output_dir.resolve()

    if not config_csv.exists():
        raise FileNotFoundError(f"Config CSV not found: {config_csv}")
    if not template_geo.exists():
        raise FileNotFoundError(f"Template GEO not found: {template_geo}")

    rows = _load_rows(config_csv)

    gmsh_exe = _resolve_gmsh_exe(args.gmsh_exe)
    if not args.geo_only and not gmsh_exe:
        raise RuntimeError(
            "gmsh executable not found. Use --gmsh-exe or run with --geo-only."
        )

    output_dir.mkdir(parents=True, exist_ok=True)

    generated = 0
    mesh_generated = 0
    skipped = 0
    for row in rows:
        run_name = _run_name(row)
        run_dir = output_dir / run_name
        run_dir.mkdir(parents=True, exist_ok=True)

        geo_path = run_dir / "gmsh_mos2d.geo"
        msh_path = run_dir / "gmsh_mos2d.msh"

        expected_outputs = [geo_path] if args.geo_only else [geo_path, msh_path]
        if args.skip_existing and all(path.exists() and path.stat().st_size > 0 for path in expected_outputs):
            print(f"[SKIP] {run_name}: existing mesh files found")
            skipped += 1
            continue

        if (geo_path.exists() or msh_path.exists()) and not args.overwrite:
            raise FileExistsError(
                f"Output already exists for run_id='{run_name}'. "
                "Use --overwrite to replace files."
            )

        geo_text = _build_geo(template_geo, row)
        geo_path.write_text(geo_text, encoding="utf-8")

        mesh_points = None
        if not args.geo_only:
            _run_gmsh(gmsh_exe, geo_path, msh_path, args.mesh_format)
            mesh_points = _msh_node_count(msh_path)
            mesh_generated += 1

        generated += 1
        mesh_text = "skip" if args.geo_only else msh_path.name
        point_text = "NA" if mesh_points is None else str(mesh_points)
        print(
            f"[OK] {run_name}: gate_width={row.gate_width}, "
            f"oxide_thickness={row.oxide_thickness}, "
            f"spacer_width={row.spacer_width or 'template'}, "
            f"LDD_thickness={row.LDD_thickness or 'template'}, "
            f"geo={geo_path.name}, msh={mesh_text}, points={point_text}"
        )

    print(
        f"\nGenerated {generated} run(s), "
        f"mesh files generated: {mesh_generated}, "
        f"skipped: {skipped}"
    )
    print(f"Output directory: {output_dir}")


if __name__ == "__main__":
    main()
