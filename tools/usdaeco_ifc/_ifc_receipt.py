"""Read exactly the requested native objects into the sync receipt."""

from pxr import Gf
import ifcopenshell.geom
from ._ifc_utils import (world, np_to_gf, pipe_axis_local, pipe_profile, ports_of,
    wall_axis_local, wall_thickness, wall_height, uel, usys, np)
from ._ifc_authoring import remembered
from aeco_sync.closure import JOIN_NAMES
from aeco_sync.identity import guid_to_uuid
from . import _ifc_cctv as cctv


def readback(host, touched):
    records, meshes = list(host.deleted), {}
    entities = [host.f.by_guid(ref) for ref in dict.fromkeys(touched)]
    host.validation_refs = set(touched)
    # Reserve all paths first, including newly discovered fittings and their ports.
    for entity in entities:
        host.path_for(entity)
        if entity.is_a("IfcDistributionElement"):
            for port in usys.get_ports(entity):
                host.path_for(port)
    settings = ifcopenshell.geom.settings()
    settings.set("weld-vertices", True)
    settings.set("unify-shapes", True)
    ifcopenshell.ifcopenshell_wrapper.set_log_format_json()
    for entity in entities:
        path = host.path_for(entity)
        if path is None:
            continue
        item = {"path": str(path), "ref": entity.GlobalId, "id": guid_to_uuid(entity.GlobalId),
                "localRef": "#" + str(entity.id()), "drivers": {}, "derived": {}, "ports": [],
                "joins": {}, "generated": [], "name": entity.Name or entity.is_a()}
        if entity.is_a("IfcTypeProduct"):
            material = uel.get_material(entity, should_skip_usage=True)
            if material and material.is_a("IfcMaterialLayerSet"):
                layers = material.MaterialLayers
                item["drivers"] = {"aeco:buildUp:thicknesses": [l.LayerThickness for l in layers],
                    "aeco:buildUp:priorities": [l.Priority or 0 for l in layers],
                    "aeco:buildUp:functions": [l.Category or "other" for l in layers],
                    "aeco:buildUp:materials": [l.Material.Name or "" if l.Material else "" for l in layers]}
                item["derived"]["aeco:buildUp:totalThickness"] = sum(l.LayerThickness for l in layers)
            records.append(item)
            continue
        item["matrix"] = np_to_gf(world(entity))
        item["classification"] = entity.is_a() + "." + (uel.get_predefined_type(entity) or "NOTDEFINED")
        native_type = uel.get_type(entity)
        if native_type and host.path_for(native_type):
            item["inherits"] = [str(host.path_for(native_type))]
        stored = remembered(entity)
        if cctv.is_camera(entity):
            cctv.receipt(host, entity, item)
        elif entity.is_a("IfcPipeSegment"):
            item["kind"] = "pipe"
            start, end, length = pipe_axis_local(entity)
            profile = pipe_profile(entity)
            outer, inner = 2 * float(profile.Radius), 2 * (float(profile.Radius) - float(getattr(profile, "WallThickness", profile.Radius)))
            nominal = float(stored.get("NominalDiameter", outer))
            prim = host.current.GetPrimAtPath(path)
            if native_type and host.path_for(native_type):
                prim = host.current.GetPrimAtPath(host.path_for(native_type))
            if prim and "NominalDiameter" not in stored:
                names = prim.GetAttribute("aeco:pipeType:nominalDiameters").Get() or []
                outs = prim.GetAttribute("aeco:pipeType:outerDiameters").Get() or []
                nominal = next((n for n, od in zip(names, outs) if abs(outer - od) < 1e-9), nominal)
            item["drivers"]["aeco:pipe:nominalDiameter"] = nominal
            item["derived"].update({"aeco:pipe:outerDiameter": outer, "aeco:pipe:innerDiameter": inner,
                "aeco:pipe:sizeLabel": profile.ProfileName or ""})
            horizontal = np.linalg.norm((world(entity)[:3, :3] @ (end-start))[:2])
            rise = (world(entity)[:3, :3] @ (end-start))[2]
            item["derived"]["aeco:pipe:slope"] = float(rise / horizontal) if horizontal > 1e-9 else 0.
        elif entity.is_a("IfcPipeFitting"):
            item["kind"] = "fitting"
            item["drivers"]["aeco:pipeFitting:origin"] = stored.get("Origin", "authored")
            item["derived"].update({"aeco:pipeFitting:angle": float(stored.get("Angle", 0)),
                "aeco:pipeFitting:bendRadius": float(stored.get("BendRadius", 0))})
            item["generated"] = [entity.GlobalId] if stored.get("Origin") == "generated" else []
        elif entity.is_a("IfcWall"):
            item["kind"] = "wall"
            start, end = wall_axis_local(entity)
            length = float(np.linalg.norm(end - start))
            height, thickness = wall_height(entity), wall_thickness(entity)
            usage = uel.get_material(entity)
            item["drivers"].update({"aeco:wall:height": float(stored.get("Height", height)),
                "aeco:wall:baseOffset": float(stored.get("BaseOffset", 0)),
                "aeco:wall:topOffset": float(stored.get("TopOffset", 0)),
                "aeco:wall:flipped": getattr(usage, "DirectionSense", None) == "NEGATIVE",
                "aeco:wall:locationLine": host.wall_location_token(entity)})
            for key in ("allowJoinAtStart", "allowJoinAtEnd"):
                item["drivers"]["aeco:wall:" + key] = bool(stored.get(key, True))
            item["derived"].update({"aeco:wall:thickness": thickness,
                "aeco:wall:grossSideArea": length * height, "aeco:wall:grossVolume": length * height * thickness})
            item["joins"] = {name: [] for name in JOIN_NAMES}
            for rel in list(entity.ConnectedTo) + list(entity.ConnectedFrom):
                if not rel.is_a("IfcRelConnectsPathElements"):
                    continue
                first = rel.RelatingElement == entity
                other = rel.RelatedElement if first else rel.RelatingElement
                end_name = rel.RelatingConnectionType if first else rel.RelatedConnectionType
                name = dict(zip(("ATSTART", "ATEND", "ATPATH"), JOIN_NAMES)).get(end_name)
                if name and host.path_for(other):
                    item["joins"][name].append(str(host.path_for(other)))
        else:
            continue
        if item["kind"] in ("pipe", "wall"):
            item["drivers"].update({"aeco:axis:start": Gf.Vec3d(*start), "aeco:axis:end": Gf.Vec3d(*end), "aeco:axis:curve": "line"})
            item["derived"]["aeco:axis:length"] = length
        if item["kind"] in ("pipe", "fitting"):
            for port, local in ports_of(entity):
                pp = host.path_for(port)
                peer = usys.get_connected_port(port)
                peer_path = host.path_for(peer) if peer else None
                diameter = nominal if item["kind"] == "pipe" else float(uel.get_pset(port, "Pset_DistributionPortTypePipe", "NominalDiameter") or 0)
                item["ports"].append({"path": str(pp), "ref": port.GlobalId,
                    "id": guid_to_uuid(port.GlobalId), "localRef": "#" + str(port.id()),
                    "matrix": np_to_gf(local), "connected": [str(peer_path)] if peer_path else [],
                    "diameter": diameter, "flow": {"SOURCE": "source", "SINK": "sink", "SOURCEANDSINK": "bidirectional"}.get(port.FlowDirection, "undefined")})
        if item["kind"] == "camera" and not entity.Representation:
            records.append(item)
            continue
        try:
            shape = ifcopenshell.geom.create_shape(settings, entity)
            meshes[entity.GlobalId] = {"verts": list(shape.geometry.verts), "faces": list(shape.geometry.faces),
                "materialIds": list(shape.geometry.material_ids),
                "materials": [{"name": m.name, "instanceId": m.instance_id()} for m in shape.geometry.materials]}
        except Exception as exc:
            host._diagnostics.add("error", "ifcopenshell:geometry", str(exc), [path], True, "regenerate", [entity.GlobalId])
            raise
        finally:
            host.geometry_log(entity)
        records.append(item)
    return {"touched": records, "meshes": meshes, "stamp": host.stamp,
            "diagnostics": host._diagnostics.items, "version": host.version()}
