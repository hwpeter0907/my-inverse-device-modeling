from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ENV_NAME = "devsim_env"


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_local_config() -> dict[str, object]:
    path = project_root() / "configs" / "local_config.json"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _path_from_config(config: dict[str, object], key: str) -> Path | None:
    value = config.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value).expanduser()


def conda_prefix() -> Path | None:
    value = os.environ.get("CONDA_PREFIX")
    if value:
        return Path(value)
    return None


def conda_env_name() -> str:
    return os.environ.get("CONDA_DEFAULT_ENV", "")


def is_devsim_env() -> bool:
    prefix = conda_prefix()
    return conda_env_name() == ENV_NAME or (prefix is not None and prefix.name == ENV_NAME)


def resolve_python_exe(explicit: str | None = None) -> str:
    if explicit:
        return str(Path(explicit).expanduser())

    config = load_local_config()
    configured = _path_from_config(config, "python_exe")
    if configured and configured.exists():
        return str(configured)

    return sys.executable


def conda_dll_dirs() -> list[Path]:
    roots = []
    prefix = conda_prefix()
    if prefix:
        roots.append(prefix)
    roots.extend([Path(sys.prefix), Path(sys.base_prefix)])

    dirs: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        for candidate in (
            root / "Library" / "bin",
            root / "DLLs",
            root / "Scripts",
            root,
        ):
            key = str(candidate).lower()
            if key not in seen and candidate.exists():
                dirs.append(candidate)
                seen.add(key)
    return dirs


def find_mkl_rt() -> Path | None:
    for dll_dir in conda_dll_dirs():
        matches = sorted(dll_dir.glob("mkl_rt*.dll"))
        if matches:
            return matches[0]
    return None


def resolve_devsim_math_libs() -> str | None:
    config = load_local_config()
    configured = config.get("devsim_math_libs")
    if isinstance(configured, list) and configured:
        return ";".join(str(Path(str(path)).expanduser()) for path in configured)
    if isinstance(configured, str) and configured.strip():
        return configured

    current = os.environ.get("DEVSIM_MATH_LIBS")
    if current:
        return current

    mkl = find_mkl_rt()
    if mkl:
        return f"{mkl};{mkl};{mkl}"
    return None


def build_runtime_env(gmsh_exe: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    config = load_local_config()

    path_entries = [str(path) for path in conda_dll_dirs()]
    if gmsh_exe:
        path_entries.insert(0, str(Path(gmsh_exe).expanduser().resolve().parent))

    extra_path = config.get("extra_path")
    if isinstance(extra_path, list):
        path_entries.extend(str(Path(str(path)).expanduser()) for path in extra_path)

    existing_path = env.get("PATH", "")
    env["PATH"] = os.pathsep.join(path_entries + ([existing_path] if existing_path else []))

    math_libs = resolve_devsim_math_libs()
    if math_libs:
        env["DEVSIM_MATH_LIBS"] = math_libs

    return env


def apply_runtime_environment(gmsh_exe: str | None = None) -> None:
    env = build_runtime_env(gmsh_exe)
    os.environ.update(env)

    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        for dll_dir in conda_dll_dirs():
            os.add_dll_directory(str(dll_dir))


def resolve_gmsh_exe(explicit: str | None = None) -> str:
    if explicit:
        return str(Path(explicit).expanduser())

    gmsh = shutil.which("gmsh", path=build_runtime_env().get("PATH"))
    if gmsh:
        return gmsh

    prefix = conda_prefix() or Path(sys.prefix)
    for candidate in (
        prefix / "Library" / "bin" / "gmsh.exe",
        prefix / "Scripts" / "gmsh.exe",
        prefix / "bin" / "gmsh",
    ):
        if candidate.exists():
            return str(candidate)

    config = load_local_config()
    configured = _path_from_config(config, "gmsh_exe")
    if configured and configured.exists():
        return str(configured)

    return ""
