"""Additional native baselines and catalog/topology intents for IFC acceptance."""

import numpy as np
from pxr import Usd, UsdGeom
from ifcopenshell.api import run
from usdaeco_ifc._ifc_utils import world, uel, usys
from usdaeco_ifc._ifc_authoring import frame
from scenarios.s4_intent import elements


def configure(native, variant):
    if variant.startswith("free-pipes"):
        pipes = sorted(native.by_type("IfcPipeSegment"), key=lambda p: p.Name)
        for pipe in pipes:
            for port in usys.get_ports(pipe):
                run("system.disconnect_port", native, port=port)
        if variant == "free-pipes-angle":
            run("geometry.edit_object_placement", native, product=pipes[1],
                matrix=frame((0, 1, 0), (2, 0, 1)), should_transform_children=True)
        if variant == "free-pipes-step":
            profile = uel.get_material(pipes[1], should_skip_usage=True).MaterialProfiles[0].Profile
            profile.Radius, profile.ProfileName = .04, "DN80"
    if variant == "unjoined-walls":
        for rel in list(native.by_type("IfcRelConnectsPathElements")):
            run("geometry.disconnect_path", native, relating_element=rel.RelatingElement, related_element=rel.RelatedElement)
    if variant == "wall-type":
        wall = next(w for w in native.by_type("IfcWall") if w.Name == "Wall B")
        layer = uel.get_material(wall, should_skip_usage=True).MaterialLayers[0]
        run("material.edit_layer", native, layer=layer, attributes={"LayerThickness": .3})
        run("geometry.regenerate_wall_representation", native, wall=wall)


def select(stage, selector):
    name, _, suffix = selector.partition("/")
    prim = elements(stage)[name]
    if suffix == "type":
        return stage.GetPrimAtPath(prim.GetInherits().GetAllDirectInherits()[0])
    if suffix:
        ports = [p for p in prim.GetChildren() if p.GetTypeName() == "AecoPort"]
        ports.sort(key=lambda p: UsdGeom.Xformable(p).GetLocalTransformation().ExtractTranslation()[2])
        return ports[0 if suffix == "start" else -1]
    return prim


def author_extra(session, case):
    stage = session.stage
    session.capture_base()
    with Usd.EditContext(stage, session.intent):
        for selector, name, targets in case.get("relations", []):
            select(stage, selector).GetRelationship(name).SetTargets([select(stage, t).GetPath() for t in targets])
        for selector, target in case.get("type", []):
            select(stage, selector).GetInherits().SetInherits([select(stage, target).GetPath()])
        for selector, name, val in case.get("typeRaw", []):
            select(stage, selector).GetAttribute(name).Set(val)
    session.intent.Save()
