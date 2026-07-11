# Copyright 2013 DEVSIM LLC
#
# SPDX-License-Identifier: Apache-2.0

from runtime_setup import configure_devsim_runtime, ensure_msh2, mesh_path


_EXAMPLE_DIR = configure_devsim_runtime(__file__)
_MESH_FILE = mesh_path(_EXAMPLE_DIR, "gmsh_mos2d.msh")
ensure_msh2(_MESH_FILE)

from devsim import (
    add_gmsh_contact,
    add_gmsh_interface,
    add_gmsh_region,
    create_device,
    create_gmsh_mesh,
    finalize_mesh,
    node_model,
    write_devices,
)

device = "mos2d"

gate_width = 1.0e-5
diffusion_width = 0.4

air_thickness = 1e-7
oxide_thickness = 1e-5
gate_thickness = 1e-5
device_thickness = 1e-4
diffusion_thickness = 5e-6
spacer_width = 5.0e-6
contact_gap = 5.0e-6
contact_width = 3.0e-5
device_width = gate_width + 2 * spacer_width + 2 * contact_width
LDD_thickness = 2.5e-6

x_diffusion_decay = 2e-7
y_diffusion_decay = 5e-7

# p doping
bulk_doping = 1e15
body_doping = 1e19
# n doping
drain_doping = 1e19
source_doping = 1e19
LDD_doping = 1e17
gate_doping = 1e20

y_channel_spacing = 1e-8
y_diffusion_spacing = 1e-6
y_gate_top_spacing = 1e-8
y_gate_mid_spacing = gate_thickness * 0.25
y_gate_bot_spacing = 1e-8
y_oxide_mid_spacing = oxide_thickness * 0.25
x_channel_spacing = 1e-6
x_diffusion_spacing = 1e-5
max_y_spacing = 1e-4
max_x_spacing = 1e-2
y_bulk_mid_spacing = device_thickness * 0.25
y_bulk_bottom_spacing = 1e-8

x_bulk_left = 0.0
x_bulk_right = x_bulk_left + device_width
x_center = 0.5 * (x_bulk_left + x_bulk_right)
x_gate_left = x_center - 0.5 * (gate_width)
x_gate_right = x_center + 0.5 * (gate_width)
x_spacer_left = x_gate_left - spacer_width
x_spacer_right = x_gate_right + spacer_width
x_source_contact_right = x_spacer_left - contact_gap
x_drain_contact_left = x_spacer_right + contact_gap
x_device_left = x_bulk_left - air_thickness
x_device_right = x_bulk_right + air_thickness

y_bulk_top = 0.0
y_oxide_top = y_bulk_top - oxide_thickness
y_oxide_mid = 0.5 * (y_oxide_top + y_bulk_top)
y_gate_top = y_oxide_top - gate_thickness
y_gate_mid = 0.5 * (y_gate_top + y_oxide_top)
y_device_top = y_gate_top - air_thickness
y_bulk_bottom = y_bulk_top + device_thickness
y_bulk_mid = 0.5 * (y_bulk_top + y_bulk_bottom)
y_device_bottom = y_bulk_bottom + air_thickness
y_diffusion = y_bulk_top + diffusion_thickness
y_LDD = y_bulk_top + LDD_thickness

create_gmsh_mesh(file=str(_MESH_FILE), mesh="mos2d")
add_gmsh_region(mesh="mos2d", gmsh_name="bulk", region="bulk", material="Silicon")
add_gmsh_region(mesh="mos2d", gmsh_name="oxide", region="oxide", material="Silicon")
add_gmsh_region(mesh="mos2d", gmsh_name="gate", region="gate", material="Silicon")
add_gmsh_contact(
    mesh="mos2d",
    gmsh_name="drain_contact",
    region="bulk",
    name="drain",
    material="metal",
)
add_gmsh_contact(
    mesh="mos2d",
    gmsh_name="source_contact",
    region="bulk",
    name="source",
    material="metal",
)
add_gmsh_contact(
    mesh="mos2d", gmsh_name="body_contact", region="bulk", name="body", material="metal"
)
add_gmsh_contact(
    mesh="mos2d", gmsh_name="gate_contact", region="gate", name="gate", material="metal"
)
add_gmsh_interface(
    mesh="mos2d",
    gmsh_name="gate_oxide_interface",
    region0="gate",
    region1="oxide",
    name="gate_oxide",
)
add_gmsh_interface(
    mesh="mos2d",
    gmsh_name="bulk_oxide_interface",
    region0="bulk",
    region1="oxide",
    name="bulk_oxide",
)
finalize_mesh(mesh="mos2d")
create_device(mesh="mos2d", device="mos2d")

