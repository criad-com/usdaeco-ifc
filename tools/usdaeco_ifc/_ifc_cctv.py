"""Point-placed IFC cameras; native property sets and catalog representations.

Standard properties describe the first head. The project Pset retains all
sensor/type drivers. PanTiltZoomPreset is an IfcPropertyTableValue: stable
sensor/preset labels map to JSON poses (degrees, IFC tilt positive up, mm).
"""
import json
import re
import numpy as np
from ifcopenshell.api import run
from ifcopenshell.util import element as uel
from pxr import Gf, Sdf, UsdGeom
from aeco_sync.cctv import SENSOR, PRESET, CONTROLS, rigid_z, optics_parameters, controls, is_sensor
from aeco_sync.identity import guid_to_uuid, uuid_to_guid, mint_id
from aeco_sync.stack import value
from ._ifc_utils import gf_to_np

from ._camera_contract import (CONTRACT, PSET, STANDARD, pset, stored, load,
    mirror, prefer, table_presets, standard_values)
from . import _camera_contract as contract


def is_camera(entity):
    return entity.is_a("IfcAudioVisualAppliance") and uel.get_predefined_type(entity) == "CAMERA"


def store(f, e, key, val):
    contract.store(f, e, key, value(val))


def persist_sensors(f, e, sensors):
    contract.persist_sensors(f, e, value(sensors))


def parameters(e):
    """Project family parameters retain their exported names; optics are mm/deg."""
    result = {}
    for name, fields in uel.get_psets(e).items():
        if name not in (PSET, STANDARD):
            result.update({key.replace("_", " "): val for key, val in fields.items() if key != "id"})
    return result


SENSOR_FIELDS = CONTROLS | {"projection", "spectrum", "focalRange", "hfovRange", "vfovRange", "sensorSize", "pixels", "offset", "panRange", "tiltRange", "motorised", "tour"}
TYPE_FIELDS = {"aeco:cctvType:outdoor", "aeco:cctvType:irRange", "aeco:type:model", "aeco:type:manufacturer"}


def known_drivers(data):
    return {k: v for k, v in data.items() if k.startswith(SENSOR) and k[len(SENSOR):] in SENSOR_FIELDS}


def type_drivers(e):
    standard = standard_values(e)
    fallback = {"aeco:cctvType:outdoor": standard["IsOutdoors"]} if "IsOutdoors" in standard else {}
    return prefer({k: v for k, v in load(e, "Type", {}).items() if k in TYPE_FIELDS}, fallback, e.GlobalId)


def type_data(e, host=None):
    if e is None:
        return []
    a = load(e, "Sensors", [])
    c = optics_parameters(parameters(e))
    b = {k: v for k, v in mirror(e).items() if k == SENSOR + "pixels"}
    # The native tiers are sufficient; never treat a previous USD receipt as
    # a more authoritative source than independently changed IFC properties.
    sensors = a or ([dict(name="Sensor_0", drivers={})] if c or b else [])
    return [dict(name=s["name"], drivers={**c, **prefer(known_drivers(s.get("drivers", {})), b if i == 0 else {}, e.GlobalId)})
            for i, s in enumerate(sensors)]


