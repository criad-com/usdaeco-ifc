import pytest
from scenarios.exchange import run_case


@pytest.mark.parametrize("length", ["METERS", "MILLIMETERS"])
@pytest.mark.parametrize("angle", ["degree", "radian"])
def test_fresh_camera_exchange(tmp_path, length, angle):
    assert run_case(tmp_path, length, angle)["passed"]


@pytest.mark.parametrize("field,standard,native_value,expected", [
    ("pan", "PanHorizontal", 35., 35.),
    ("tilt", "TiltHorizontal", -.5, 28.64788975654116),
    ("focalLength", "Zoom", .005, 5.),
])
@pytest.mark.parametrize("tier_a", [True, False])
def test_reader_prefers_a_then_unit_converted_b_then_c(tmp_path, field, standard, native_value, expected, tier_a):
    from scenarios.cctv import fixture
    from usdaeco_ifc import _ifc_cctv as native
    from aeco_sync.cctv import SENSOR
    from ifcopenshell.api import run
    import ifcopenshell
    f = ifcopenshell.open(fixture(tmp_path).document("ifc"))
    e = f.by_type("IfcAudioVisualAppliance")[0]
    sensors = native.load(e, "Sensors")
    sensors[0]["drivers"].pop(SENSOR + field, None)
    if tier_a:
        sensors[0]["drivers"][SENSOR + field] = 12.
    native.store(f, e, "Sensors", sensors)
    p = native.pset(f, e, "Family")
    c_name = {"pan": "FOV Pan", "tilt": "FOV Tilt", "focalLength": "FOV Actual Focal Length"}[field]
    run("pset.edit_pset", f, pset=p, properties={c_name: 90.})
    run("pset.edit_pset", f, pset=native.pset(f, e, native.STANDARD), properties={standard: native_value})
    if tier_a:
        with pytest.warns(UserWarning, match="tier A 12.0 conflicts with tier B"):
            actual = native.sensor_data(e, native.uel.get_type(e))[0]["drivers"][SENSOR + field]
        assert actual == 12.
    else:
        assert native.sensor_data(e, native.uel.get_type(e))[0]["drivers"][SENSOR + field] == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize("setpoint,expected", [(None, 20.), (25., 25.)])
def test_standard_bounded_values_use_setpoint_then_mean(tmp_path, setpoint, expected):
    from scenarios.cctv import fixture
    from usdaeco_ifc import _ifc_cctv as native
    import ifcopenshell
    f = ifcopenshell.open(fixture(tmp_path).document("ifc"))
    e = f.by_type("IfcAudioVisualAppliance")[0]
    p = native.pset(f, e, native.STANDARD)
    p.HasProperties = tuple(prop for prop in p.HasProperties if prop.Name != "PanHorizontal") + (
        f.create_entity("IfcPropertyBoundedValue", Name="PanHorizontal", LowerBoundValue=f.create_entity("IfcLengthMeasure", 10.),
                        UpperBoundValue=f.create_entity("IfcLengthMeasure", 30.),
                        SetPointValue=f.create_entity("IfcLengthMeasure", setpoint) if setpoint else None),)
    assert native.mirror(e)[native.SENSOR + "pan"] == expected


def test_unknown_major_contract_falls_back_with_warning(tmp_path):
    from scenarios.cctv import fixture
    from usdaeco_ifc import _ifc_cctv as native
    from ifcopenshell.api import run
    import ifcopenshell
    f = ifcopenshell.open(fixture(tmp_path).document("ifc"))
    e = f.by_type("IfcAudioVisualAppliance")[0]
    run("pset.edit_pset", f, pset=native.pset(f, e, native.PSET), properties={"Contract": "usdaeco-cctv-ifc/9.0"})
    with pytest.warns(UserWarning, match="unknown camera contract"):
        assert native.load(e, "Sensors", []) == []


def test_known_minor_and_unknown_driver_are_compatible(tmp_path):
    from scenarios.cctv import fixture
    from usdaeco_ifc import _ifc_cctv as native
    from ifcopenshell.api import run
    import ifcopenshell
    f = ifcopenshell.open(fixture(tmp_path).document("ifc"))
    e = f.by_type("IfcAudioVisualAppliance")[0]
    data = native.load(e, "Sensors")
    data[0]["drivers"][native.SENSOR + "futureDriver"] = 99.
    native.store(f, e, "Sensors", data)
    run("pset.edit_pset", f, pset=native.pset(f, e, native.PSET), properties={"Contract": "usdaeco-cctv-ifc/1.9"})
    assert native.SENSOR + "futureDriver" not in native.sensor_data(e, native.uel.get_type(e))[0]["drivers"]
