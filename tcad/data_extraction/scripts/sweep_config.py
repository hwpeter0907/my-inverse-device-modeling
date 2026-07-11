from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DopingRow:
    run_id: str
    bulk_doping: str
    source_doping: str
    drain_doping: str
    LDD_doping: str = ""


@dataclass
class GeometryRow:
    run_id: str
    gate_width: str
    oxide_thickness: str
    spacer_width: str = ""
    LDD_thickness: str = ""


def load_doping_rows(config_csv: Path) -> list[DopingRow]:
    rows: list[DopingRow] = []
    with config_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"run_id", "bulk_doping", "source_doping", "drain_doping"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError(
                f"Doping config format invalid: {config_csv}. "
                "Required columns: run_id, bulk_doping, source_doping, drain_doping"
            )

        for line_no, row in enumerate(reader, start=2):
            run_id = (row.get("run_id") or "").strip()
            bulk = (row.get("bulk_doping") or "").strip()
            source = (row.get("source_doping") or "").strip()
            drain = (row.get("drain_doping") or "").strip()
            LDD = (row.get("LDD_doping") or "").strip()
            if not run_id or not bulk or not source or not drain:
                raise ValueError(
                    f"Missing value at {config_csv}:{line_no} "
                    "(run_id, bulk_doping, source_doping, drain_doping)"
                )
            rows.append(
                DopingRow(
                    run_id=run_id,
                    bulk_doping=bulk,
                    source_doping=source,
                    drain_doping=drain,
                    LDD_doping=LDD,
                )
            )

    if not rows:
        raise ValueError(f"No rows found in doping config: {config_csv}")
    return rows


def load_geometry_rows(config_csv: Path) -> dict[str, GeometryRow]:
    rows: dict[str, GeometryRow] = {}
    with config_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"run_id", "gate_width", "oxide_thickness"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError(
                f"Geometry config format invalid: {config_csv}. "
                "Required columns: run_id, gate_width, oxide_thickness"
            )

        for line_no, row in enumerate(reader, start=2):
            run_id = (row.get("run_id") or "").strip()
            gate_width = (row.get("gate_width") or "").strip()
            oxide_thickness = (row.get("oxide_thickness") or "").strip()
            spacer_width = (row.get("spacer_width") or "").strip()
            LDD_thickness = (row.get("LDD_thickness") or "").strip()
            if not run_id or not gate_width or not oxide_thickness:
                raise ValueError(
                    f"Missing value at {config_csv}:{line_no} "
                    "(run_id, gate_width, oxide_thickness)"
                )
            rows[run_id] = GeometryRow(
                run_id=run_id,
                gate_width=gate_width,
                oxide_thickness=oxide_thickness,
                spacer_width=spacer_width,
                LDD_thickness=LDD_thickness,
            )

    if not rows:
        raise ValueError(f"No rows found in geometry config: {config_csv}")
    return rows