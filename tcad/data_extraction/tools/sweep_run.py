from __future__ import annotations

import argparse
import concurrent.futures
import fnmatch
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from env_config import build_runtime_env, resolve_python_exe  # type: ignore[import-not-found]
from sweep_config import DopingRow, GeometryRow, load_doping_rows, load_geometry_rows  # type: ignore[import-not-found]
from sweep_outputs import (
    curve_name_stem,
    expected_output_paths,
    extract_final_bias,
    extract_ivpoint_count,
    format_elapsed,
    has_existing_outputs,
    parse_ivpoints,
    write_curve_csv,
    write_status_csv,
)  # type: ignore[import-not-found]



def _resolve_defaults(script_file: Path) -> tuple[Path, Path, Path, Path, Path, str, Path]:
    root = script_file.resolve().parents[1]
    runs_dir = root / "runs"
    base_dir = root / "base_case"
    geometry_config = root / "config" / "sweep_geometry.csv"
    doping_config = root / "config" / "sweep_doping.csv"
    status_csv = root / "dataset" / "run_status.csv"
    structure_glob = "L*"
    work_root = root / "runs" / "_tmp_work"
    return runs_dir, base_dir, geometry_config, doping_config, status_csv, structure_glob, work_root


def _parse_args() -> argparse.Namespace:
    (
        runs_dir,
        base_dir,
        geometry_config,
        doping_config,
        status_csv,
        structure_glob,
        work_root,
    ) = _resolve_defaults(Path(__file__))

    parser = argparse.ArgumentParser(
        description=(
            "Execute DEVSIM runs using structures under runs/ and doping rows "
            "from a config CSV."
        )
    )
    parser.add_argument("--runs-dir", type=Path, default=runs_dir)
    parser.add_argument("--base-dir", type=Path, default=base_dir)
    parser.add_argument("--geometry-config", type=Path, default=geometry_config)
    parser.add_argument("--doping-config", type=Path, default=doping_config)
    parser.add_argument(
        "--curves-dir",
        type=Path,
        default=doping_config.parents[1] / "dataset",
    )
    parser.add_argument(
        "--final-fields-dir",
        type=Path,
        default=doping_config.parents[1] / "dataset" / "final_fields",
        help="Directory for DEVSIM field dumps such as *_IdVd_Vg3p0_Vd3p0.dat",
    )
    parser.add_argument("--python-exe", type=str, default=resolve_python_exe())
    parser.add_argument("--structure-glob", type=str, default=structure_glob)
    parser.add_argument("--work-root", type=Path, default=work_root)
    parser.add_argument("--status-csv", type=Path, default=status_csv)
    parser.add_argument(
        "--keep-work",
        action="store_true",
        help="Keep per-run work directories and artifacts under work-root",
    )
    parser.add_argument(
        "--continue-on-error",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Continue remaining runs even if one run fails (default: true)",
    )
    parser.add_argument(
        "--skip-existing",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Skip structure/doping pairs whose IdVd, IdVg, and Vg=3.0/Vd=3.0 field files already exist.",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=4,
        help=(
            "Number of independent DEVSIM runs to execute in parallel "
            "(default: 4). Increase carefully if each run uses a lot of CPU or memory."
        ),
    )
    return parser.parse_args()


def _list_structures(runs_dir: Path, pattern: str) -> list[Path]:
    out: list[Path] = []
    for p in sorted(runs_dir.glob(pattern)):
        if not p.is_dir():
            continue
        if (p / "gmsh_mos2d.geo").exists() and (p / "gmsh_mos2d.msh").exists():
            out.append(p)
    if not out:
        raise FileNotFoundError(
            f"No structure folders found in {runs_dir} with pattern '{pattern}'"
        )
    return out




def _replace_param(text: str, name: str, value: str) -> str:
    pattern = re.compile(rf"^(\s*{re.escape(name)}\s*=\s*)([^#\n]+)$", re.MULTILINE)
    replaced, count = pattern.subn(rf"\g<1>{value}", text, count=1)
    if count != 1:
        raise ValueError(f"Failed to replace parameter: {name}")
    return replaced


