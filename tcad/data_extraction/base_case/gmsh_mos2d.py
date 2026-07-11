# Copyright 2013 DEVSIM LLC
#
# SPDX-License-Identifier: Apache-2.0

from runtime_setup import configure_devsim_runtime, mesh_path


_EXAMPLE_DIR = configure_devsim_runtime(__file__)

from python_packages.simple_physics import (  # type: ignore[reportMissingImports]
    GetContactBiasName,
    SetOxideParameters,
    SetSiliconParameters,
    CreateSiliconPotentialOnly,
    CreateSiliconPotentialOnlyContact,
    CreateSiliconDriftDiffusion,
    CreateSiliconDriftDiffusionAtContact,
    CreateOxidePotentialOnly,
    CreateSiliconOxideInterface,
)
from python_packages.ramp import rampbias, printAllCurrents  # type: ignore[reportMissingImports]
from devsim import (
    element_from_edge_model,
    get_contact_current,
    get_contact_list,
    get_node_model_values,
    get_parameter,
    get_region_list,
    node_model,
    set_node_values,
    set_parameter,
    solve,
    write_devices,
)
from python_packages.model_create import CreateSolution  # type: ignore[reportMissingImports]


import gmsh_mos2d_create  # noqa


def _noop(_device):
    return None


def _make_iv_callback(tag):
    def _callback(dev):
        gate_v = get_parameter(device=dev, name=GetContactBiasName("gate"))
        drain_v = get_parameter(device=dev, name=GetContactBiasName("drain"))
        drain_e = get_contact_current(
            device=dev, contact="drain", equation="ElectronContinuityEquation"
        )
        drain_h = get_contact_current(
            device=dev, contact="drain", equation="HoleContinuityEquation"
        )
        drain_i = drain_e + drain_h
        print(
            "IVPOINT\t{0}\t{1:.8e}\t{2:.8e}\t{3:.8e}".format(
                tag, gate_v, drain_v, drain_i
            )
        )

    return _callback


def _log_ivpoint(tag, dev):
    _make_iv_callback(tag)(dev)


def _write_field_dump(tag, dev):
    gate_v = get_parameter(device=dev, name=GetContactBiasName("gate"))
    drain_v = get_parameter(device=dev, name=GetContactBiasName("drain"))
    print("FIELD_DUMP\t{0}\t{1:.8e}\t{2:.8e}".format(tag, gate_v, drain_v))
    write_devices(file=str(mesh_path(_EXAMPLE_DIR, "gmsh_mos2d_dd")), type="vtk")
    write_devices(file=str(mesh_path(_EXAMPLE_DIR, "gmsh_mos2d_dd.dat")), type="tecplot")


def _save_solution(dev, model_names_by_region):
    saved = {}
    for region, model_names in model_names_by_region.items():
        saved[region] = {}
        for model in model_names:
            saved[region][model] = get_node_model_values(
                device=dev, region=region, name=model
            )
    return saved


def _restore_solution(dev, saved):
    for region, models in saved.items():
        for model, values in models.items():
            set_node_values(device=dev, region=region, name=model, values=values)


def _reset_to_zero_bias(dev):
    for c in get_contact_list(device=dev):
        set_parameter(device=dev, name=GetContactBiasName(c), value=0.0)
    solve(type="dc", absolute_error=1.0e30, relative_error=1e-5, maximum_iterations=RESET_MAX_ITER)


def _prepare_zero_bias_from_initial(dev, saved):
    _restore_solution(dev, saved)
    _reset_to_zero_bias(dev)

device = "mos2d"
silicon_regions = ("gate", "bulk")
oxide_regions = ("oxide",)
regions = ("gate", "bulk", "oxide")
interfaces = ("bulk_oxide", "gate_oxide")

for i in regions:
    CreateSolution(device, i, "Potential")

for i in silicon_regions:
    SetSiliconParameters(device, i, 300)
    CreateSiliconPotentialOnly(device, i)

for i in oxide_regions:
    SetOxideParameters(device, i, 300)
    CreateOxidePotentialOnly(device, i, "log_damp")

### Set up contacts
contacts = get_contact_list(device=device)
for i in contacts:
    tmp = get_region_list(device=device, contact=i)
    r = tmp[0]
    print("%s %s" % (r, i))
    CreateSiliconPotentialOnlyContact(device, r, i)
    set_parameter(device=device, name=GetContactBiasName(i), value=0.0)

for i in interfaces:
    CreateSiliconOxideInterface(device, i)

INITIAL_POTENTIAL_MAX_ITER = 500
INITIAL_DD_MAX_ITER = 500
RESET_MAX_ITER = 500

solve(type="dc", absolute_error=1.0e-13, relative_error=1e-12, maximum_iterations=INITIAL_POTENTIAL_MAX_ITER)
solve(type="dc", absolute_error=1.0e-13, relative_error=1e-12, maximum_iterations=INITIAL_POTENTIAL_MAX_ITER)

write_devices(file=str(mesh_path(_EXAMPLE_DIR, "gmsh_mos2d_potentialonly")), type="vtk")

for i in silicon_regions:
    CreateSolution(device, i, "Electrons")
    CreateSolution(device, i, "Holes")
    set_node_values(
        device=device, region=i, name="Electrons", init_from="IntrinsicElectrons"
    )
    set_node_values(device=device, region=i, name="Holes", init_from="IntrinsicHoles")
    CreateSiliconDriftDiffusion(device, i, "mu_n", "mu_p")

for c in contacts:
    tmp = get_region_list(device=device, contact=c)
    r = tmp[0]
    CreateSiliconDriftDiffusionAtContact(device, r, c)

