from __future__ import annotations

import csv
import os
import time
from pathlib import Path

from sweep_config import DopingRow, GeometryRow


def extract_ivpoint_count(stdout_text: str) -> int:
    return sum(1 for line in stdout_text.splitlines() if line.startswith("IVPOINT"))


def extract_final_bias(stdout_text: str) -> tuple[float, float] | None:
    for raw in stdout_text.splitlines():
        line = raw.strip()
        if not line.startswith("FIELD_DUMP"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            return (float(parts[2]), float(parts[3]))
        except ValueError:
            continue

    final_bias: tuple[float, float] | None = None
    for raw in stdout_text.splitlines():
        line = raw.strip()
        if not line.startswith("IVPOINT"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            final_bias = (float(parts[2]), float(parts[3]))
        except ValueError:
            continue
    return final_bias


def parse_ivpoints(stdout_text: str) -> dict[str, list[tuple[float, float, float]]]:
    points: dict[str, list[tuple[float, float, float]]] = {}
    for raw in stdout_text.splitlines():
        line = raw.strip()
        if not line.startswith("IVPOINT"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        tag = parts[1]
        try:
            gate_v = float(parts[2])
            drain_v = float(parts[3])
            drain_i = float(parts[4])
        except ValueError:
            continue
        points.setdefault(tag, []).append((gate_v, drain_v, drain_i))
    return points


def write_curve_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class _StatusCsvLock:
    def __init__(self, path: Path, timeout_sec: float = 120.0) -> None:
        self.path = path.with_suffix(path.suffix + ".lock")
        self.timeout_sec = timeout_sec
        self.fd: int | None = None

    def __enter__(self) -> None:
        started = time.time()
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode("ascii"))
                return
            except FileExistsError:
                if time.time() - started >= self.timeout_sec:
                    raise TimeoutError(f"Timed out waiting for status CSV lock: {self.path}")
                time.sleep(0.2)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        self.path.unlink(missing_ok=True)


def write_status_csv(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "structure_id",
        "doping_run_id",
        "gate_width",
        "oxide_thickness",
        "bulk_doping",
        "source_doping",
        "drain_doping",
        "LDD_doping",
        "status",
        "return_code",
        "elapsed_sec",
        "ivpoint_count",
        "final_gate_v",
        "final_drain_v",
        "idvd_csv",
        "idvg_csv",
        "idvd_points",
        "idvg_points",
        "stdout_log",
        "stderr_log",
        "work_dir",
        "final_dat",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with _StatusCsvLock(path):
        merged_rows: list[dict[str, str]] = []
        row_by_key: dict[tuple[str, str], dict[str, str]] = {}

        if path.exists():
            with path.open("r", encoding="utf-8", newline="") as handle:
                for existing in csv.DictReader(handle):
                    key = (existing.get("structure_id", ""), existing.get("doping_run_id", ""))
                    if key == ("", ""):
                        continue
                    normalized = {name: existing.get(name, "") for name in fieldnames}
                    row_by_key[key] = normalized
                    merged_rows.append(normalized)

        for row in rows:
            key = (row.get("structure_id", ""), row.get("doping_run_id", ""))
            normalized = {name: row.get(name, "") for name in fieldnames}
            if key in row_by_key:
                row_by_key[key].update(normalized)
            else:
                row_by_key[key] = normalized
                merged_rows.append(normalized)

        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(merged_rows)


def format_doping_label(value: str) -> str:
    token = value.strip().lower().replace("+", "")
    token = token.replace(".0e", "e")
    token = token.replace(".", "p")
    out = []
    for ch in token:
        if ch.isalnum():
            out.append(ch)
    return "".join(out) or "NA"


def format_elapsed(seconds: float) -> str:
    whole_seconds = int(round(seconds))
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def curve_name_stem(structure_id: str, row: DopingRow) -> str:
    bulk = format_doping_label(row.bulk_doping)
    source = format_doping_label(row.source_doping)
    drain = format_doping_label(row.drain_doping)
    LDD = format_doping_label(row.LDD_doping) if row.LDD_doping else ""
    LDD_suffix = f"LDD{LDD}" if LDD else ""
    if source == drain:
        return f"{structure_id}B{bulk}SD{source}{LDD_suffix}"
    return f"{structure_id}B{bulk}S{source}D{drain}{LDD_suffix}"


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
