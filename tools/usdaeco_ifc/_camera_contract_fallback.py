"""Standalone IFC camera reader for older/absent CCTV companions and Blender.

Tier A uses degrees, mm and metres. Tier B mirrors the first head in IFC
project units, except PanHorizontal whose defective template stores degrees.
"""
import copy
import json
import math
import warnings

from ifcopenshell.util import element as uel, unit

CONTRACT = "usdaeco-cctv-ifc/1.0"
PSET = "Pset_AecoCctv"
STANDARD = "Pset_AudioVisualApplianceTypeCamera"
SENSOR = "aeco:cctvSensor:"




def stored(e):
    return uel.get_psets(e, should_inherit=False).get(PSET, {})




def load(e, key, default=None):
    fields = stored(e)
    contract = fields.get("Contract")
    if contract and not str(contract).startswith("usdaeco-cctv-ifc/1."):
        warnings.warn(f"{e.GlobalId}: unknown camera contract {contract}; using tiers B/C", stacklevel=2)
        return copy.deepcopy(default)
    if not contract:
        return copy.deepcopy(default)
    raw = fields.get(key)
    return json.loads(raw) if raw is not None else copy.deepcopy(default)


def standard_values(e):
    result = {}
    fields = uel.get_psets(e).get(STANDARD, {})
    if not fields:
        return result
    for prop in e.file.by_id(fields["id"]).HasProperties:
        if prop.is_a("IfcPropertySingleValue") and prop.NominalValue is not None:
            result[prop.Name] = prop.NominalValue.wrappedValue
        elif prop.is_a("IfcPropertyEnumeratedValue") and prop.EnumerationValues:
            vals = [v.wrappedValue for v in prop.EnumerationValues]
            result[prop.Name] = vals[0] if len(vals) == 1 else vals
        elif prop.is_a("IfcPropertyBoundedValue"):
            vals = [v.wrappedValue for v in (prop.LowerBoundValue, prop.UpperBoundValue) if v is not None]
            if prop.SetPointValue is not None:
                result[prop.Name] = prop.SetPointValue.wrappedValue
            elif vals:
                result[prop.Name] = sum(vals) / len(vals)
    return result


def mirror(e):
    p, d = standard_values(e), {}
    if "PanHorizontal" in p:
        d[SENSOR + "pan"] = float(p["PanHorizontal"])
    if "TiltHorizontal" in p:
        d[SENSOR + "tilt"] = -math.degrees(float(p["TiltHorizontal"]) * unit.calculate_unit_scale(e.file, "PLANEANGLEUNIT"))
    if "Zoom" in p:
        d[SENSOR + "focalLength"] = float(p["Zoom"]) * unit.calculate_unit_scale(e.file) * 1000
    if "VideoResolutionWidth" in p and "VideoResolutionHeight" in p:
        d[SENSOR + "pixels"] = [p["VideoResolutionWidth"], p["VideoResolutionHeight"]]
    return d


def prefer(authoritative, fallback, label):
    result = dict(fallback)
    for key, val in authoritative.items():
        if key in fallback:
            other = fallback[key]
            if isinstance(val, (int, float)) and isinstance(other, (int, float)):
                same = abs(val - other) <= 1e-6
            elif isinstance(val, list) and isinstance(other, list):
                same = len(val) == len(other) and all(abs(a-b) <= 1e-6 for a, b in zip(val, other))
            else:
                same = val == other
            if not same:
                warnings.warn(f"{label}: {key} tier A {val!r} conflicts with tier B {other!r}; keeping A", stacklevel=2)
        result[key] = val
    return result


def table_presets(e):
    fields = uel.get_psets(e, should_inherit=False).get(STANDARD, {})
    result = {}
    if fields:
        for p in e.file.by_id(fields["id"]).HasProperties:
            if p.Name == "PanTiltZoomPreset" and p.is_a("IfcPropertyTableValue"):
                for key, val in zip(p.DefiningValues or [], p.DefinedValues or []):
                    name, preset = key.wrappedValue.split(":", 1)
                    pose = json.loads(val.wrappedValue)
                    result.setdefault(name, {})[preset] = {**pose, "tilt": -pose.get("tilt", 0)}
    return result