solve(type="dc", absolute_error=1.0e30, relative_error=1e-5, maximum_iterations=INITIAL_DD_MAX_ITER)
zero_bias_solution = _save_solution(
    device,
    {
        "gate": ("Potential", "Electrons", "Holes"),
        "bulk": ("Potential", "Electrons", "Holes"),
        "oxide": ("Potential",),
    },
)

for r in silicon_regions:
    node_model(
        device=device, region=r, name="logElectrons", equation="log(Electrons)/log(10)"
    )


for r in silicon_regions:
    element_from_edge_model(edge_model="ElectricField", device=device, region=r)
    element_from_edge_model(edge_model="ElectronCurrent", device=device, region=r)
    element_from_edge_model(edge_model="HoleCurrent", device=device, region=r)

RAMP_MIN_STEP = 1e-8
RAMP_STEP = 0.005
RAMP_MAX_STEP = 0.1
IV_OUTPUT_STEP = 0.03
RAMP_MAX_ITER = 30
RAMP_REL_ERROR = 1e-4
RAMP_ABS_ERROR = 1e30

print("=== SWEEP_START IDVD_VG1P5 ===")
_prepare_zero_bias_from_initial(device, zero_bias_solution)
rampbias(device, "gate", 1.5, RAMP_STEP, RAMP_MIN_STEP, RAMP_MAX_ITER, RAMP_REL_ERROR, RAMP_ABS_ERROR, _noop, max_step=RAMP_MAX_STEP)
rampbias(device, "drain", 0.0, RAMP_STEP, RAMP_MIN_STEP, RAMP_MAX_ITER, RAMP_REL_ERROR, RAMP_ABS_ERROR, _noop, max_step=RAMP_MAX_STEP)
_log_ivpoint("IDVD_VG1P5", device)
rampbias(
    device,
    "drain",
    3.0,
    RAMP_STEP,
    RAMP_MIN_STEP,
    RAMP_MAX_ITER,
    RAMP_REL_ERROR,
    RAMP_ABS_ERROR,
    _make_iv_callback("IDVD_VG1P5"),
    max_step=RAMP_MAX_STEP,
    callback_interval=IV_OUTPUT_STEP,
)

print("=== SWEEP_START IDVD_VG3P0 ===")
_prepare_zero_bias_from_initial(device, zero_bias_solution)
rampbias(device, "gate", 3.0, RAMP_STEP, RAMP_MIN_STEP, RAMP_MAX_ITER, RAMP_REL_ERROR, RAMP_ABS_ERROR, _noop, max_step=RAMP_MAX_STEP)
rampbias(device, "drain", 0.0, RAMP_STEP, RAMP_MIN_STEP, RAMP_MAX_ITER, RAMP_REL_ERROR, RAMP_ABS_ERROR, _noop, max_step=RAMP_MAX_STEP)
_log_ivpoint("IDVD_VG3P0", device)
rampbias(
    device,
    "drain",
    3.0,
    RAMP_STEP,
    RAMP_MIN_STEP,
    RAMP_MAX_ITER,
    RAMP_REL_ERROR,
    RAMP_ABS_ERROR,
    _make_iv_callback("IDVD_VG3P0"),
    max_step=RAMP_MAX_STEP,
    callback_interval=IV_OUTPUT_STEP,
)
_write_field_dump("IDVD_VG3P0", device)

print("=== SWEEP_START IDVG_VD0P05 ===")
_prepare_zero_bias_from_initial(device, zero_bias_solution)
rampbias(device, "drain", 0.05, RAMP_STEP, RAMP_MIN_STEP, RAMP_MAX_ITER, RAMP_REL_ERROR, RAMP_ABS_ERROR, _noop, max_step=RAMP_MAX_STEP)
rampbias(device, "gate", -1.0, RAMP_STEP, RAMP_MIN_STEP, RAMP_MAX_ITER, RAMP_REL_ERROR, RAMP_ABS_ERROR, _noop, max_step=RAMP_MAX_STEP)
_log_ivpoint("IDVG_VD0P05", device)
rampbias(
    device,
    "gate",
    3.0,
    RAMP_STEP,
    RAMP_MIN_STEP,
    RAMP_MAX_ITER,
    RAMP_REL_ERROR,
    RAMP_ABS_ERROR,
    _make_iv_callback("IDVG_VD0P05"),
    max_step=RAMP_MAX_STEP,
    callback_interval=IV_OUTPUT_STEP,
)

print("=== SWEEP_START IDVG_VD1P5 ===")
_prepare_zero_bias_from_initial(device, zero_bias_solution)
rampbias(device, "drain", 1.5, RAMP_STEP, RAMP_MIN_STEP, RAMP_MAX_ITER, RAMP_REL_ERROR, RAMP_ABS_ERROR, _noop, max_step=RAMP_MAX_STEP)
rampbias(device, "gate", -1.0, RAMP_STEP, RAMP_MIN_STEP, RAMP_MAX_ITER, RAMP_REL_ERROR, RAMP_ABS_ERROR, _noop, max_step=RAMP_MAX_STEP)
_log_ivpoint("IDVG_VD1P5", device)
rampbias(
    device,
    "gate",
    3.0,
    RAMP_STEP,
    RAMP_MIN_STEP,
    RAMP_MAX_ITER,
    RAMP_REL_ERROR,
    RAMP_ABS_ERROR,
    _make_iv_callback("IDVG_VD1P5"),
    max_step=RAMP_MAX_STEP,
    callback_interval=IV_OUTPUT_STEP,
)

printAllCurrents(device)
