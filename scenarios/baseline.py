"""Build the S4/S5 baseline IFC (metres): a storey, two joined walls with layer-set types,
two port-connected DN50 pipes with hollow-circle profile types, a cold-water system."""

import math, sys
import numpy as np
import ifcopenshell, ifcopenshell.api
from ifcopenshell.api import run


def build(out):
    f = ifcopenshell.file(schema="IFC4X3")
    project = run("root.create_entity", f, ifc_class="IfcProject", name="S4 baseline")
    run("unit.assign_unit", f, length={"is_metric": True, "raw": "METERS"})
    f.by_type("IfcUnitAssignment")[0].Units += (run("unit.add_si_unit", f, unit_type="PLANEANGLEUNIT"),)
    model = run("context.add_context", f, context_type="Model")
    body = run(
        "context.add_context",
        f,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=model,
    )
    plan = run("context.add_context", f, context_type="Plan")
    axis = run(
        "context.add_context",
        f,
        context_type="Plan",
        context_identifier="Axis",
        target_view="GRAPH_VIEW",
        parent=plan,
    )
    site = run("root.create_entity", f, ifc_class="IfcSite", name="Site")
    bld = run("root.create_entity", f, ifc_class="IfcBuilding", name="Building")
    storey = run("root.create_entity", f, ifc_class="IfcBuildingStorey", name="Level 1")
    run("aggregate.assign_object", f, relating_object=project, products=[site])
    run("aggregate.assign_object", f, relating_object=site, products=[bld])
    run("aggregate.assign_object", f, relating_object=bld, products=[storey])

    def rotz(a):
        c, s = math.cos(a), math.sin(a)
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], float)

    block = run("material.add_material", f, name="Block")

    def make_wall(name, p1, p2, thickness=0.2, height=2.4):
        wall = run(
            "root.create_entity",
            f,
            ifc_class="IfcWall",
            name=name,
            predefined_type="STANDARD",
        )
        wtype = run(
            "root.create_entity", f, ifc_class="IfcWallType", name=name + " type"
        )
        lset = run(
            "material.add_material_set",
            f,
            name=name + " layers",
            set_type="IfcMaterialLayerSet",
        )
        layer = run("material.add_layer", f, layer_set=lset, material=block)
        run(
            "material.edit_layer",
            f,
            layer=layer,
            attributes={
                "LayerThickness": thickness,
                "Priority": 50,
                "Category": "structure",
            },
        )
        run(
            "material.assign_material",
            f,
            products=[wtype],
            type="IfcMaterialLayerSet",
            material=lset,
        )
        run("type.assign_type", f, related_objects=[wall], relating_type=wtype)
        run("spatial.assign_container", f, relating_structure=storey, products=[wall])
        d = np.array(p2, float) - np.array(p1, float)
        L = float(np.linalg.norm(d))
        ang = math.atan2(d[1], d[0])
        m = np.eye(4)
        m[:3, :3] = rotz(ang)
        m[:3, 3] = [p1[0], p1[1], 0.0]
        run("geometry.edit_object_placement", f, product=wall, matrix=m)
        run(
            "geometry.assign_representation",
            f,
            product=wall,
            representation=run(
                "geometry.add_axis_representation",
                f,
                context=axis,
                axis=[(0.0, 0.0), (L, 0.0)],
            ),
        )
        run(
            "geometry.assign_representation",
            f,
            product=wall,
            representation=run(
                "geometry.add_wall_representation",
                f,
                context=body,
                length=L,
                height=height,
                thickness=thickness,
            ),
        )
        return wall

    A = make_wall("Wall A", (0, 0), (4, 0))
    B = make_wall("Wall B", (4, 0), (4, 3))
    run("geometry.connect_wall", f, wall1=A, wall2=B)
    for w in (A, B):
        run("geometry.regenerate_wall_representation", f, wall=w)

    steel = run("material.add_material", f, name="Steel")

    def make_pipe(name, start, length, r_out=0.025, t=0.003):
        pipe = run(
            "root.create_entity",
            f,
            ifc_class="IfcPipeSegment",
            name=name,
            predefined_type="RIGIDSEGMENT",
        )
        ptype = run(
            "root.create_entity",
            f,
            ifc_class="IfcPipeSegmentType",
            name=name + " type",
            predefined_type="RIGIDSEGMENT",
        )
        pset = run(
            "material.add_material_set",
            f,
            name=name + " profile",
            set_type="IfcMaterialProfileSet",
        )
        prof = f.createIfcCircleHollowProfileDef("AREA", "DN50", None, r_out, t)
        run("material.add_profile", f, profile_set=pset, material=steel, profile=prof)
        run(
            "material.assign_material",
            f,
            products=[ptype],
            type="IfcMaterialProfileSet",
            material=pset,
        )
        run("type.assign_type", f, related_objects=[pipe], relating_type=ptype)
        run("spatial.assign_container", f, relating_structure=storey, products=[pipe])
        m = np.eye(4)
        m[:3, 0] = (0, 1, 0)
        m[:3, 1] = (0, 0, 1)
        m[:3, 2] = (1, 0, 0)
        m[:3, 3] = start  # local +Z along world +X
        run("geometry.edit_object_placement", f, product=pipe, matrix=m)
        run(
            "geometry.assign_representation",
            f,
            product=pipe,
            representation=run(
                "geometry.add_profile_representation",
                f,
                context=body,
                profile=prof,
                depth=length,
            ),
        )
        ports = []
        for zloc, fd in ((0.0, "SINK"), (length, "SOURCE")):
            port = run("system.add_port", f, element=pipe)
            port.PredefinedType = "PIPE"
            port.FlowDirection = fd
            pm = m.copy()
            pm[:3, 3] = m[:3, 3] + m[:3, 2] * zloc
            run("geometry.edit_object_placement", f, product=port, matrix=pm)
            ports.append(port)
        return pipe, ports

    P1, (p1s, p1e) = make_pipe("Pipe 1", (0, 0, 1), 2.0)
    P2, (p2s, p2e) = make_pipe("Pipe 2", (2, 0, 1), 1.5)
    run("system.connect_port", f, port1=p1e, port2=p2s, direction="SOURCE")
    system = run("system.add_system", f, ifc_class="IfcDistributionSystem")
    system.Name = "Domestic cold water"
    system.PredefinedType = "DOMESTICCOLDWATER"
    run("system.assign_system", f, products=[P1, P2], system=system)
    for material in (block, steel):
        style = run("style.add_style", f, name=material.Name)
        run("style.add_surface_style", f, style=style, ifc_class="IfcSurfaceStyleShading",
            attributes={"SurfaceColour": {"Name": None, "Red": .6, "Green": .6, "Blue": .6}})
        run("style.assign_material_style", f, material=material, style=style, context=body)
    f.write(out)
    return f


if __name__ == "__main__":
    f = build(sys.argv[1])
    print("wrote", sys.argv[1], len(list(f)), "entities")