HOUSING = {'aeco:cctv:mount', 'aeco:cctv:scenario', 'aeco:cctvType:outdoor',
           'aeco:cctvType:irRange', 'aeco:type:model', 'aeco:type:manufacturer'}
SENSOR_DRIVERS = {'projection', 'spectrum', 'focalRange', 'hfovRange', 'vfovRange', 'sensorSize',
                  'pixels', 'offset', 'panRange', 'tiltRange', 'motorised', 'pan', 'tilt', 'roll',
                  'focalLength', 'range', 'targetDensity', 'tour'}


def drivers(sensor):
    return {k[len(SENSOR):]: v for k, v in sensor.get('drivers', {}).items()
            if k.startswith(SENSOR) and k[len(SENSOR):] in SENSOR_DRIVERS}


def read(e):
    """Read an occurrence and its type into a JSON-compatible driver dictionary.

    Result keys: ``type`` (``drivers``, ``sensors``), effective housing ``drivers``
    and ``sensors``. Each head has ``name``, effective ``drivers``, ``presets``
    and ``tour``. Type values are also retained separately for sparse authors.
    Tier A wins over B; occurrence A overrides type A. No fallback optics are
    invented. Unknown driver keys are ignored; unknown major versions warn.
    This function reads the versioned tiers A/B, not vendor tier C dialects.
    """
    from ifcopenshell.util import element as uel
    typ = uel.get_type(e)
    type_a = load(typ, "Type", {}) if typ else {}
    type_heads = load(typ, "Sensors", []) if typ else []
    own_heads = load(e, "Sensors", [])
    type_b = mirror(typ) if typ else {}
    own_b = mirror(e)
    type_housing_b = standard_values(typ) if typ else {}
    type_drivers = prefer({k: v for k, v in type_a.items() if k in HOUSING},
        {"aeco:cctvType:outdoor": type_housing_b["IsOutdoors"]}
        if "IsOutdoors" in type_housing_b else {}, typ.GlobalId if typ else e.GlobalId)
    type_sensors = []
    if not type_heads and type_b:
        type_heads = [{"name": "Sensor_0", "drivers": {}}]
    for i, head in enumerate(type_heads):
        td = {SENSOR + k: v for k, v in drivers(head).items()}
        type_sensors.append(dict(name=head["name"], drivers=prefer(td,
            {k: v for k, v in type_b.items() if k == SENSOR + "pixels"} if i == 0 else {}, e.GlobalId)))
    entries = own_heads or [{"name": h["name"], "drivers": {}} for h in type_heads]
    tables = table_presets(e)
    if not entries and (own_b or tables):
        entries = [{"name": "Sensor_0", "drivers": {}}]
    sensors = []
    for i, entry in enumerate(entries):
        td = type_sensors[i]["drivers"] if i < len(type_sensors) else {}
        od = {SENSOR + k: v for k, v in drivers(entry).items()}
        resolved = prefer({**td, **od}, own_b if i == 0 else {}, e.GlobalId)
        type_head = type_heads[i] if i < len(type_heads) else {}
        presets = entry.get("presets", type_head.get("presets", tables.get(entry["name"], {})))
        tour = entry.get("tour", type_head.get("tour", resolved.pop(SENSOR + "tour", [])))
        sensors.append(dict(name=entry["name"], drivers=resolved, presets=presets, tour=tour))
    housing_b = standard_values(e)
    housing_a = {**{k: v for k, v in type_a.items() if k in HOUSING},
                 **{k: v for k, v in load(e, "Drivers", {}).items() if k in HOUSING}}
    housing_fallback = {**type_drivers, **({"aeco:cctvType:outdoor": housing_b["IsOutdoors"]}
                        if "IsOutdoors" in housing_b else {})}
    housing = prefer(housing_a, housing_fallback, e.GlobalId)
    return dict(type=dict(drivers=type_drivers, sensors=type_sensors), drivers=housing, sensors=sensors)
