from __future__ import annotations

import argparse
import csv
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


def _resolve_root(script_file: Path) -> Path:
    return script_file.resolve().parents[1]


def _parse_args() -> argparse.Namespace:
    root = _resolve_root(Path(__file__))
    parser = argparse.ArgumentParser(
        description="Run sweep_run.py repeatedly with increasing -j values."
    )
    parser.add_argument(
        "--jobs",
        type=int,
        nargs="+",
        default=[4, 8, 10, 12],
        help="Job counts to benchmark in order (default: 4 8 10 12).",
    )
    parser.add_argument(
        "--runner-python",
        type=str,
        default=sys.executable,
        help="Python executable used to launch sweep_run.py.",
    )
    parser.add_argument(
        "--sweep-run",
        type=Path,
        default=root / "tools" / "sweep_run.py",
        help="Path to sweep_run.py.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "dataset" / "job_benchmarks",
        help="Directory for benchmark logs and copied run_status CSVs.",
    )
    parser.add_argument(
        "--continue-on-error",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Continue benchmarking later job counts if one run fails.",
    )
    parser.add_argument(
        "sweep_args",
        nargs=argparse.REMAINDER,
        help="Extra arguments passed to sweep_run.py after '--'.",
    )
    return parser.parse_args()


def _format_elapsed(seconds: float) -> str:
    whole_seconds = int(round(seconds))
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _extract_reported_elapsed(log_text: str) -> str:
    match = re.search(r"^Total sweep elapsed:\s*(.+)$", log_text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _extract_summary(log_text: str) -> str:
    match = re.search(r"^Convergence summary:\s*(.+)$", log_text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _run_and_tee(command: list[str], log_path: Path) -> tuple[int, str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    with log_path.open("w", encoding="utf-8", newline="") as log:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
            lines.append(line)
        return process.wait(), "".join(lines)


def _copy_status_csv(root: Path, output_dir: Path, jobs: int) -> Path:
    status_csv = root / "dataset" / "run_status.csv"
    if not status_csv.exists():
        return Path("")
    dst = output_dir / f"run_status_j{jobs}.csv"
    shutil.copy2(status_csv, dst)
    return dst


def _write_benchmark_summary(output_dir: Path, rows: list[dict[str, str]]) -> Path:
    path = output_dir / "benchmark_summary.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "jobs",
                "return_code",
                "measured_elapsed_sec",
                "measured_elapsed_hms",
                "reported_elapsed",
                "convergence_summary",
                "log_file",
                "status_csv",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def main() -> None:
    args = _parse_args()
    root = _resolve_root(Path(__file__))
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    extra_args = list(args.sweep_args)
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]

    rows: list[dict[str, str]] = []
    for jobs in args.jobs:
        print(f"\n=== BENCHMARK_START j={jobs} ===", flush=True)
        command = [
            args.runner_python,
            str(args.sweep_run.resolve()),
            "-j",
            str(jobs),
            *extra_args,
        ]
        log_path = output_dir / f"sweep_j{jobs}.log"
        started = time.time()
        return_code, log_text = _run_and_tee(command, log_path)
        elapsed = time.time() - started
        status_copy = _copy_status_csv(root, output_dir, jobs)

        row = {
            "jobs": str(jobs),
            "return_code": str(return_code),
            "measured_elapsed_sec": f"{elapsed:.3f}",
            "measured_elapsed_hms": _format_elapsed(elapsed),
            "reported_elapsed": _extract_reported_elapsed(log_text),
            "convergence_summary": _extract_summary(log_text),
            "log_file": str(log_path),
            "status_csv": "" if not status_copy else str(status_copy),
        }
        rows.append(row)
        summary_csv = _write_benchmark_summary(output_dir, rows)

        print(
            f"=== BENCHMARK_DONE j={jobs} return_code={return_code} "
            f"elapsed={row['measured_elapsed_sec']}s ({row['measured_elapsed_hms']}) ===",
            flush=True,
        )
        print(f"Benchmark summary written: {summary_csv}", flush=True)

        if return_code != 0 and not args.continue_on_error:
            raise SystemExit(return_code)


if __name__ == "__main__":
    main()