def _patch_doping(create_file: Path, row: DopingRow) -> None:
    text = create_file.read_text(encoding="utf-8")
    text = _replace_param(text, "bulk_doping", row.bulk_doping)
    text = _replace_param(text, "source_doping", row.source_doping)
    text = _replace_param(text, "drain_doping", row.drain_doping)
    if row.LDD_doping:
        text = _replace_param(text, "LDD_doping", row.LDD_doping)
    create_file.write_text(text, encoding="utf-8")


def _patch_geometry(create_file: Path, row: GeometryRow) -> None:
    text = create_file.read_text(encoding="utf-8")
    text = _replace_param(text, "gate_width", row.gate_width)
    text = _replace_param(text, "oxide_thickness", row.oxide_thickness)
    if row.spacer_width:
        text = _replace_param(text, "spacer_width", row.spacer_width)
    if row.LDD_thickness:
        text = _replace_param(text, "LDD_thickness", row.LDD_thickness)
    create_file.write_text(text, encoding="utf-8")


def _copy_base_files(base_dir: Path, work_dir: Path) -> None:
    for name in ("gmsh_mos2d.py", "gmsh_mos2d_create.py", "runtime_setup.py"):
        src = base_dir / name
        if not src.exists():
            raise FileNotFoundError(f"Base file not found: {src}")
        shutil.copy2(src, work_dir / name)


def _copy_structure_files(structure_dir: Path, work_dir: Path) -> None:
    for name in ("gmsh_mos2d.geo", "gmsh_mos2d.msh"):
        src = structure_dir / name
        if not src.exists():
            raise FileNotFoundError(f"Structure file not found: {src}")
        shutil.copy2(src, work_dir / name)










def expected_output_paths(
    structure_id: str,
    row: DopingRow,
    curves_dir: Path,
    final_fields_dir: Path,
) -> tuple[Path, Path, Path]:
    naming = curve_name_stem(structure_id, row)
    return (
        curves_dir / f"{naming}_IdVd.csv",
        curves_dir / f"{naming}_IdVg.csv",
        final_fields_dir / f"{naming}_IdVd_Vg3p0_Vd3p0.dat",
    )


def has_existing_outputs(
    structure_id: str,
    row: DopingRow,
    curves_dir: Path,
    final_fields_dir: Path,
) -> bool:
    return all(
        path.exists() and path.stat().st_size > 0
        for path in expected_output_paths(structure_id, row, curves_dir, final_fields_dir)
    )


def _skipped_result(
    structure_id: str,
    row: DopingRow,
    geometry: GeometryRow,
    curves_dir: Path,
    final_fields_dir: Path,
) -> dict[str, str]:
    idvd_csv, idvg_csv, final_dat = expected_output_paths(
        structure_id, row, curves_dir, final_fields_dir
    )
    return {
        "structure_id": structure_id,
        "doping_run_id": row.run_id,
        "gate_width": geometry.gate_width,
        "oxide_thickness": geometry.oxide_thickness,
        "bulk_doping": row.bulk_doping,
        "source_doping": row.source_doping,
        "drain_doping": row.drain_doping,
        "LDD_doping": row.LDD_doping,
        "status": "skipped_existing",
        "return_code": "",
        "elapsed_sec": "0.000",
        "ivpoint_count": "",
        "final_gate_v": "",
        "final_drain_v": "",
        "idvd_csv": str(idvd_csv),
        "idvg_csv": str(idvg_csv),
        "idvd_points": "",
        "idvg_points": "",
        "stdout_log": "",
        "stderr_log": "",
        "work_dir": "",
        "final_dat": str(final_dat),
    }


