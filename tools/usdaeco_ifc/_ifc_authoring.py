"""Native pipe construction and wall section operations (IFC length units: metres)."""

import math
import uuid
import numpy as np
from pxr import Gf, UsdGeom
from ifcopenshell.api import run
from ifcopenshell.util.shape_builder import ShapeBuilder
from ._ifc_utils import (world, ports_of, pipe_profile, base_extrusion, wall_height,
                         wall_thickness, gf_to_np, uel, urep, usys, ush)
from aeco_sync.identity import mint_id, uuid_to_guid

BOOKKEEPING = "Pset_AecoSync"
WALL_FIELDS = {"aeco:wall:" + name for name in (
    "height", "baseOffset", "topOffset", "locationLine", "flipped",
    "allowJoinAtStart", "allowJoinAtEnd")}
LAYER_FIELDS = {"aeco:buildUp:" + name for name in (
    "thicknesses", "priorities", "functions", "materials")}


def remember(f, entity, **values):
    pset = uel.get_pset(entity, BOOKKEEPING, should_inherit=False)
    pset = f.by_id(pset["id"]) if pset else run("pset.add_pset", f, product=entity, name=BOOKKEEPING)
    run("pset.edit_pset", f, pset=pset, properties=values)


def remembered(entity):
    return uel.get_pset(entity, BOOKKEEPING, should_inherit=False) or {}


def minted(f, key, kind="element"):
    return uuid_to_guid(mint_id("ifc", key, document=f.by_type("IfcProject")[0].GlobalId, kind=kind))


def frame(direction, origin, lateral=None):
    z = np.asarray(direction, float)
    z /= np.linalg.norm(z)
    x = np.asarray(lateral if lateral is not None else (1, 0, 0), float)
    if abs(np.dot(x, z)) > .999:
        x = np.array((0, 1, 0), float)
    x -= np.dot(x, z) * z
    x /= np.linalg.norm(x)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, np.cross(z, x), z, origin
    return m


def add_ports(f, entity, positions, diameters, directions):
    result = []
    for i, (position, diameter, direction) in enumerate(zip(positions, diameters, directions)):
        port = run("system.add_port", f, element=entity)
        port.GlobalId = minted(f, entity.GlobalId + ":" + str(i), "port")
        port.PredefinedType, port.FlowDirection = "PIPE", "SOURCEANDSINK"
        run("geometry.edit_object_placement", f, product=port, matrix=frame(direction, position))
        pset = run("pset.add_pset", f, product=port, name="Pset_DistributionPortTypePipe")
        run("pset.edit_pset", f, pset=pset, properties={"NominalDiameter": float(diameter)})
        result.append(port)
    return result


def near_end(port):
    element = usys.get_port_element(port)
    m, depth = world(element), float(base_extrusion(element).Depth)
    at_start = abs((np.linalg.inv(m) @ world(port))[2, 3]) < depth / 2
    outward = m[:3, 2] * (-1 if at_start else 1)
    return element, at_start, outward


def move_end(f, port, point):
    from .host import set_pipe_depth_and_ports
    element, at_start, _ = near_end(port)
    m = world(element)
    depth = float(base_extrusion(element).Depth)
    start, end = m[:3, 3], m[:3, 3] + m[:3, 2] * depth
    if at_start:
        start = np.asarray(point)
    else:
        end = np.asarray(point)
    delta = end - start
    if np.linalg.norm(np.cross(delta, m[:3, 2])) > 1e-7 or np.dot(delta, m[:3, 2]) <= 1e-8:
        raise ValueError("Fitting take-out exceeds the segment or leaves its axis")
    set_pipe_depth_and_ports(f, element, float(np.linalg.norm(delta)), start)


