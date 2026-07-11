from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

_DLL_DIR_HANDLES = []


def _find_parent_with_child(start: Path, child_name: str) -> Path | None:
    for parent in [start, *start.parents]:
        candidate = parent / child_name
        if candidate.exists():
            return candidate
    return None


def _find_project_root(start: Path) -> Path | None:
    for parent in [start, *start.parents]:
        if parent.name == "data_extraction":
            return parent
    return None


def _load_env_config(start: Path):
    project_root = _find_project_root(start)
    if project_root:
        scripts_dir = project_root / "scripts"
        scripts_dir_str = str(scripts_dir)
        if scripts_dir.exists() and scripts_dir_str not in sys.path:
            sys.path.insert(0, scripts_dir_str)
    try:
        import env_config  # type: ignore[import-not-found]
    except ImportError:
        return None
    return env_config


def configure_devsim_runtime(caller_file: str) -> Path:
    this_file = Path(caller_file).resolve()
    env_config = _load_env_config(this_file.parent)
    devsim_repo = _find_parent_with_child(this_file.parent, "devsim")

    if env_config is not None:
        env_config.apply_runtime_environment()

    if devsim_repo and devsim_repo.exists():
        devsim_repo_str = str(devsim_repo)
        if devsim_repo_str not in sys.path:
            sys.path.insert(0, devsim_repo_str)

    project_root = _find_project_root(this_file.parent)
    if project_root:
        config_dir = project_root / "config"
        config_str = str(config_dir)
        if config_dir.exists() and config_str not in sys.path:
            sys.path.insert(0, config_str)

    if os.name == "nt":
        dll_dirs = []
        conda_prefix = os.environ.get("CONDA_PREFIX")
        if conda_prefix:
            dll_dirs.append(Path(conda_prefix) / "Library" / "bin")
        dll_dirs.append(Path(sys.prefix) / "Library" / "bin")
        dll_dirs.append(Path(sys.base_prefix) / "Library" / "bin")

        seen = set()
        for dll_dir in dll_dirs:
            dll_dir_str = str(dll_dir)
            key = dll_dir_str.lower()
            if key in seen or not dll_dir.exists():
                continue
            seen.add(key)

            if hasattr(os, "add_dll_directory"):
                _DLL_DIR_HANDLES.append(os.add_dll_directory(dll_dir_str))
            os.environ["PATH"] = f"{dll_dir_str};{os.environ.get('PATH', '')}"

            if "DEVSIM_MATH_LIBS" not in os.environ:
                mkl_candidates = sorted(dll_dir.glob("mkl_rt*.dll"))
                if mkl_candidates:
                    mkl_dll = str(mkl_candidates[0])
                    os.environ["DEVSIM_MATH_LIBS"] = f"{mkl_dll};{mkl_dll};{mkl_dll}"

    return this_file.parent


def mesh_path(example_dir: Path, filename: str) -> Path:
    return example_dir / filename


def read_mesh_format(mesh_path: Path) -> str | None:
    try:
        lines = mesh_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    for index, line in enumerate(lines):
        if line.strip() == "$MeshFormat" and index + 1 < len(lines):
            return lines[index + 1].strip().split()[0]
    return None


def read_mesh_counts(mesh_path: Path) -> tuple[int, int] | None:
    try:
        lines = mesh_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    node_count = None
    element_count = None
    for index, line in enumerate(lines):
        text = line.strip()
        if text == "$Nodes" and index + 1 < len(lines):
            try:
                node_count = int(lines[index + 1].strip().split()[0])
            except (TypeError, ValueError, IndexError):
                node_count = None
        elif text == "$Elements" and index + 1 < len(lines):
            try:
                element_count = int(lines[index + 1].strip().split()[0])
            except (TypeError, ValueError, IndexError):
                element_count = None

    if node_count is None or element_count is None:
        return None
    return node_count, element_count


def find_gmsh_executable() -> str | None:
    env_config = _load_env_config(Path(__file__).resolve().parent)
    if env_config is not None:
        gmsh_exe = env_config.resolve_gmsh_exe()
        if gmsh_exe:
            return gmsh_exe

    for candidate in (os.environ.get("GMSH_EXE"), os.environ.get("GMSH_PATH")):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def ensure_msh2(mesh_file: Path) -> None:
    mesh_format = read_mesh_format(mesh_file)
    if mesh_format is None or mesh_format in {"2.1", "2.2"}:
        return

    original_counts = read_mesh_counts(mesh_file)
    gmsh_exe = find_gmsh_executable()
    if not gmsh_exe:
        raise RuntimeError(
            "Gmsh executable was not found. Set GMSH_EXE to gmsh.exe or add Gmsh to PATH."
        )

    temp_mesh_path = None
    with tempfile.NamedTemporaryFile(suffix=".msh", delete=False, dir=str(mesh_file.parent)) as handle:
        temp_mesh_path = Path(handle.name)

    try:
        command = [
            gmsh_exe,
            str(mesh_file),
            "-save",
            "-format",
            "msh2",
            "-o",
            str(temp_mesh_path),
        ]
        completed = subprocess.run(
            command,
            cwd=str(mesh_file.parent),
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Failed to convert mesh to msh2 using Gmsh.\n"
                f"Command: {' '.join(command)}\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )

        temp_format = read_mesh_format(temp_mesh_path)
        temp_counts = read_mesh_counts(temp_mesh_path)
        if temp_format != "2.2":
            raise RuntimeError(
                f"Gmsh conversion completed, but output mesh format is '{temp_format}', not '2.2'."
            )
        if temp_counts is None or temp_counts[0] <= 0 or temp_counts[1] <= 0:
            raise RuntimeError(
                "Converted mesh is empty after Gmsh export. "
                f"Original counts: {original_counts}, converted counts: {temp_counts}."
            )

        temp_mesh_path.replace(mesh_file)
        print(
            f"Converted {mesh_file.name} from MeshFormat {mesh_format} to 2.2 using {gmsh_exe} "
            f"(nodes={temp_counts[0]}, elements={temp_counts[1]})"
        )
    finally:
        if temp_mesh_path and temp_mesh_path.exists():
            temp_mesh_path.unlink()
