"""IFC geometry helpers promoted from the S4 adapter."""

import numpy as np
from pxr import Usd, UsdGeom, Sdf, Gf, Vt
import ifcopenshell, ifcopenshell.geom
import ifcopenshell.util.element as uel
import ifcopenshell.util.placement as up
import ifcopenshell.util.representation as urep
import ifcopenshell.util.system as usys
import ifcopenshell.util.shape as ush
from aeco_sync.identity import guid_to_uuid
from aeco_sync.stack import find_layer


def T(name):
    return Usd.SchemaRegistry.GetTypeFromSchemaTypeName(name)


def index_ids(stage):
    out = {}
    for prim in stage.TraverseAll():
        a = prim.GetAttribute("aeco:id")
        if a and a.Get():
            out[a.Get()] = prim
    return out


def prim_for(stage_index, entity):
    ref = getattr(entity, "GlobalId", None)
    return stage_index.get(guid_to_uuid(ref)) if ref else None


def world(e):
    return up.get_local_placement(e.ObjectPlacement)


def np_to_gf(m):
    return Gf.Matrix4d(*np.asarray(m).T.flatten().tolist())


def gf_to_np(g):
    return np.array([[g[r][c] for c in range(4)] for r in range(4)], float).T


def base_extrusion(e):
    ex = ush.get_base_extrusions(e) or []
    return ex[0] if ex else None


def pipe_profile(e):
    mat = uel.get_material(e, should_skip_usage=True)
    if mat and mat.is_a("IfcMaterialProfileSet") and mat.MaterialProfiles:
        return mat.MaterialProfiles[0].Profile
    extrusion = base_extrusion(e)
    profile = extrusion.SweptArea if extrusion else None
    return profile if profile and profile.is_a("IfcCircleProfileDef") else None


def pipe_axis_local(e):
    ex = base_extrusion(e)
    d = float(ex.Depth)
    pos = (
        ex.Position.Location.Coordinates
        if ex.Position and ex.Position.Location
        else (0.0, 0.0, 0.0)
    )
    start = np.array([pos[0], pos[1], pos[2] if len(pos) > 2 else 0.0])
    direction = np.array(ex.ExtrudedDirection.DirectionRatios, float)
    return start, start + direction * d, d


def wall_axis_local(e):
    p1, p2 = urep.get_reference_line(e)
    return np.array([p1[0], p1[1], 0.0]), np.array([p2[0], p2[1], 0.0])


def wall_thickness(e):
    return float(sum(l.thickness for l in uel.get_material_layers(e)))


def wall_height(e):
    ex = base_extrusion(e)
    return float(ex.Depth) if ex else 0.0


def ports_of(e):
    """[(port, local_matrix)] with local relative to the element."""
    inv = np.linalg.inv(world(e))
    return [(p, inv @ world(p)) for p in usys.get_ports(e)]
