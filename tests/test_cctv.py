from usdaeco_ifc.runtime import python as run_python
"""C4 native IFC transactions, optional delegation and Revit receipt contracts."""
import copy
import json
import math
from pathlib import Path
import subprocess
import sys
import os
import pytest
from pxr import Gf, Sdf, Usd, UsdGeom
from aeco_sync.cctv import SENSOR, PRESET, normalize_revit_camera
from aeco_sync.edits import Edit, collect, preflight, classify
from aeco_sync.diagnostics import Diagnostics
from aeco_sync.readback import bind, publish
from usdaeco_ifc.host import IfcHost
from aeco_sync import engine

CCTV = Usd.SchemaRegistry().FindAppliedAPIPrimDefinition("AecoCctvSensorAPI") is not None
requires_cctv = pytest.mark.skipif(not CCTV, reason="Set AECO_CCTV_ROOT before registration for optional camera integration")
CASES = [c["id"] for c in json.loads((Path(__file__).parents[1] / "scenarios/cases.json").read_text()) if c.get("family") == "camera"]


@requires_cctv
@pytest.mark.parametrize("case", CASES)
def test_ifc_camera_cases(tmp_path, case):
    from scenarios.cctv import run_case
    assert run_case(tmp_path, case)["passed"]








@requires_cctv
def test_sensor_stock_attributes_are_always_derived(tmp_path):
    from scenarios.cctv import fixture, SENSOR_PATH
    session = fixture(tmp_path)
    sensor = session.stage.GetPrimAtPath(SENSOR_PATH)
    for name in (*UsdGeom.Camera.GetSchemaAttributeNames(False), "xformOpOrder", "xformOp:rotateXYZ"):
        assert classify(sensor, name) == "derived", name
    assert classify(sensor, PRESET + "Preset_1:pan") == "section"













@requires_cctv
def test_camera_transaction_failure_restores_native_file_and_intent(tmp_path, monkeypatch):
    from scenarios.cctv import fixture, author
    from aeco_sync.stack import digest
    from usdaeco_ifc import _ifc_cctv
    session = fixture(tmp_path)
    file = session.document("ifc")
    before = digest(file)
    author(session, "C-pan-tilt")
    intent = session.intent.ExportToString()
    original = _ifc_cctv.apply_edit
    def fail_after_native_edit(host, entity, edit):
        original(host, entity, edit)
        raise ValueError("seeded native refusal after parameter set")
    monkeypatch.setattr(_ifc_cctv, "apply_edit", fail_after_native_edit)
    result = engine.apply(session)
    assert result["accepted"] == 0 and result["pending"] == 2
    assert digest(file) == before and session.intent.ExportToString() == intent
    assert not (session.path.parent / "host.ifc.ifc").exists()


@requires_cctv
def test_camera_delegation_preserves_other_derived_geometry_and_is_deterministic(tmp_path):
    from scenarios.cctv import fixture
    from aeco_sync.derive import derive_cameras
    session = fixture(tmp_path)
    layer = session.layer("derived.usda")
    with Usd.EditContext(session.stage, layer):
        UsdGeom.Cube.Define(session.stage, "/Other/Proxy").GetSizeAttr().Set(2.)
    current = session.current()
    derive_cameras(current, layer)
    before = layer.ExportToString()
    derive_cameras(current, layer)
    assert layer.ExportToString() == before
    assert session.stage.GetPrimAtPath("/Other/Proxy").GetAttribute("size").Get() == 2.


@requires_cctv
def test_camera_stage_composes_without_plugins(tmp_path):
    from scenarios.cctv import fixture, SENSOR_PATH
    session = fixture(tmp_path)
    for layer in session.stage.GetLayerStack():
        if layer.realPath:
            layer.Save()
    env = {k:v for k,v in os.environ.items() if k not in ("PYTHONPATH", "AECO_CCTV_ROOT", "PXR_PLUGINPATH_NAME", "PXR_AR_DEFAULT_SEARCH_PATH")}
    code = "from pxr import Usd,UsdGeom; import sys; s=Usd.Stage.Open(sys.argv[1]); assert Usd.SchemaRegistry.GetTypeFromSchemaTypeName('AecoCctvSensorAPI').isUnknown; assert s.GetPrimAtPath('/Model/Level').IsA(UsdGeom.Xform); p=s.GetPrimAtPath(sys.argv[2]); assert p.IsA(UsdGeom.Camera) and p.GetAttribute('focalLength').Get()==3.; assert p.GetChild('Sector').IsA(UsdGeom.Mesh); assert s.Flatten()"
    result = run_python(["-c",code,str(session.path),SENSOR_PATH],env=env,capture_output=True,text=True)
    assert result.returncode == 0, result.stderr


@requires_cctv
def test_independent_tier_a_preset_change_is_read_back_and_conflicts(tmp_path):
    from scenarios.cctv import fixture, author, SENSOR_PATH
    from usdaeco_ifc import _ifc_cctv
    import ifcopenshell
    session = fixture(tmp_path)
    author(session, "C-preset")
    f = ifcopenshell.open(session.document("ifc"))
    camera = f.by_type("IfcAudioVisualAppliance")[0]
    pset = _ifc_cctv.pset(f, camera, _ifc_cctv.STANDARD)
    table = next(p for p in pset.HasProperties if p.Name == "PanTiltZoomPreset")
    pose = json.loads(table.DefinedValues[0].wrappedValue)
    pose["pan"] = 11.
    table.DefinedValues = [f.create_entity("IfcText", json.dumps(pose))]
    sensors = _ifc_cctv.load(camera, "Sensors")
    sensors[0]["presets"]["Preset_1"]["pan"] = 11.
    _ifc_cctv.persist_sensors(f, camera, sensors)
    file = tmp_path / "independent.ifc"
    f.write(str(file))
    result = engine.readback(session, "ifc", file)
    assert any(d["code"] == "sync:conflict" for d in result["diagnostics"])
    current = session.current()
    assert current.GetPrimAtPath(SENSOR_PATH).GetAttribute(PRESET + "Preset_1:pan").Get() == 11.
    assert session.status()["pending"] == 3