def _write_iv_curves(
    structure_id: str,
    row: DopingRow,
    curves_dir: Path,
    points: dict[str, list[tuple[float, float, float]]],
) -> tuple[Path, Path, int, int]:
    idvd_rows: list[dict[str, str]] = []
    idvg_rows: list[dict[str, str]] = []

    for tag, vals in points.items():
        if tag.startswith("IDVD"):
            sorted_vals = sorted(vals, key=lambda t: t[1])
            for gate_v, drain_v, drain_i in sorted_vals:
                idvd_rows.append(
                    {
                        "structure_id": structure_id,
                        "doping_run_id": row.run_id,
                        "curve_tag": tag,
                        "gate_v": f"{gate_v:.12e}",
                        "drain_v": f"{drain_v:.12e}",
                        "drain_current": f"{drain_i:.12e}",
                    }
                )
        elif tag.startswith("IDVG"):
            sorted_vals = sorted(vals, key=lambda t: t[0])
            for gate_v, drain_v, drain_i in sorted_vals:
                idvg_rows.append(
                    {
                        "structure_id": structure_id,
                        "doping_run_id": row.run_id,
                        "curve_tag": tag,
                        "gate_v": f"{gate_v:.12e}",
                        "drain_v": f"{drain_v:.12e}",
                        "drain_current": f"{drain_i:.12e}",
                    }
                )

    naming = curve_name_stem(structure_id, row)
    idvd_csv = curves_dir / f"{naming}_IdVd.csv"
    idvg_csv = curves_dir / f"{naming}_IdVg.csv"

    write_curve_csv(
        idvd_csv,
        ["structure_id", "doping_run_id", "curve_tag", "gate_v", "drain_v", "drain_current"],
        idvd_rows,
    )
    write_curve_csv(
        idvg_csv,
        ["structure_id", "doping_run_id", "curve_tag", "gate_v", "drain_v", "drain_current"],
        idvg_rows,
    )

    return idvd_csv, idvg_csv, len(idvd_rows), len(idvg_rows)


def _copy_final_dat(
    structure_id: str,
    row: DopingRow,
    work_dir: Path,
    final_fields_dir: Path,
) -> Path:
    src = work_dir / "gmsh_mos2d_dd.dat"
    if not src.exists():
        return Path("")

    final_fields_dir.mkdir(parents=True, exist_ok=True)
    naming = curve_name_stem(structure_id, row)
    dst = final_fields_dir / f"{naming}_IdVd_Vg3p0_Vd3p0.dat"
    shutil.copy2(src, dst)
    return dst


def _run_one(
    structure_dir: Path,
    row: DopingRow,
    geometry: GeometryRow,
    base_dir: Path,
    python_exe: str,
    work_root: Path,
    curves_dir: Path,
    final_fields_dir: Path,
    keep_work: bool,
) -> dict[str, str]:
    structure_id = structure_dir.name
    work_dir = work_root / structure_id / row.run_id
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    _copy_base_files(base_dir, work_dir)
    _copy_structure_files(structure_dir, work_dir)
    _patch_geometry(work_dir / "gmsh_mos2d_create.py", geometry)
    _patch_doping(work_dir / "gmsh_mos2d_create.py", row)

    started = time.time()
    completed = subprocess.run(
        [python_exe, "gmsh_mos2d.py"],
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        env=build_runtime_env(),
    )
    elapsed = time.time() - started

    logs_dir = structure_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = logs_dir / f"{row.run_id}.stdout.log"
    stderr_log = logs_dir / f"{row.run_id}.stderr.log"
    stdout_log.write_text(completed.stdout, encoding="utf-8")
    stderr_log.write_text(completed.stderr, encoding="utf-8")

    final_bias = extract_final_bias(completed.stdout)
    iv_points = parse_ivpoints(completed.stdout)
    idvd_csv, idvg_csv, idvd_points, idvg_points = _write_iv_curves(
        structure_id=structure_id,
        row=row,
        curves_dir=curves_dir,
        points=iv_points,
    )
    final_dat = _copy_final_dat(
        structure_id=structure_id,
        row=row,
        work_dir=work_dir,
        final_fields_dir=final_fields_dir,
    )

    result = {
        "structure_id": structure_id,
        "doping_run_id": row.run_id,
        "gate_width": geometry.gate_width,
        "oxide_thickness": geometry.oxide_thickness,
        "bulk_doping": row.bulk_doping,
        "source_doping": row.source_doping,
        "drain_doping": row.drain_doping,
        "LDD_doping": row.LDD_doping,
        "status": "ok" if completed.returncode == 0 else "failed",
        "return_code": str(completed.returncode),
        "elapsed_sec": f"{elapsed:.3f}",
        "ivpoint_count": str(extract_ivpoint_count(completed.stdout)),
        "final_gate_v": "" if final_bias is None else f"{final_bias[0]:.12e}",
        "final_drain_v": "" if final_bias is None else f"{final_bias[1]:.12e}",
        "idvd_csv": str(idvd_csv),
        "idvg_csv": str(idvg_csv),
        "idvd_points": str(idvd_points),
        "idvg_points": str(idvg_points),
        "stdout_log": str(stdout_log),
        "stderr_log": str(stderr_log),
        "work_dir": str(work_dir),
        "final_dat": str(final_dat),
    }

    if not keep_work:
        shutil.rmtree(work_dir, ignore_errors=True)

    return result