def join_ports(host, first, second):
    f = host.f
    if first == second or usys.get_connected_port(first) or usys.get_connected_port(second):
        raise ValueError("A fitting requires two distinct free ports")
    a, _, incoming = near_end(first)
    b, _, outward_b = near_end(second)
    if a == b or not a.is_a("IfcPipeSegment") or not b.is_a("IfcPipeSegment"):
        raise ValueError("Join requires ports on two straight pipe segments")
    outgoing = -outward_b
    pa, pb = world(first)[:3, 3], world(second)[:3, 3]
    profile_a, profile_b = pipe_profile(a), pipe_profile(b)
    ra, rb = float(profile_a.Radius), float(profile_b.Radius)
    angle = math.acos(float(np.clip(np.dot(incoming, outgoing), -1, 1)))
    builder = ShapeBuilder(f)
    radius = 0.0
    if angle > 1e-6:
        if angle >= math.pi - 1e-6 or abs(ra - rb) > 1e-9:
            raise ValueError("A bend requires equal profiles and an angle below 180 degrees; use a separate transition for a size step")
        distance = np.linalg.lstsq(np.column_stack((incoming, -outgoing)), pb - pa, rcond=None)[0]
        corner = pa + incoming * distance[0]
        if np.linalg.norm(corner - (pb + outgoing * distance[1])) > 1e-7:
            raise ValueError("Pipe axes are skew; no planar bend can connect them")
        # ShapeBuilder's radius is the INNER bend radius, not the centreline radius.
        radius = 2 * ra
        centre_radius = radius + ra
        takeout = centre_radius * math.tan(angle / 2)
        start, end = corner - incoming * takeout, corner + outgoing * takeout
        lateral = outgoing - incoming * np.dot(incoming, outgoing)
        matrix = frame(incoming, start, lateral)
        representation, _ = builder.mep_bend_shape(a, 0., 0., angle, radius, (1., 0., 0.), False)
        # The builder omits the bore for circular bends; preserve hollow-pipe geometry.
        for item in representation.Items:
            if item.is_a("IfcSweptDiskSolid") and profile_a.is_a("IfcCircleHollowProfileDef"):
                item.InnerRadius = ra - float(profile_a.WallThickness)
        role = "BEND"
    else:
        if np.linalg.norm(np.cross(pb - pa, incoming)) > 1e-7:
            raise ValueError("Collinear coupling/transition requires coincident axes")
        stub = max(ra, rb)
        if abs(ra - rb) < 1e-9:
            length = 2 * stub
            representation = run("geometry.add_profile_representation", f,
                context=urep.get_context(f, "Model", "Body", "MODEL_VIEW"), profile=profile_a, depth=length)
            role = "CONNECTOR"
        else:
            representation, data = builder.mep_transition_shape(a, b, stub, stub)
            if representation is None:
                raise ValueError("ShapeBuilder could not construct the transition")
            length, role = data["full_transition_length"], "TRANSITION"
        midpoint = (pa + pb) / 2
        start, end = midpoint - incoming * length / 2, midpoint + incoming * length / 2
        matrix = frame(incoming, start)
    move_end(f, first, start)
    move_end(f, second, end)
    fitting = run("root.create_entity", f, ifc_class="IfcPipeFitting", name="Generated " + role.lower(), predefined_type=role)
    fitting.GlobalId = minted(f, str(uuid.uuid4()))
    container = uel.get_container(a)
    if container:
        run("spatial.assign_container", f, products=[fitting], relating_structure=container)
    run("geometry.edit_object_placement", f, product=fitting, matrix=matrix)
    run("geometry.assign_representation", f, product=fitting, representation=representation)
    material = uel.get_material(a, should_skip_usage=True).MaterialProfiles[0].Material
    if material:
        run("material.assign_material", f, products=[fitting], type="IfcMaterial", material=material)
    remember(f, fitting, Origin="generated", Angle=float(angle), BendRadius=float(radius + ra if radius else 0))
    ports = add_ports(f, fitting, [start, end], [2 * ra, 2 * rb], [-incoming, outgoing])
    for native, fitted in zip([first, second], ports):
        run("system.connect_port", f, port1=native, port2=fitted, direction="NOTDEFINED")
    for system in usys.get_element_systems(a):
        run("system.assign_system", f, products=[fitting], system=system)
    host.register_new(fitting, host.path_for(a).GetParentPath().AppendChild("Fitting_" + fitting.GlobalId.replace("$", "_")))
    return [a, b, fitting]


