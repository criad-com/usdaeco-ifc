import json
import pytest
import ifcopenshell
from pxr import Gf, Usd, UsdGeom
from aeco_sync import engine
from aeco_sync.cctv import SENSOR, PRESET, set_values
from usdaeco_ifc import _ifc_cctv as native
from scenarios.cctv import fixture, author, OPTICS, POSE, CAMERA, SENSOR_PATH


def unbound_catalog(session):
    with Usd.EditContext(session.stage, session.layer("kind.usda")):
        typ = session.stage.CreateClassPrim("/_Types/Published")
        typ.ApplyAPI("AecoTypeAPI")
        typ.ApplyAPI("AecoCctvCameraTypeAPI")
        typ.GetAttribute("aeco:type:model").Set("Demo camera")
        sensor = UsdGeom.Camera.Define(session.stage, "/_Types/Published/Sensor_0").GetPrim()
        sensor.ApplyAPI("AecoCctvSensorAPI")
        set_values(sensor, OPTICS)
    session.layer("kind.usda").Save()
    return typ.GetPath()


@pytest.mark.parametrize("operation", ["create", "swap"])
def test_publish_catalog_on_demand_and_repeat(tmp_path, operation):
    session = fixture(tmp_path)
    path = unbound_catalog(session)
    author(session, "C-create" if operation == "create" else "C-type-swap")
    camera_path = "/Model/Level/NewCamera" if operation == "create" else CAMERA
    with Usd.EditContext(session.stage, session.intent):
        session.stage.GetPrimAtPath(camera_path).GetInherits().SetInherits([path])
    result = engine.apply(session)
    assert result["inSync"] and result["publishedTypes"] == [str(path)], result
    current = session.current()
    binding = current.GetPrimAtPath(path).GetAttribute("aeco:host:ifc:ref").Get()
    f = ifcopenshell.open(session.document("ifc"))
    typ = f.by_guid(binding)
    assert typ.is_a("IfcAudioVisualApplianceType") and typ.PredefinedType == "CAMERA"
    assert typ.RepresentationMaps and native.stored(typ)["Contract"] == native.CONTRACT
    assert native.load(typ, "Type")["aeco:type:model"] == "Demo camera"
    assert native.load(typ, "Sensors")[0]["drivers"][SENSOR + "pixels"] == [2592, 1944]
    assert len(f.by_type("IfcAudioVisualApplianceType")) == 3
    repeat = engine.apply(session)
    assert repeat["mutations"] == 0 and repeat["publishedTypes"] == [] and repeat["inSync"], repeat


@pytest.mark.parametrize("reason", ["type", "attribute", "transform", "container"])
def test_creation_refusal_names_cause(tmp_path, reason):
    session = fixture(tmp_path)
    author(session, "C-create")
    with Usd.EditContext(session.stage, session.intent):
        prim = session.stage.GetPrimAtPath("/Model/Level/NewCamera")
        if reason == "type":
            # Keep optics composed but remove its native binding and class API.
            pass
        elif reason == "attribute":
            prim.CreateAttribute("unhandled", __import__("pxr").Sdf.ValueTypeNames.String).Set("value")
        elif reason == "transform":
            prim.GetAttribute("xformOp:transform").Set(Gf.Matrix4d().SetScale(2.))
    if reason in ("type", "container"):
        with Usd.EditContext(session.stage, session.layer("result.ifc.usda")):
            path = "/_Types/Dome" if reason == "type" else "/Model/Level"
            prim = session.stage.GetPrimAtPath(path)
            prim.GetAttribute("aeco:host:ifc:ref").Block()
            if reason == "type":
                prim.RemoveAPI("AecoCctvCameraTypeAPI")
            else:
                prim.GetAttribute("aeco:id").Block()
    result = engine.apply(session)
    assert result["accepted"] == 0 and result["pending"], result
    message = " ".join(d["message"] for d in result["diagnostics"])
    expected = {"type": "Unbound type /_Types/Dome", "attribute": "unhandled", "transform": "Non-rigid", "container": "/Model/Level"}[reason]
    assert expected in message, message


def test_preset_removal_and_tour_round_trip(tmp_path):
    session = fixture(tmp_path)
    session.capture_base()
    with Usd.EditContext(session.stage, session.intent):
        sensor = session.stage.GetPrimAtPath(SENSOR_PATH)
        sensor.GetAttribute(SENSOR + "tour").Set(["Preset_1"])
    assert engine.apply(session)["inSync"]
    session.capture_base()
    with Usd.EditContext(session.stage, session.intent):
        sensor.RemoveAPI("AecoCctvPresetAPI", "Preset_1")
    result = engine.apply(session)
    assert result["inSync"], result
    stage = session.current()
    current = stage.GetPrimAtPath(SENSOR_PATH)
    assert "AecoCctvPresetAPI:Preset_1" not in current.GetAppliedSchemas()
    assert not current.GetAttribute(SENSOR + "tour").Get()
    f = ifcopenshell.open(session.document("ifc"))
    assert native.load(f.by_type("IfcAudioVisualAppliance")[0], "Sensors")[0]["presets"] == {}


@pytest.mark.parametrize("new_camera", [False, True])
def test_preset_membership_alone_persists_default_pose(tmp_path, new_camera):
    session = fixture(tmp_path)
    if new_camera:
        author(session, "C-create")
    else:
        session.capture_base([])
    path = "/Model/Level/NewCamera" if new_camera else CAMERA
    with Usd.EditContext(session.stage, session.intent):
        session.stage.GetPrimAtPath(path + "/Sensor_0").ApplyAPI("AecoCctvPresetAPI", "Home")
    result = engine.apply(session)
    assert result["inSync"], result
    f = ifcopenshell.open(session.document("ifc"))
    current = session.current()
    ref = current.GetPrimAtPath(path).GetAttribute("aeco:host:ifc:ref").Get()
    home = native.load(f.by_guid(ref), "Sensors")[0]["presets"]["Home"]
    assert set(home) == {"pan", "tilt", "focalLength", "dwell", "home"}
    assert "AecoCctvPresetAPI:Home" in current.GetPrimAtPath(path + "/Sensor_0").GetAppliedSchemas()