@dataclass(frozen=True)
class RunTask:
    index: int
    structure_dir: Path
    row: DopingRow
    geometry: GeometryRow

    @property
    def label(self) -> str:
        return f"{self.structure_dir.name}/{self.row.run_id}"


def _failed_result(task: RunTask) -> dict[str, str]:
    return {
        "structure_id": task.structure_dir.name,
        "doping_run_id": task.row.run_id,
        "gate_width": task.geometry.gate_width,
        "oxide_thickness": task.geometry.oxide_thickness,
        "bulk_doping": task.row.bulk_doping,
        "source_doping": task.row.source_doping,
        "drain_doping": task.row.drain_doping,
        "LDD_doping": task.row.LDD_doping,
        "status": "failed",
        "return_code": "",
        "elapsed_sec": "",
        "ivpoint_count": "",
        "final_gate_v": "",
        "final_drain_v": "",
        "idvd_csv": "",
        "idvg_csv": "",
        "idvd_points": "",
        "idvg_points": "",
        "stdout_log": "",
        "stderr_log": "",
        "work_dir": "",
        "final_dat": "",
    }


def _ordered_results(results_by_index: dict[int, dict[str, str]]) -> list[dict[str, str]]:
    return [results_by_index[index] for index in sorted(results_by_index)]