def create_pipe(host, edit):
    f, data = host.f, edit.value
    attrs = data["attributes"]
    prim = host.session.stage.GetPrimAtPath(edit.path)
    tprim = host.current.GetPrimAtPath(data["inherits"][0])
    native_type = host.entity_for_path(tprim.GetPath())
    section = host.creation_section(edit)
    pipe = run("root.create_entity", f, ifc_class="IfcPipeSegment", name=prim.GetDisplayName() or prim.GetName(), predefined_type="RIGIDSEGMENT")
    pipe.GlobalId = uuid_to_guid(attrs["aeco:id"]) if attrs.get("aeco:id") else minted(f, str(uuid.uuid4()))
    run("type.assign_type", f, related_objects=[pipe], relating_type=native_type, should_map_representations=False)
    parent = prim.GetParent()
    while parent and not parent.IsPseudoRoot():
        ref = parent.GetAttribute("aeco:id")
        if ref and ref.Get():
            try:
                candidate = f.by_guid(uuid_to_guid(ref.Get()))
                if candidate.is_a("IfcSpatialStructureElement"):
                    run("spatial.assign_container", f, products=[pipe], relating_structure=candidate)
                    break
            except RuntimeError:
                pass
        parent = parent.GetParent()
    else:
        raise ValueError("A new pipe must be beneath an IFC spatial container")
    start, end = np.array(attrs["aeco:axis:start"], float), np.array(attrs["aeco:axis:end"], float)
    wm = gf_to_np(UsdGeom.XformCache().GetLocalToWorldTransform(prim))
    start, end = (wm @ np.append(start, 1))[:3], (wm @ np.append(end, 1))[:3]
    matrix = frame(end - start, start)
    run("geometry.edit_object_placement", f, product=pipe, matrix=matrix)
    # Occurrence usage selects its profile without mutating an unrelated type occurrence.
    material = uel.get_material(pipe, should_skip_usage=True).MaterialProfiles[0].Material
    profile = f.createIfcCircleHollowProfileDef("AREA", section["label"], None, section["outer"] / 2, (section["outer"] - section["inner"]) / 2)
    pset = run("material.add_material_set", f, name="Pipe section", set_type="IfcMaterialProfileSet")
    run("material.add_profile", f, profile_set=pset, material=material, profile=profile)
    run("material.assign_material", f, products=[pipe], type="IfcMaterialProfileSetUsage", material=pset)
    run("geometry.assign_representation", f, product=pipe, representation=run("geometry.add_profile_representation", f,
        context=urep.get_context(f, "Model", "Body", "MODEL_VIEW"), profile=profile, depth=float(np.linalg.norm(end-start))))
    remember(f, pipe, NominalDiameter=float(section["nominal"]))
    add_ports(f, pipe, [start, end], [section["nominal"]] * 2, [-matrix[:3, 2], matrix[:3, 2]])
    host.register_new(pipe, edit.path)
    return pipe


def wall_location(usage, token):
    layers = usage.ForLayerSet.MaterialLayers
    widths = np.array([float(l.LayerThickness) for l in layers])
    total = sum(widths)
    core = [i for i, layer in enumerate(layers) if (layer.Category or "").lower() == "structure"]
    first, last = (core[0], core[-1] + 1) if core else (0, len(layers))
    values = {"centerline": total / 2, "finishFaceExterior": 0., "finishFaceInterior": total,
              "coreCenterline": (sum(widths[:first]) + sum(widths[:last])) / 2,
              "coreFaceExterior": sum(widths[:first]), "coreFaceInterior": sum(widths[:last])}
    sign = -1 if usage.DirectionSense == "NEGATIVE" else 1
    usage.OffsetFromReferenceLine = float(-sign * values[token])


