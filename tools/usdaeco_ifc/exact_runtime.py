"""Launch the optional exact route in its ABI-matched native Python process."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import shlex

PACKAGE = Path(__file__).resolve().parent


class RuntimeUnavailable(RuntimeError):
    pass


def runtime_paths():
    root = os.environ.get("USD_SOLID_OCCT_RUNTIME")
    if not root or not Path(root).exists():
        raise RuntimeUnavailable("Set USD_SOLID_OCCT_RUNTIME to the built usdSolidOcct runtime")
    if not (Path(root) / "paths.json").is_file():
        raise RuntimeError("The configured native runtime has no paths.json")
    paths = json.loads((Path(root) / "paths.json").read_text())
    if not Path(paths["python"]).is_file():
        raise RuntimeError("The configured native Python is unavailable")
    return paths


def clean_env(paths):
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PXR_PLUGINPATH_NAME", "PXR_AR_DEFAULT_SEARCH_PATH"):
        env.pop(key, None)
    core = Path(os.environ.get("AECO_CORE_ROOT", PACKAGE.parents[2] / "usdaeco-core"))
    env["PXR_PLUGINPATH_NAME"] = os.pathsep.join([os.environ.get("CORE_PLUGIN_DIR", str(core / "out/plugins/usdAeco/resources")), paths["plugins"]])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def native_renderer():
    """Expose the runtime's stock usdrecord without its optional schema plugins.

    This is a source-checkout launcher, not an installation. The harness's
    plugin-free child controls PXR_PLUGINPATH_NAME in the normal way.
    """
    paths = runtime_paths()
    closure = [Path(p) for p in Path(paths['closure']).read_text().splitlines()]
    usd = next(p for p in closure if (p / 'include/pxr/pxr.h').is_file())
    script = usd / 'bin/usdrecord'
    python = script.read_text().splitlines()[0].removeprefix('#!')
    candidates = list((usd / 'lib').glob('python*/site-packages/pxr/__init__.py'))
    if not candidates:
        candidates = list((usd / 'lib/python/pxr').glob('__init__.py'))
    if len(candidates) != 1 or not Path(python).is_file():
        raise RuntimeError('Native stock renderer Python layout is unavailable')
    package = candidates[0].parents[1]
    key = hashlib.sha256(str(usd).encode()).hexdigest()[:16]
    directory = Path(os.environ.get('AECO_EXACT_CACHE', PACKAGE.parents[1] / '.work/exact-native')) / ('render-' + key)
    directory.mkdir(parents=True, exist_ok=True)
    launcher = directory / 'usdrecord'
    code = 'import sys,runpy;sys.path.insert(0,' + repr(str(package)) + ');sys.argv[0]=' + repr(str(script)) + ';runpy.run_path(sys.argv[0],run_name="__main__")'
    launcher.write_text('#!/bin/sh\nexec env -u PYTHONPATH ' + shlex.quote(python) + ' -c ' + shlex.quote(code) + ' "$@"\n')
    launcher.chmod(0o755)
    return launcher


def build_adapter(paths=None):
    """Compile only our thin adapter, reusing the installed native libraries."""
    paths = paths or runtime_paths()
    source = PACKAGE / "native/exact_adapter.cpp"
    key = hashlib.sha256(source.read_bytes() + json.dumps(paths, sort_keys=True).encode()).hexdigest()[:16]
    cache = Path(os.environ.get("AECO_EXACT_CACHE", PACKAGE.parents[1] / ".work/exact-native")) / key
    output = cache / "_exact_native.so"
    if output.is_file():
        return cache
    cache.mkdir(parents=True, exist_ok=True)
    closure = [Path(p) for p in Path(paths["closure"]).read_text().splitlines()]
    usd = next(p for p in closure if (p / "include/pxr/pxr.h").is_file())
    settings = json.loads(subprocess.check_output([paths["python"], "-c",
        "import json,sysconfig;print(json.dumps(sysconfig.get_paths()))"], env=clean_env(paths), text=True))
    command = [os.environ.get("CXX", "c++"), "-std=c++17", "-O2", "-shared", "-fPIC"]
    if sys.platform == "darwin":
        command += ["-undefined", "dynamic_lookup"]
    for directory in (usd / "include", Path(paths["bridge"]) / "include", Path(paths["schema"]) / "include",
                      Path(paths["occt"]) / "include/opencascade", Path(settings["include"])):
        command += ["-I", str(directory)]
    for directory in (usd / "lib", Path(paths["bridge"]) / "lib", Path(paths["schema"]) / "lib", Path(paths["occt"]) / "lib"):
        command += ["-L", str(directory), "-Wl,-rpath," + str(directory)]
    command += [str(source), "-o", str(output), "-lusdSolidOcct", "-lusd_boost", "-lTKBRep", "-lTKTopAlgo",
                "-lTKGeomBase", "-lTKShHealing", "-lTKG3d", "-lTKG2d", "-lTKMath", "-lTKernel"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    if result.returncode:
        output.unlink(missing_ok=True)
        raise RuntimeError("Exact adapter compilation failed:\n" + result.stderr[-5000:])
    return cache


def run_native(script, *args, timeout=300):
    paths = runtime_paths()
    adapter = build_adapter(paths)
    code = "import sys;sys.path[:0]=sys.argv[1:3];script=sys.argv[3];sys.argv=sys.argv[3:];import runpy;runpy.run_path(script,run_name='__main__')"
    result = subprocess.run([paths["python"], "-c", code, str(adapter), str(PACKAGE.parent), str(script), *map(str, args)],
                            env=clean_env(paths), capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError("Exact native worker failed:\n" + (result.stdout + result.stderr)[-6000:])
    return result.stdout
