from usdaeco_ifc.runtime import python as run_python
"""The registered reference reader and standalone path agree on native IFC."""
import importlib
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import ifcopenshell
from ifcopenshell.api import run
from ifcopenshell.util import element, unit
import pytest

from usdaeco_ifc import _camera_contract as contract, _ifc_cctv as native
from aeco_sync.requirements import shared_camera_reader_available


def compare_paths(model, monkeypatch):
    shared = pytest.importorskip("usdaeco_cctv.contract")
    cameras = [e for e in model.by_type("IfcAudioVisualAppliance") if native.is_camera(e)]
    assert cameras
    def snapshot(implementation):
        monkeypatch.setattr(contract, "reader", lambda: implementation)
        return {e.GlobalId: dict(contract=contract.read(e),
            sensors=native.sensor_data(e, element.get_type(e)),
            typeSensors=native.type_data(element.get_type(e)),
            typeDrivers=native.type_drivers(element.get_type(e))) for e in cameras}
    assert snapshot(shared) == snapshot(contract.fallback)
    return len(cameras)


@pytest.mark.parametrize("millimetres", [False, True])
@pytest.mark.parametrize("angle", ["degree", "radian"])
@pytest.mark.parametrize("tier_a", [False, True])
def test_shared_and_fallback_driver_dictionaries(tmp_path, monkeypatch, millimetres, angle, tier_a):
    from scenarios.cctv import fixture
    model = ifcopenshell.open(fixture(tmp_path).document("ifc"))
    if millimetres:
        model = unit.convert_file_length_units(model, "MILLIMETER")
    assignment = model.by_type("IfcUnitAssignment")[0]
    assignment.Units = tuple(u for u in assignment.Units if u.UnitType != "PLANEANGLEUNIT") + (
        run("unit.add_conversion_based_unit", model, name="degree") if angle == "degree"
        else run("unit.add_si_unit", model, unit_type="PLANEANGLEUNIT"),)
    camera = model.by_type("IfcAudioVisualAppliance")[0]
    sensors = native.load(camera, "Sensors")
    sensors[0]["drivers"][contract.SENSOR + "tilt"] = 30.
    native.persist_sensors(model, camera, sensors)
    if not tier_a:
        for e in (camera, element.get_type(camera)):
            run("pset.remove_pset", model, product=e, pset=native.pset(model, e, contract.PSET))
    assert compare_paths(model, monkeypatch) == 1


def test_generator_ifc_parity(monkeypatch):
    source = os.environ.get("AECO_CONTRACT_IFC")
    if not source:
        pytest.skip("set AECO_CONTRACT_IFC to the generated demo IFC for integration parity")
    assert Path(source).is_file(), "AECO_CONTRACT_IFC must be an existing generated IFC"
    assert compare_paths(ifcopenshell.open(source), monkeypatch) == 45


@pytest.mark.parametrize("version,shared", [(None, False), ("0.4.4", False), ("0.4.7", False),
                                           ("0.4.8", True), ("0.5.0", True), ("0.6.0", False)])
def test_shared_reader_version_range(version, shared):
    assert shared_camera_reader_available(version) is shared


def test_registered_companion_selects_reference():
    shared = pytest.importorskip("usdaeco_cctv.contract")
    assert contract.reader() is shared


@pytest.mark.parametrize("version", [None, "0.4.7"])
def test_absent_or_older_registered_companion_selects_fallback(monkeypatch, version):
    plugin = SimpleNamespace(metadata={"aeco": {"version": version}}) if version else None
    plug = SimpleNamespace(Registry=lambda: SimpleNamespace(GetPluginWithName=lambda name: plugin))
    monkeypatch.setitem(sys.modules, "pxr.Plug", plug)
    assert contract.reader() is contract.fallback


def test_installed_reader_error_is_not_silently_hidden(monkeypatch):
    original = importlib.import_module
    def broken(name):
        if name == "usdaeco_cctv.contract":
            raise ModuleNotFoundError("broken reader dependency", name="reader_dependency")
        return original(name)
    monkeypatch.setattr(importlib, "import_module", broken)
    with pytest.raises(ModuleNotFoundError, match="broken reader dependency"):
        contract.reader()


def test_standalone_writer_and_reader_without_usd(tmp_path):
    script = """
import importlib.abc, sys
class NoUsd(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'pxr' or fullname.startswith('pxr.') or fullname == 'usdaeco_cctv':
            raise AssertionError('standalone contract must not import companions')
sys.meta_path.insert(0, NoUsd())
from usdaeco_ifc import _camera_contract as c
import ifcopenshell
from ifcopenshell.api import run
f = ifcopenshell.file(schema='IFC4X3')
run('root.create_entity', f, ifc_class='IfcProject')
run('unit.assign_unit', f, length={'is_metric': True, 'raw': 'METERS'})
e = run('root.create_entity', f, ifc_class='IfcAudioVisualAppliance', predefined_type='CAMERA')
c.persist_sensors(f, e, [dict(name='Sensor_0', drivers={c.SENSOR+'pan': 12.}, presets={}, tour=[])])
assert c.reader() is c.fallback
assert c.read(e)['sensors'][0]['drivers'][c.SENSOR+'pan'] == 12.
assert not any(n == 'pxr' or n.startswith('pxr.') for n in sys.modules)
"""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PXR_PLUGINPATH_NAME", "AECO_CCTV_ROOT")}
    result = run_python([ "-c", script],
        cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
