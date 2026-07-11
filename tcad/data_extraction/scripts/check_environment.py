from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from env_config import (
    ENV_NAME,
    apply_runtime_environment,
    conda_env_name,
    conda_prefix,
    find_mkl_rt,
    is_devsim_env,
    project_root,
    resolve_devsim_math_libs,
    resolve_gmsh_exe,
)


def _print_item(name: str, value: object) -> None:
    print(f"{name}: {value}")


def _check_import(module_name: str) -> bool:
    try:
        module = __import__(module_name)
    except Exception as exc:
        print(f"import {module_name}: FAIL ({exc})")
        return False

    version = getattr(module, "__version__", "")
    suffix = f" {version}" if version else ""
    print(f"import {module_name}: OK{suffix}")
    return True


def _check_gmsh(gmsh_exe: str) -> bool:
    if not gmsh_exe:
        print("gmsh: FAIL (not found in conda/PATH or configs/local_config.json)")
        return False

    try:
        completed = subprocess.run(
            [gmsh_exe, "--version"],
            capture_output=True,
            text=True,
            env=os.environ.copy(),
            check=False,
        )
    except Exception as exc:
        print(f"gmsh --version: FAIL ({exc})")
        return False

    output = (completed.stdout or completed.stderr).strip()
    if completed.returncode == 0:
        print(f"gmsh --version: OK {output}")
        print(f"gmsh path: {gmsh_exe}")
        return True

    print(f"gmsh --version: FAIL returncode={completed.returncode}")
    print(output)
    return False


def main() -> None:
    apply_runtime_environment()
    tcad_dir = project_root().parent
    if str(tcad_dir) not in sys.path:
        sys.path.insert(0, str(tcad_dir))

    prefix = conda_prefix()
    mkl = find_mkl_rt()
    math_libs = resolve_devsim_math_libs()
    gmsh_exe = resolve_gmsh_exe()

    _print_item("project root", project_root())
    _print_item("sys.executable", sys.executable)
    _print_item("CONDA_DEFAULT_ENV", conda_env_name() or "(not set)")
    _print_item("CONDA_PREFIX", prefix or "(not set)")
    _print_item(f"is {ENV_NAME}", "YES" if is_devsim_env() else "NO")
    _print_item("mkl_rt*.dll", mkl or "not found")
    _print_item("DEVSIM_MATH_LIBS", math_libs or "not set")
    _print_item("PATH first entries", os.environ.get("PATH", "").split(os.pathsep)[:6])

    ok = True
    ok = _check_import("numpy") and ok
    ok = _check_import("devsim") and ok
    ok = _check_gmsh(gmsh_exe) and ok

    if not is_devsim_env():
        ok = False
        print(f"\nWARNING: 현재 Python이 '{ENV_NAME}' conda 환경으로 보이지 않습니다.")

    if not ok:
        raise SystemExit(1)

    print("\nEnvironment check passed.")


if __name__ == "__main__":
    main()