def sensor_data(e, typ, host=None):
    a = load(e, "Sensors", [])
    ts = type_data(typ, host)
    b = mirror(e)
    parameters_c = parameters(e)
    tables = table_presets(e)
    standard = uel.get_psets(e, should_inherit=False).get(STANDARD, {})
    has_table = bool(standard and any(p.Name == "PanTiltZoomPreset" for p in e.file.by_id(standard["id"]).HasProperties))
    entries = a or [dict(name=s["name"], drivers={}) for s in ts]
    if not entries and (b or controls(parameters_c)):
        entries = [dict(name="Sensor_0", drivers={})]
    sensors = []
    for i, entry in enumerate(entries):
        ad = known_drivers(entry.get("drivers", {}))
        head_b = dict(b) if i == 0 else {}
        # Datasheet values belong on the type unless the occurrence explicitly
        # overrides them. A standard mirror must not freeze inherited optics.
        if i == 0 and ts and SENSOR + "pixels" in ts[0]["drivers"] and SENSOR + "pixels" not in ad:
            prefer({SENSOR + "pixels": ts[0]["drivers"][SENSOR + "pixels"]}, head_b, e.GlobalId)
            head_b.pop(SENSOR + "pixels", None)
        d = {**controls(parameters_c, i + 1 if len(entries) > 1 else None), **prefer(ad, head_b, e.GlobalId)}
        presets = entry.get("presets", tables.get(entry["name"], {}))
        if "presets" not in entry and not has_table:
            presets = {f"Preset_{n}": {field: v for key, v in controls(parameters_c, n).items() if (field := key[len(SENSOR):]) in ("pan", "tilt", "focalLength")}
                       for n in range(1, 5) if parameters_c.get(f"Preset {n}")}
        sensors.append(dict(name=entry["name"], drivers=d, presets=presets,
                            tour=entry.get("tour", d.pop(SENSOR + "tour", [])), ref=e.GlobalId, nativeIndex=i + 1))
    return sensors


def receipt(host, e, item):
    typ = uel.get_type(e)
    item["kind"] = "camera"
    item["drivers"] = {k: v for k, v in load(e, "Drivers", {}).items() if k in ("aeco:cctv:scenario", "aeco:cctv:mount")}
    item["sensors"] = sensor_data(e, typ, host)
    if typ:
        path = host.path_for(typ)
        item["type"] = dict(path=str(path), ref=typ.GlobalId, id=guid_to_uuid(typ.GlobalId),
                            localRef="#" + str(typ.id()), sensors=type_data(typ, host), drivers=type_drivers(typ))
        item["inherits"] = [str(path)]
    for sensor in item["sensors"]:
        sensor["path"] = item["path"] + "/" + sensor["name"]
    return item


def catalog_data(host, path):
    prim = host.current.GetPrimAtPath(path)
    if not prim or not prim.IsAbstract() or not prim.HasAPI("AecoCctvCameraTypeAPI"):
        raise ValueError(f"Unbound type {path}: needs a catalog class with AecoCctvCameraTypeAPI")
    sensors = []
    for child in prim.GetAllChildren():
        if not is_sensor(child):
            continue
        if not child.IsA(UsdGeom.Camera) or child.GetName() != f"Sensor_{len(sensors)}":
            raise ValueError(f"Type {path}: sensors must be ordered Camera children Sensor_0 ... Sensor_n")
        drivers = {a.GetName(): value(a.Get()) for a in child.GetAttributes()
                   if a.GetName().startswith(SENSOR) and not a.GetMetadata("aecoDerived") and a.HasValue()}
        for field in ("focalRange", "hfovRange", "vfovRange", "pixels"):
            pair = drivers.get(SENSOR + field)
            if not pair or len(pair) != 2 or not all(x > 0 for x in pair):
                raise ValueError(f"Type {path}/{child.GetName()}: missing or invalid {field}")
        sensors.append(dict(name=child.GetName(), drivers=drivers))
    if not sensors:
        raise ValueError(f"Unbound type {path}: no Camera sensor children")
    drivers = {a.GetName(): value(a.Get()) for a in prim.GetAttributes()
               if a.GetName().startswith(("aeco:cctvType:", "aeco:type:")) and not a.GetMetadata("aecoDerived") and a.HasValue()}
    json.dumps(dict(drivers=drivers, sensors=sensors), allow_nan=False)
    ref = uuid_to_guid(mint_id("ifc", str(path), document=host.f.by_type("IfcProject")[0].GlobalId, kind="type"))
    return dict(operation="publishType", path=str(path), ref=ref, name=prim.GetName(), drivers=drivers, sensors=sensors)


def require_type(host, path):
    try:
        typ = host.entity_for_path(path)
    except ValueError:
        return catalog_data(host, path)
    if not typ.is_a("IfcAudioVisualApplianceType") or typ.PredefinedType != "CAMERA":
        raise ValueError(f"Type {path} is not IfcAudioVisualApplianceType/CAMERA")
    if not type_data(typ, host):
        raise ValueError(f"Type {path} has no sensor optics")
    return None


