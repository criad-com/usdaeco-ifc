"""Camera authoring JSON protocol, executed by IFC and Blender's ifcopenshell."""
import numpy as np
from ifcopenshell.api import run
from ifcopenshell.util import element as uel
from usdaeco_ifc._camera_contract import load, store, persist_sensors


def execute(f, operation):
    op = operation["operation"]
    if op == "publishType":
        try:
            return f.by_guid(operation["ref"])
        except RuntimeError:
            pass
        typ = run("root.create_entity", f, ifc_class="IfcAudioVisualApplianceType", predefined_type="CAMERA", name=operation["name"])
        typ.GlobalId = operation["ref"]
        store(f, typ, "Type", operation["drivers"])
        store(f, typ, "Sensors", operation["sensors"])
        body = next((c for c in f.by_type("IfcGeometricRepresentationSubContext") if c.ContextIdentifier == "Body"), None)
        if body is None:
            model = run("context.add_context", f, context_type="Model")
            body = run("context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=model)
        representation = run("geometry.add_wall_representation", f, context=body, length=.2, height=.1, thickness=.15)
        run("geometry.assign_representation", f, product=typ, representation=representation)
        return typ
    if op == "create":
        e = run("root.create_entity", f, ifc_class="IfcAudioVisualAppliance", predefined_type="CAMERA", name=operation["name"])
        e.GlobalId = operation["ref"]
        run("type.assign_type", f, related_objects=[e], relating_type=f.by_guid(operation["type"]))
        run("spatial.assign_container", f, products=[e], relating_structure=f.by_guid(operation["container"]))
        run("geometry.edit_object_placement", f, product=e, matrix=np.array(operation["matrix"]))
        store(f, e, "Drivers", operation["drivers"])
        persist_sensors(f, e, operation["sensors"])
        if operation.get("phase"):
            p = run("pset.add_pset", f, product=e, name="Pset_AudioVisualApplianceTypeCommon")
            run("pset.edit_pset", f, pset=p, properties={"Status": operation["phase"]})
        return e
    e = f.by_guid(operation["ref"])
    if op == "delete":
        run("root.remove_product", f, product=e)
        return None
    if op == "move":
        run("geometry.edit_object_placement", f, product=e, matrix=np.array(operation["matrix"]))
    elif op == "typeSwap":
        old = {s["name"]: s for s in load(e, "Sensors", [])}
        typ = f.by_guid(operation["type"])
        run("type.assign_type", f, related_objects=[e], relating_type=typ)
        persist_sensors(f, e, [old.get(s["name"], dict(name=s["name"], drivers={}, presets={}, tour=[])) for s in load(typ, "Sensors", [])])
    elif op == "sensors":
        persist_sensors(f, e, operation["sensors"])
    elif op == "drivers":
        store(f, e, "Drivers", operation["drivers"])
    else:
        raise ValueError("Unknown camera operation: " + op)
    return e
