"""Select the optional reference reader; keep native IFC writing in sync.

No USD import is required in Blender. An already registered, compatible CCTV
companion enables the shared reader. Older or absent companions use the
standalone reader. Errors inside an installed reference reader propagate.
"""
import copy
import importlib
import json
import sys

from ifcopenshell.api import run
from ifcopenshell.util import element as uel, unit
import math

from . import _camera_contract_fallback as fallback

CONTRACT, PSET, STANDARD, SENSOR = fallback.CONTRACT, fallback.PSET, fallback.STANDARD, fallback.SENSOR


def reader():
    """Resolve lazily: host modules may be imported before plugin registration."""
    plug = sys.modules.get("pxr.Plug")
    plugin = plug.Registry().GetPluginWithName("usdAecoCctv") if plug else None
    if plugin is None:
        return fallback
    from aeco_sync.requirements import shared_camera_reader_available
    if not shared_camera_reader_available(plugin.metadata.get("aeco", {}).get("version")):
        return fallback
    try:
        companion = importlib.import_module("usdaeco_cctv")
    except ModuleNotFoundError as exc:
        if exc.name == "usdaeco_cctv":
            return fallback
        raise
    if not shared_camera_reader_available(companion.__version__):
        return fallback
    return importlib.import_module("usdaeco_cctv.contract")


def stored(e):
    return reader().stored(e)


def load(e, key, default=None):
    return reader().load(e, key, default)


def standard_values(e):
    return reader().standard_values(e)


def mirror(e):
    return reader().mirror(e)


def prefer(authoritative, fallback, label):
    return reader().prefer(authoritative, fallback, label)


def table_presets(e):
    return reader().table_presets(e)


def read(e):
    return reader().read(e)


def pset(f, e, name):
    data = uel.get_psets(e, should_inherit=False).get(name)
    return f.by_id(data["id"]) if data else run("pset.add_pset", f, product=e, name=name)


def store(f, e, key, val):
    # Explicit IfcText also avoids template inference on this project Pset.
    text = json.dumps(val, sort_keys=True, allow_nan=False)
    run("pset.edit_pset", f, pset=pset(f, e, PSET), properties={
        "Contract": f.create_entity("IfcText", CONTRACT),
        key: f.create_entity("IfcText", text),
    })


def persist_sensors(f, e, sensors):
    sensors = [{k: copy.deepcopy(s[k]) for k in ("name", "drivers", "presets", "tour") if k in s} for s in sensors]
    for s in sensors:
        if SENSOR + "tour" in s.get("drivers", {}):
            s["tour"] = s["drivers"].pop(SENSOR + "tour")
        s.setdefault("presets", {})
        s.setdefault("tour", [])
    store(f, e, "Sensors", sensors)
    if load(e, "Drivers") is None:
        store(f, e, "Drivers", {})
    typ = uel.get_type(e)
    td = load(typ, "Type", {}) if typ else {}
    ts = load(typ, "Sensors", []) if typ else []
    props = {"CameraType": ["VIDEO"], "IsOutdoors": bool(td.get("aeco:cctvType:outdoor", False))}
    if sensors:
        d = {**(ts[0].get("drivers", {}) if ts else {}), **sensors[0].get("drivers", {})}
        if SENSOR + "pan" in d:
            props["PanHorizontal"] = float(d[SENSOR + "pan"])
        if SENSOR + "tilt" in d:
            props["TiltHorizontal"] = -math.radians(d[SENSOR + "tilt"]) / unit.calculate_unit_scale(f, "PLANEANGLEUNIT")
        if SENSOR + "focalLength" in d:
            props["Zoom"] = float(d[SENSOR + "focalLength"]) / (1000 * unit.calculate_unit_scale(f))
        if SENSOR + "pixels" in d:
            props["VideoResolutionWidth"], props["VideoResolutionHeight"] = map(int, d[SENSOR + "pixels"])
    standard = pset(f, e, STANDARD)
    run("pset.edit_pset", f, pset=standard, properties=props)
    table = next((p for p in standard.HasProperties if p.Name == "PanTiltZoomPreset"), None)
    if table is not None and not table.is_a("IfcPropertyTableValue"):
        standard.HasProperties = tuple(p for p in standard.HasProperties if p != table)
        f.remove(table)
        table = None
    if table is None:
        table = f.create_entity("IfcPropertyTableValue", Name="PanTiltZoomPreset")
        standard.HasProperties += (table,)
    rows = [(s["name"] + ":" + name, {**pose, "tilt": -pose.get("tilt", 0)})
            for s in sensors for name, pose in sorted(s["presets"].items())]
    table.DefiningValues = [f.create_entity("IfcIdentifier", key) for key, _ in rows]
    table.DefinedValues = [f.create_entity("IfcText", json.dumps(pose, sort_keys=True, allow_nan=False)) for _, pose in rows]