def refusal_reason(host, edit):
    try:
        if edit.operation == "create":
            data = edit.value
            if len(data["inherits"]) != 1:
                return "Camera creation requires exactly one catalog type"
            require_type(host, data["inherits"][0])
            attrs = data["attributes"]
            allowed = {"aeco:id", "aeco:phase", "aeco:cctv:scenario", "aeco:cctv:mount", "xformOp:transform", "xformOpOrder"}
            if set(attrs) - allowed:
                return "Disallowed camera creation attribute: " + ", ".join(sorted(set(attrs) - allowed))
            if data["relationships"]:
                return "Camera creation relationships are unsupported: " + ", ".join(data["relationships"])
            if set(data["metadata"]) - {"specifier", "typeName", "apiSchemas", "inheritPaths", "displayName"}:
                return "Disallowed camera creation metadata"
            if not rigid_z(attrs.get("xformOp:transform", Gf.Matrix4d(1))):
                return "Non-rigid camera transform (only translation and rotation about Z are supported)"
            container = host.entity_for_path(Sdf.Path(edit.path).GetParentPath())
            if not container.is_a("IfcSpatialStructureElement"):
                return f"Camera container {container.Name} is not a space, storey or site"
            if attrs.get("aeco:id"):
                try:
                    host.f.by_guid(uuid_to_guid(attrs["aeco:id"]))
                    return "Camera identity already exists in the native file"
                except RuntimeError:
                    pass
            catalog = host.current.GetPrimAtPath(data["inherits"][0])
            for sensor in data["sensors"]:
                if not catalog.GetChild(sensor["name"]):
                    return f"Type {data['inherits'][0]} has no sensor {sensor['name']}"
                for name in sensor["drivers"]:
                    if name not in {SENSOR + field for field in CONTROLS | {"tour"}} and not re.fullmatch(r"aeco:cctvPreset:[A-Za-z_][A-Za-z_0-9]*:(pan|tilt|focalLength|dwell|home)", name):
                        return f"Disallowed camera sensor attribute: {name}"
            return None
        e = host.f.by_guid(edit.ref)
        if not is_camera(e):
            return "Native element is not an IfcAudioVisualAppliance/CAMERA"
        if edit.operation == "activation":
            return None if edit.value is False and not (edit.section or {}).get("sensor") else "Only camera occurrence deletion is supported"
        if edit.operation == "primMetadata":
            if edit.name == "apiSchemas" and (edit.section or {}).get("sensor"):
                current = host.current.GetPrimAtPath(edit.path).GetAppliedSchemas()
                ordinary = lambda apis: {a for a in apis if not a.startswith("AecoCctvPresetAPI:")}
                if ordinary(edit.value) != ordinary(current):
                    return "Only preset API membership may change on a camera sensor"
                return None
            if edit.name != "inheritPaths" or len(edit.value) != 1:
                return "Only one camera type inherit is supported"
            require_type(host, edit.value[0])
            return None
        if edit.operation != "attribute":
            return f"Unsupported camera operation {edit.operation}"
        if edit.name == "xformOpOrder":
            return None if list(edit.value) == ["xformOp:transform"] else "Camera requires one matrix transform"
        if edit.name == "xformOp:transform":
            return None if rigid_z(edit.value) else "Non-rigid camera transform (only translation and rotation about Z are supported)"
        if edit.name.startswith(SENSOR) and edit.name[len(SENSOR):] in CONTROLS | {"tour"}:
            return None
        if re.fullmatch(r"aeco:cctvPreset:[A-Za-z_][A-Za-z_0-9]*:(pan|tilt|focalLength|dwell|home)", edit.name):
            return None
        if edit.name in ("aeco:cctv:scenario", "aeco:cctv:mount"):
            return None
        return f"Disallowed camera attribute {edit.name}; datasheet optics require a catalog type change"
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        return str(exc)


def supports(host, edit):
    host._unsupported_reason = refusal_reason(host, edit)
    return host._unsupported_reason is None


