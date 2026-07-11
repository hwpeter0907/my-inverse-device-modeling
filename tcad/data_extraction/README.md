# TCAD Data Extraction

`tcad/data_extraction` is the active workflow for generating DEVSIM MOSFET simulation data.

## Folder Layout

```text
base_case/      Base MOSFET DEVSIM/Gmsh files copied into each run
config/         Sweep CSV files and local Python physics packages
configs/        Machine-local config template, not generated data
scripts/        Environment/path helpers and environment checks
tools/      User-facing scripts for mesh generation, sweeps, benchmarks, and plots
dataset/        Generated output data, ignored by Git
runs/           Generated meshes, logs, and temporary run folders, ignored by Git
```

## Config Files

Main sweep inputs:

```text
config/sweep_geometry.csv
config/sweep_doping.csv
```

Temporary scratch files are kept but intentionally empty:

```text
config/test_geometry.csv
config/test_doping.csv
```

`spacer_width`, `contact_gap`, `contact_width`, and `LDD_thickness` are currently fixed in the base template:

```text
base_case/gmsh_mos2d.geo
base_case/gmsh_mos2d_create.py
```

Current fixed values:

```text
spacer_width  = 5.0e-6 cm
contact_gap   = 5.0e-6 cm
contact_width = 3.0e-5 cm
LDD_thickness = 2.5e-6 cm
```

## Local Machine Config

`configs/local_config.template.json` is a template for computer-specific paths:

```text
gmsh_exe
python_exe
devsim_math_libs
extra_path
```

If needed, copy it to `configs/local_config.json` and edit the paths for the current machine. `local_config.json` is ignored by Git because the desktop and laptop can have different install paths.

## Scripts

`tools/sweep_generate_meshes.py`
: Reads `config/sweep_geometry.csv`, patches the base `.geo`, and writes generated mesh folders under `runs/`.

`tools/sweep_run.py`
: Reads `config/sweep_geometry.csv` and `config/sweep_doping.csv`, runs DEVSIM, and writes output under `dataset/`. The default parallel job count is 4 for the desktop machine.

`tools/sweep_benchmark_jobs.py`
: Benchmarks different `--jobs` values. This is optional now that the desktop default is 4.

`tools/structure_visualization.py`
: Visualizes generated mesh, doping, current density, potential, and electric field maps.

`tools/iv_visualization.py`
: Visualizes generated IdVd/IdVg CSV outputs.

`scripts/check_environment.py`
: Verifies that Python, DEVSIM, Gmsh, and related runtime paths are available.

`scripts/env_config.py`
: Centralizes local path/runtime setup for Python, Gmsh, and Windows DLL paths.

## Output Files

A normal sweep creates local output like:

```text
runs/<structure_id>/gmsh_mos2d.geo
runs/<structure_id>/gmsh_mos2d.msh
runs/<structure_id>/logs/*.log

dataset/<case_id>_IdVd.csv
dataset/<case_id>_IdVg.csv
dataset/final_fields/<case_id>_IdVd_Vg3p0_Vd3p0.dat
dataset/run_status.csv
```

The CSV files are intended for I-V curve model training. The `.dat` field dump is the raw Tecplot snapshot for field-map model preprocessing.

## Common Commands

From `tcad/data_extraction`:

```powershell
conda activate devsim_env
python scripts/check_environment.py
python tools/sweep_generate_meshes.py
python tools/sweep_run.py --jobs 4
```

Or on Windows, run:

```powershell
.\run_sweep.bat
```

This checks the environment, generates meshes, and runs the sweep with `--jobs 4`.

## Git Notes

These are ignored by Git:

```text
configs/local_config.json
dataset/
runs/
```

Commit code and config CSV changes. Do not commit generated simulation datasets unless a separate data-storage decision is made later.