def _status_counts(results_by_index: dict[int, dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results_by_index.values():
        status = result.get("status", "")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _done_message(task: RunTask, total_runs: int, result: dict[str, str]) -> str:
    final_gate_v = result.get("final_gate_v") or "NA"
    final_drain_v = result.get("final_drain_v") or "NA"
    return (
        f"[DONE {task.index}/{total_runs}] {task.label} "
        f"status={result['status']} "
        f"elapsed={result['elapsed_sec']}s "
        f"idvd_points={result['idvd_points']} "
        f"idvg_points={result['idvg_points']} "
        f"final_vg={final_gate_v} "
        f"final_vd={final_drain_v}"
    )


def _run_task(
    task: RunTask,
    base_dir: Path,
    python_exe: str,
    work_root: Path,
    curves_dir: Path,
    final_fields_dir: Path,
    keep_work: bool,
) -> dict[str, str]:
    return _run_one(
        structure_dir=task.structure_dir,
        row=task.row,
        geometry=task.geometry,
        base_dir=base_dir,
        python_exe=python_exe,
        work_root=work_root,
        curves_dir=curves_dir,
        final_fields_dir=final_fields_dir,
        keep_work=keep_work,
    )


def _run_tasks_sequential(
    tasks: list[RunTask],
    total_runs: int,
    results_by_index: dict[int, dict[str, str]],
    status_csv: Path,
    base_dir: Path,
    python_exe: str,
    work_root: Path,
    curves_dir: Path,
    final_fields_dir: Path,
    keep_work: bool,
    continue_on_error: bool,
) -> None:
    for task in tasks:
        print(
            f"[RUN {task.index}/{total_runs}] {task.label} "
            f"B={task.row.bulk_doping} S={task.row.source_doping} "
            f"D={task.row.drain_doping} LDD={task.row.LDD_doping}",
            flush=True,
        )
        try:
            result = _run_task(
                task=task,
                base_dir=base_dir,
                python_exe=python_exe,
                work_root=work_root,
                curves_dir=curves_dir,
                final_fields_dir=final_fields_dir,
                keep_work=keep_work,
            )
            results_by_index[task.index] = result
            write_status_csv(status_csv, _ordered_results(results_by_index))
            print(_done_message(task, total_runs, result), flush=True)
            if result["status"] != "ok" and not continue_on_error:
                raise SystemExit(f"DEVSIM failed for {task.label}")
        except Exception as exc:
            failed = _failed_result(task)
            results_by_index[task.index] = failed
            write_status_csv(status_csv, _ordered_results(results_by_index))
            print(f"[ERROR {task.index}/{total_runs}] {task.label}: {exc}", flush=True)
            if not continue_on_error:
                raise


def _run_tasks_parallel(
    tasks: list[RunTask],
    total_runs: int,
    jobs: int,
    results_by_index: dict[int, dict[str, str]],
    status_csv: Path,
    base_dir: Path,
    python_exe: str,
    work_root: Path,
    curves_dir: Path,
    final_fields_dir: Path,
    keep_work: bool,
    continue_on_error: bool,
) -> None:
    print(f"Running up to {jobs} DEVSIM jobs in parallel", flush=True)
    with concurrent.futures.ProcessPoolExecutor(max_workers=jobs) as executor:
        futures: dict[concurrent.futures.Future[dict[str, str]], RunTask] = {}
        for task in tasks:
            print(
                f"[RUN {task.index}/{total_runs}] {task.label} "
                f"B={task.row.bulk_doping} S={task.row.source_doping} "
                f"D={task.row.drain_doping} LDD={task.row.LDD_doping}",
                flush=True,
            )
            future = executor.submit(
                _run_task,
                task,
                base_dir,
                python_exe,
                work_root,
                curves_dir,
                final_fields_dir,
                keep_work,
            )
            futures[future] = task

        for future in concurrent.futures.as_completed(futures):
            task = futures[future]
            try:
                result = future.result()
                results_by_index[task.index] = result
                write_status_csv(status_csv, _ordered_results(results_by_index))
                print(_done_message(task, total_runs, result), flush=True)
                if result["status"] != "ok" and not continue_on_error:
                    raise SystemExit(f"DEVSIM failed for {task.label}")
            except Exception as exc:
                failed = _failed_result(task)
                results_by_index[task.index] = failed
                write_status_csv(status_csv, _ordered_results(results_by_index))
                print(f"[ERROR {task.index}/{total_runs}] {task.label}: {exc}", flush=True)
                if not continue_on_error:
                    for pending in futures:
                        pending.cancel()
                    raise


def main() -> None:
    sweep_started = time.time()
    args = _parse_args()

    runs_dir = args.runs_dir.resolve()
    base_dir = args.base_dir.resolve()
    geometry_config = args.geometry_config.resolve()
    doping_config = args.doping_config.resolve()
    work_root = args.work_root.resolve()
    curves_dir = args.curves_dir.resolve()
    final_fields_dir = args.final_fields_dir.resolve()
    status_csv = args.status_csv.resolve()
    python_exe = resolve_python_exe(args.python_exe)

    if not runs_dir.exists():
        raise FileNotFoundError(f"Runs directory not found: {runs_dir}")
    if not base_dir.exists():
        raise FileNotFoundError(f"Base directory not found: {base_dir}")
    if not geometry_config.exists():
        raise FileNotFoundError(f"Geometry config not found: {geometry_config}")
    if not doping_config.exists():
        raise FileNotFoundError(f"Doping config not found: {doping_config}")

    work_root.mkdir(parents=True, exist_ok=True)
    curves_dir.mkdir(parents=True, exist_ok=True)
    final_fields_dir.mkdir(parents=True, exist_ok=True)

    geometry_rows = load_geometry_rows(geometry_config)
    configured_structure_ids = [
        run_id
        for run_id in geometry_rows
        if fnmatch.fnmatch(run_id, args.structure_glob)
    ]
    if not configured_structure_ids:
        raise FileNotFoundError(
            f"No geometry config rows match structure pattern '{args.structure_glob}': "
            f"{geometry_config}"
        )

    missing_structures = [
        run_id
        for run_id in configured_structure_ids
        if not (runs_dir / run_id / "gmsh_mos2d.geo").exists()
        or not (runs_dir / run_id / "gmsh_mos2d.msh").exists()
    ]
    if missing_structures:
        missing = ", ".join(missing_structures)
        raise FileNotFoundError(
            "Configured structure mesh files are missing for: "
            f"{missing}. Run sweep_generate_meshes.py first."
        )

    structures = [runs_dir / run_id for run_id in configured_structure_ids]
    rows = load_doping_rows(doping_config)
    total_runs = len(structures) * len(rows)
    jobs = max(1, args.jobs)
    results_by_index: dict[int, dict[str, str]] = {}
    tasks: list[RunTask] = []
    run_index = 0

    for structure_dir in structures:
        geometry = geometry_rows.get(structure_dir.name)
        if geometry is None:
            continue
        for row in rows:
            run_index += 1
            label = f"{structure_dir.name}/{row.run_id}"
            if args.skip_existing and has_existing_outputs(
                structure_dir.name, row, curves_dir, final_fields_dir
            ):
                result = _skipped_result(
                    structure_id=structure_dir.name,
                    row=row,
                    geometry=geometry,
                    curves_dir=curves_dir,
                    final_fields_dir=final_fields_dir,
                )
                results_by_index[run_index] = result
                write_status_csv(status_csv, _ordered_results(results_by_index))
                print(f"[SKIP {run_index}/{total_runs}] {label}: existing outputs found", flush=True)
                continue

            tasks.append(
                RunTask(
                    index=run_index,
                    structure_dir=structure_dir,
                    row=row,
                    geometry=geometry,
                )
            )

    if jobs == 1:
        _run_tasks_sequential(
            tasks=tasks,
            total_runs=total_runs,
            results_by_index=results_by_index,
            status_csv=status_csv,
            base_dir=base_dir,
            python_exe=python_exe,
            work_root=work_root,
            curves_dir=curves_dir,
            final_fields_dir=final_fields_dir,
            keep_work=args.keep_work,
            continue_on_error=args.continue_on_error,
        )
    else:
        _run_tasks_parallel(
            tasks=tasks,
            total_runs=total_runs,
            jobs=jobs,
            results_by_index=results_by_index,
            status_csv=status_csv,
            base_dir=base_dir,
            python_exe=python_exe,
            work_root=work_root,
            curves_dir=curves_dir,
            final_fields_dir=final_fields_dir,
            keep_work=args.keep_work,
            continue_on_error=args.continue_on_error,
        )

    print(f"\nCSV files written in: {curves_dir}")
    print(f"Final field dumps written in: {final_fields_dir}")
    print(f"Run status written: {status_csv}")
    sweep_elapsed = time.time() - sweep_started
    print(f"Total sweep elapsed: {sweep_elapsed:.3f}s ({format_elapsed(sweep_elapsed)})")
    counts = _status_counts(results_by_index)
    print(
        "Convergence summary: "
        f"ok={counts.get('ok', 0)} "
        f"failed={counts.get('failed', 0)}"
        + (
            f" skipped_existing={counts.get('skipped_existing', 0)}"
            if counts.get("skipped_existing", 0)
            else ""
        )
    )


if __name__ == "__main__":
    main()