#### all variable substitutions are immediate, since they are locked into the mesh
mydict = {}
mydict["drain_doping"] = drain_doping
mydict["LDD_doping"] = LDD_doping
mydict["body_doping"] = body_doping
mydict["gate_doping"] = gate_doping
mydict["source_doping"] = source_doping
mydict["bulk_doping"] = bulk_doping
mydict["x_gate_left"] = x_gate_left
mydict["x_gate_right"] = x_gate_right
mydict["x_spacer_left"] = x_spacer_left
mydict["x_spacer_right"] = x_spacer_right
mydict["x_diffusion_decay"] = x_diffusion_decay
mydict["y_diffusion"] = y_diffusion
mydict["y_LDD"] = y_LDD
mydict["y_bulk_bottom"] = y_bulk_bottom
mydict["y_diffusion_decay"] = y_diffusion_decay

node_model(
    name="Donors",
    device=device,
    region="gate",
    equation="%(gate_doping)1.15e + 1" % (mydict),
)
node_model(name="Acceptors", device=device, region="gate", equation="1")
node_model(
    name="NetDoping", device=device, region="gate", equation="Donors - Acceptors"
)

node_model(
    name="DrainDoping",
    device=device,
    region="bulk",
    equation="0.25*%(drain_doping)1.15e*erfc((x-%(x_spacer_left)1.15e)/%(x_diffusion_decay)1.15e)*erfc((y-%(y_diffusion)1.15e)/%(y_diffusion_decay)1.15e)"
    % mydict,
)
node_model(
    name="SourceDoping",
    device=device,
    region="bulk",
    equation="0.25*%(source_doping)1.15e*erfc(-(x-%(x_spacer_right)1.15e)/%(x_diffusion_decay)1.15e)*erfc((y-%(y_diffusion)1.15e)/%(y_diffusion_decay)1.15e)"
    % mydict,
)
node_model(
    name="LeftLDDDoping",
    device=device,
    region="bulk",
    equation="0.25*%(LDD_doping)1.15e*erfc((x-%(x_gate_left)1.15e)/%(x_diffusion_decay)1.15e)*erfc((y-%(y_LDD)1.15e)/%(y_diffusion_decay)1.15e)"
    % mydict,
)
node_model(
    name="RightLDDDoping",
    device=device,
    region="bulk",
    equation="0.25*%(LDD_doping)1.15e*erfc(-(x-%(x_gate_right)1.15e)/%(x_diffusion_decay)1.15e)*erfc((y-%(y_LDD)1.15e)/%(y_diffusion_decay)1.15e)"
    % mydict,
)
node_model(
    name="BodyDoping",
    device=device,
    region="bulk",
    equation="0.5*%(body_doping)1.15e*erfc(-(y-%(y_bulk_bottom)1.15e)/%(y_diffusion_decay)1.15e)"
    % mydict,
)
node_model(
    name="Donors",
    device=device,
    region="bulk",
    equation="DrainDoping + SourceDoping + LeftLDDDoping + RightLDDDoping + 1",
)
node_model(
    name="Acceptors",
    device=device,
    region="bulk",
    equation="%(bulk_doping)1.15e + BodyDoping" % mydict,
)
node_model(
    name="NetDoping", device=device, region="bulk", equation="Donors - Acceptors"
)

write_devices(file=str(mesh_path(_EXAMPLE_DIR, "gmsh_mos2d_out.msh")))
