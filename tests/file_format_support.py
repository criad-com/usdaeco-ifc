"""Run file-format probes in the Python ABI of the CMake OpenUSD dependency."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def native_runtime():
    usd = Path(os.environ["USD_DEV"])
    config = (usd / "pxrConfig.cmake").read_text()
    executable = re.search(r"set\(Python3_EXECUTABLE \[\[(.*?)\]\]\)", config).group(1)
    packages = list(usd.glob("lib/python*/site-packages/pxr/__init__.py"))
    if not packages:
        packages = list(usd.glob("lib/python/pxr/__init__.py"))
    assert len(packages) == 1, "USD_DEV must contain its matching Python bindings"
    assert Path(executable).is_file(), "USD_DEV's Python is unavailable"
    return executable, str(packages[0].parents[1])


def environment(cache, *, plugins=True):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PXR_PLUGINPATH_NAME", "PXR_AR_DEFAULT_SEARCH_PATH")}
    env["USDAECO_IFC_PYTHON"] = str(ROOT / "tools/ifc-python")
    env["USDAECO_IFC_SOURCE_PYTHON"] = sys.executable
    env["USDAECO_IFC_CACHE"] = str(cache)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Only the format plugin in the consumer; schemas are tested via fallbacks.
    if plugins:
        env["PXR_PLUGINPATH_NAME"] = os.environ.get("USD_IFC_PLUGIN_DIR", str(ROOT / "out/plugins/usdIfc/resources"))
    return env


def native(code, *args, env, check=True):
    executable, packages = native_runtime()
    prefix = "import sys;sys.path.insert(0,sys.argv.pop(1));"
    result = subprocess.run([executable, "-c", prefix + code, packages, *map(str, args)],
                            env=env, text=True, capture_output=True, timeout=180)
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


SNAPSHOT = '''
import hashlib, json
from pxr import Usd, UsdGeom, Sdf
def snapshot(path):
    stage = Usd.Stage.Open(path)
    assert stage and not stage.GetCompositionErrors()
    prims = list(stage.TraverseAll())
    def api(prim, name): return name in prim.GetPrimTypeInfo().GetAppliedAPISchemas()
    counts = dict(prims=len(prims),
        spatial=sum(p.GetTypeName() in ('AecoSite','AecoFacility','AecoFacilityPart','AecoLevel','AecoSpace') for p in prims),
        elements=sum(api(p, 'AecoElementAPI') for p in prims),
        types=sum(p.IsAbstract() and api(p, 'AecoTypeAPI') for p in prims),
        systems=sum(p.GetTypeName() == 'AecoSystem' for p in prims),
        ports=sum(p.GetTypeName() == 'AecoPort' for p in prims),
        meshes=sum(p.IsA(UsdGeom.Mesh) for p in prims),
        gprims=sum(p.IsA(UsdGeom.Gprim) for p in prims))
    cache = UsdGeom.XformCache()
    transforms = {str(p.GetPath()): [v for row in cache.GetLocalToWorldTransform(p) for v in row]
                  for p in prims if UsdGeom.Xformable(p)}
    flat = stage.Flatten(False)
    return dict(census=counts, transforms=transforms,
                hash=hashlib.sha256(flat.ExportToString().encode()).hexdigest(),
                sublayers=list(stage.GetRootLayer().subLayerPaths),
                spatialPaths=[str(p.GetPath()) for p in prims if p.GetTypeName() in
                    ('AecoSite','AecoFacility','AecoFacilityPart','AecoLevel','AecoSpace')])
'''


def snapshot(path, env):
    return json.loads(native(SNAPSHOT + "\nprint(json.dumps(snapshot(sys.argv[1])))", path, env=env).stdout)
