from usdaeco_ifc.runtime import python as run_python
import os
import subprocess
import sys
import pytest
from pxr import Gf, Plug, Usd
from scenarios.cctv import fixture, SENSOR_PATH
from aeco_sync.cctv import SENSOR


@pytest.mark.parametrize("operation", ["apply", "readback"])
def test_library_entrypoints_register_family_before_diagnostics(tmp_path, operation):
    session = fixture(tmp_path)
    session.capture_base([])
    with Usd.EditContext(session.stage, session.intent):
        session.stage.GetPrimAtPath(SENSOR_PATH).GetAttribute(SENSOR + "sensorSize").Set(Gf.Vec2d(10, 10))
    session.intent.Save()
    code = """import sys
from aeco_sync.stack import Session
from aeco_sync import engine
s=Session(sys.argv[1])
if sys.argv[2] == 'apply':
    result=engine.apply(s)
    assert result['pending'] == 1 and result['diagnostics'][0]['code'] == 'sync:unsupported', result
    assert 'sensorSize' in result['diagnostics'][0]['message']
else:
    engine.readback(s,'ifc',s.document('ifc'))
from pxr import Usd
assert Usd.SchemaRegistry().FindConcretePrimDefinition('AecoSyncDiagnostic')
assert Usd.SchemaRegistry().FindAppliedAPIPrimDefinition('AecoCctvSensorAPI')
"""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PXR_PLUGINPATH_NAME")}
    result = run_python([ "-c", code, str(session.path), operation], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("operation", ["apply", "readback"])
def test_late_sync_plugin_still_writes_typed_diagnostics_and_bindings(tmp_path, operation):
    from pathlib import Path
    session = fixture(tmp_path)
    session.capture_base([])
    with Usd.EditContext(session.stage, session.intent):
        session.stage.GetPrimAtPath(SENSOR_PATH).GetAttribute(SENSOR + "sensorSize").Set(Gf.Vec2d(10, 10))
    session.intent.Save()
    code = """import sys
from pxr import Plug, Usd
for path in sys.argv[3:]: Plug.Registry().RegisterPlugins(path)
Usd.Stage.CreateInMemory()  # snapshot core/CCTV schemas before sync is imported
from aeco_sync.stack import Session
from aeco_sync import engine
s=Session(sys.argv[1])
if sys.argv[2] == 'apply':
    result=engine.apply(s)
    assert result['diagnostics'][0]['code'] == 'sync:unsupported', result
    assert s.stage.GetPrimAtPath('/Sync/Diagnostics/ifc/d0001').GetAttribute('aeco:diag:severity').Get() == 'warning'
else:
    engine.readback(s,'ifc',s.document('ifc'))
    assert s.stage.GetPrimAtPath('/Model/Level/Camera').GetAttribute('aeco:host:ifc:ref').Get()
"""
    root = Path(__file__).parents[2]
    core = Path(os.environ.get("AECO_CORE_ROOT", root / "usdaeco-core"))
    axis = Path(os.environ.get("AECO_AXIS_ROOT", root / "usdaeco-axis"))
    plugins = [os.environ.get("CORE_PLUGIN_DIR", str(core / "out/plugins/usdAeco/resources")),
               os.environ.get("AXIS_PLUGIN_DIR", str(axis / "out/plugins/usdAecoAxis/resources")),
               Plug.Registry().GetPluginWithName("usdAecoCctv").resourcePath]
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PXR_PLUGINPATH_NAME")}
    result = run_python([ "-c", code, str(session.path), operation, *plugins], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