def execute(host, operation):
    from ._camera_ops import execute as native_execute
    entity = native_execute(host.f, value(operation))
    host.camera_operations.append(value(operation))
    if operation["operation"] in ("publishType", "create"):
        host.register_new(entity, operation["path"])
    return entity


def ensure_type(host, path):
    publication = require_type(host, path)
    if publication:
        return execute(host, publication)
    return host.entity_for_path(path)


def create_camera(host, edit):
    data, f = edit.value, host.f
    typ = ensure_type(host, data["inherits"][0])
    identity = data["attributes"].get("aeco:id") or mint_id("ifc", edit.path, document=f.by_type("IfcProject")[0].GlobalId)
    parent_path = Sdf.Path(edit.path).GetParentPath()
    container = host.entity_for_path(parent_path)
    matrix = Gf.Matrix4d(data["attributes"].get("xformOp:transform", Gf.Matrix4d(1)))
    matrix *= UsdGeom.XformCache().GetLocalToWorldTransform(host.current.GetPrimAtPath(parent_path))
    sensors = [dict(name=s["name"], drivers={}, presets={}, tour=[]) for s in type_data(typ, host)]
    for override in data["sensors"]:
        target = next(s for s in sensors if s["name"] == override["name"])
        for name, val in override["drivers"].items():
            if name.startswith(PRESET):
                _, _, preset, field = name.split(":")
                target["presets"].setdefault(preset, {})[field] = val
            elif name == SENSOR + "tour":
                target["tour"] = val
            else:
                target["drivers"][name] = val
    return execute(host, dict(operation="create", path=edit.path, name=Sdf.Path(edit.path).name,
        ref=uuid_to_guid(identity), type=typ.GlobalId, container=container.GlobalId,
        matrix=gf_to_np(matrix).tolist(), sensors=sensors,
        phase={"proposed": "NEW", "existing": "EXISTING", "demolished": "DEMOLISH", "temporary": "TEMPORARY"}.get(data["attributes"].get("aeco:phase")),
        drivers={k: v for k, v in data["attributes"].items() if k.startswith("aeco:cctv:")}))


def apply_edit(host, e, edit):
    if edit.name == "inheritPaths":
        typ = ensure_type(host, edit.value[0])
        execute(host, dict(operation="typeSwap", ref=e.GlobalId, type=typ.GlobalId))
    elif edit.name == "xformOp:transform":
        prim = host.current.GetPrimAtPath(edit.path)
        matrix = Gf.Matrix4d(edit.value) * UsdGeom.XformCache().GetLocalToWorldTransform(prim.GetParent())
        execute(host, dict(operation="move", ref=e.GlobalId, matrix=gf_to_np(matrix).tolist()))
    elif edit.name.startswith((SENSOR, PRESET)) or edit.name == "apiSchemas":
        sensors = sensor_data(e, uel.get_type(e), host)
        sensor = next(s for s in sensors if s["name"] == edit.section["sensor"])
        if edit.name == "apiSchemas":
            desired = {a.split(":", 1)[1] for a in edit.value if a.startswith("AecoCctvPresetAPI:")}
            prim = host.session.stage.GetPrimAtPath(edit.path)
            sensor["presets"] = {name: sensor.get("presets", {}).get(name, {field: prim.GetAttribute(PRESET + name + ":" + field).Get()
                                  for field in ("pan", "tilt", "focalLength", "dwell", "home")}) for name in sorted(desired)}
            sensor["tour"] = [n for n in sensor.get("tour", []) if n in desired]
        elif edit.name == SENSOR + "tour":
            sensor["tour"] = edit.value
        elif edit.name.startswith(SENSOR):
            sensor["drivers"][edit.name] = edit.value
        else:
            _, _, preset, field = edit.name.split(":")
            sensor.setdefault("presets", {}).setdefault(preset, {})[field] = edit.value
        execute(host, dict(operation="sensors", ref=e.GlobalId, sensors=sensors))
    elif edit.name != "xformOpOrder":
        drivers = load(e, "Drivers", {})
        drivers[edit.name] = edit.value
        execute(host, dict(operation="drivers", ref=e.GlobalId, drivers=drivers))
