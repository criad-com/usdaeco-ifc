"""Fresh writer → core converter → CCTV importer → derivation acceptance."""
import math
from pathlib import Path

import ifcopenshell
from ifcopenshell.api import run
from ifcopenshell.util import unit
from pxr import Sdf, Usd, UsdGeom
from aeco_sync.cctv import SENSOR, PRESET
from usdaeco_ifc import _ifc_cctv as native
from aeco_sync.stack import value


def run_case(directory, length="METERS", angle="radian"):
    from scenarios.cctv import fixture, OPTICS, POSE
    from usdaeco_ifc.convert import convert
    from usdaeco_cctv.importer import import_cctv
    from usdaeco_cctv.derive import derive
    directory = Path(directory)
    seed = fixture(directory / "seed")
    f = ifcopenshell.open(seed.document("ifc"))
    if length == "MILLIMETERS":
        f = unit.convert_file_length_units(f, "MILLIMETER")
    assignment = f.by_type("IfcUnitAssignment")[0]
    assignment.Units = tuple(u for u in assignment.Units if u.UnitType != "PLANEANGLEUNIT") + (
        run("unit.add_conversion_based_unit", f, name="degree") if angle == "degree"
        else run("unit.add_si_unit", f, unit_type="PLANEANGLEUNIT"),)
    camera = f.by_type("IfcAudioVisualAppliance")[0]
    typ = native.uel.get_type(camera)
    td = {"aeco:cctvType:outdoor": True, "aeco:cctvType:irRange": 40.,
          "aeco:type:model": "Demo dome", "aeco:type:manufacturer": "Example"}
    optics = {**OPTICS, SENSOR + "projection": "rectilinear", SENSOR + "spectrum": "visible", SENSOR + "sensorSize": [0., 0.]}
    native.store(f, typ, "Type", td)
    native.store(f, typ, "Sensors", [{"name": "Sensor_0", "drivers": optics}])
    poses = {"Home": dict(pan=-45., tilt=20., focalLength=7., dwell=4., home=True),
             "Door": dict(pan=90., tilt=45., focalLength=3., dwell=2., home=False)}
    drivers = {**POSE, SENSOR + "pan": 90., SENSOR + "tilt": 45., SENSOR + "focalLength": 5., SENSOR + "roll": 12.}
    housing = {"aeco:cctv:scenario": "door", "aeco:cctv:mount": "ceiling"}
    native.store(f, camera, "Drivers", housing)
    native.persist_sensors(f, camera, [{"name": "Sensor_0", "drivers": drivers, "presets": poses, "tour": ["Door", "Home"]}])
    source = directory / "camera.ifc"
    f.write(str(source))
    standard = native.standard_values(camera)
    assert standard["CameraType"] == "VIDEO" and standard["IsOutdoors"] is True
    assert abs(standard["PanHorizontal"] - 90.) < 1e-6
    assert abs(standard["TiltHorizontal"] * unit.calculate_unit_scale(f, "PLANEANGLEUNIT") + math.pi / 4) < 1e-6
    assert abs(standard["Zoom"] * unit.calculate_unit_scale(f) - .005) < 1e-9
    core, kind = directory / "core.usda", directory / "kind.usda"
    convert(str(source), str(core))
    imported = import_cctv(core, source, kind)
    stage = Usd.Stage.Open(str(kind))
    cp = next(p for p in stage.Traverse() if p.HasAPI("AecoCctvCameraAPI"))
    sensor = cp.GetChild("Sensor_0")
    catalog = stage.GetPrimAtPath(cp.GetInherits().GetAllDirectInherits()[0])
    errors = []
    def compare(actual, expected):
        actual = value(actual)
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            errors.append(abs(actual - expected))
            assert errors[-1] <= 1e-6, (actual, expected)
        elif isinstance(expected, list) and all(isinstance(x, (int, float)) for x in expected):
            assert len(actual) == len(expected)
            for a, b in zip(actual, expected):
                compare(a, b)
        else:
            assert actual == expected, (actual, expected)
    for prim, fields in ((catalog, td), (cp, housing), (sensor, {**optics, **drivers})):
        for name, expected in fields.items():
            compare(prim.GetAttribute(name).Get(), expected)
    assert list(sensor.GetAttribute(SENSOR + "tour").Get()) == ["Door", "Home"]
    for name, pose in poses.items():
        for field, expected in pose.items():
            compare(sensor.GetAttribute(PRESET + name + ":" + field).Get(), expected)
    output = Sdf.Layer.CreateAnonymous()
    derived = derive(stage, output)
    assert derived["sensors"] == 1 and not derived["skipped"], derived
    assert UsdGeom.XformCache().GetLocalToWorldTransform(cp).ExtractTranslation()[2] == 2.9
    native.persist_sensors(f, camera, [{"name": "Sensor_0", "drivers": drivers, "presets": {}, "tour": []}])
    removed = directory / "removed.ifc"
    f.write(str(removed))
    reopened = ifcopenshell.open(str(removed)).by_guid(camera.GlobalId)
    table = next(p for p in native.pset(reopened.file, reopened, native.STANDARD).HasProperties if p.Name == "PanTiltZoomPreset")
    assert not table.DefiningValues and not table.DefinedValues
    import_cctv(core, removed, directory / "removed.usda")
    final = Usd.Stage.Open(str(directory / "removed.usda"))
    assert not any(a.startswith("AecoCctvPresetAPI:") for a in final.GetPrimAtPath(sensor.GetPath()).GetAppliedSchemas())
    return dict(length=length, angle=angle, passed=True, maxDriverError=max(errors), sensors=imported["sensors"], presets=len(poses), lastPresetRemoved=True)