def wall_edit(host, entity, edit):
    f = host.f
    key = edit.name.split(":")[-1]
    usage = uel.get_material(entity)
    stored = remembered(entity)
    if key in ("height", "baseOffset", "topOffset"):
        base = float(stored.get("BaseOffset", 0.))
        top = float(stored.get("TopOffset", 0.))
        height = float(stored.get("Height", wall_height(entity) - top + base))
        old_base = base
        if key == "height": height = float(edit.value)
        if key == "baseOffset": base = float(edit.value)
        if key == "topOffset": top = float(edit.value)
        depth = height + top - base
        if not np.isfinite(depth) or height <= 0 or depth <= 1e-9:
            raise ValueError("Wall height plus offsets must be positive")
        for ex in ush.get_base_extrusions(entity) or []:
            ex.Depth = depth
        matrix = world(entity).copy()
        matrix[2, 3] += base - old_base
        run("geometry.edit_object_placement", f, product=entity, matrix=matrix, should_transform_children=True)
        remember(f, entity, Height=height, BaseOffset=base, TopOffset=top)
    elif key in ("flipped", "locationLine"):
        token = str(edit.value) if key == "locationLine" else host.wall_location_token(entity)
        if key == "flipped":
            usage.DirectionSense = "NEGATIVE" if edit.value else "POSITIVE"
        wall_location(usage, token)
        remember(f, entity, LocationLine=token)
    elif key.startswith("allowJoin"):
        remember(f, entity, **{key: bool(edit.value)})
        if not edit.value:
            connection = "ATSTART" if key.endswith("Start") else "ATEND"
            run("geometry.disconnect_path", f, element=entity, connection_type=connection)


def edit_layers(host, entity, edit):
    f = host.f
    layers = uel.get_material(entity, should_skip_usage=True).MaterialLayers
    if len(edit.value) != len(layers):
        raise ValueError("Build-up arrays must preserve the layer count; edit the catalog structure upstream")
    key = edit.name.split(":")[-1]
    for layer, value in zip(layers, edit.value):
        if key == "materials":
            material = next((m for m in f.by_type("IfcMaterial") if m.Name == value), None)
            material = material or run("material.add_material", f, name=str(value))
            run("material.edit_layer", f, layer=layer, material=material)
        else:
            if key == "thicknesses" and (not np.isfinite(value) or value < 0):
                raise ValueError("Layer thickness must be finite and non-negative")
            attr = {"thicknesses": "LayerThickness", "priorities": "Priority", "functions": "Category"}[key]
            run("material.edit_layer", f, layer=layer, attributes={attr: float(value) if key == "thicknesses" else value})
    if sum(l.LayerThickness for l in layers) <= 0:
        raise ValueError("Build-up must have positive total thickness")
    return list(uel.get_types(entity))


def rebuild_fitting(host, fitting):
    """Rebuild a generated fitting while preserving the element and port identities."""
    ports = usys.get_ports(fitting)
    peers = [usys.get_connected_port(port) for port in ports]
    if len(ports) != 2 or any(p is None for p in peers):
        raise ValueError("Reconnect or remove a disconnected fitting before changing its section")
    if any(not usys.get_port_element(p).is_a("IfcPipeSegment") for p in peers):
        raise ValueError("Generated fitting regeneration requires two adjacent pipe segments")
    ref, path = fitting.GlobalId, host.path_for(fitting)
    port_refs = [p.GlobalId for p in ports]
    run("root.remove_product", host.f, product=fitting)
    objects = join_ports(host, *peers)
    replacement = objects[-1]
    new_ports = usys.get_ports(replacement)
    replacement.GlobalId = ref
    for port, old_ref in zip(new_ports, port_refs):
        port.GlobalId = old_ref
    host.register_new(replacement, path)
    return objects
