"""Source checkout setup; installed wheels use their normal package paths."""
import os
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent

def setup():
    roots = [ROOT, ROOT / "tools"]
    for variable, sibling in (("AECO_SYNC_ROOT", "usdaeco-sync"), ("AECO_CORE_ROOT", "usdaeco-core"), ("TOOLCHAIN_DIR", "usdaeco-toolchain")):
        root = Path(os.environ.get(variable, ROOT.parent / sibling))
        roots.extend([root, root / "tools"])
    for root in reversed(roots):
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
    import tomllib
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    # Materialize standard distribution metadata from the source manifest. This
    # lets importlib.metadata exercise the real entry point without installing
    # into a shared interpreter or requiring a build frontend.
    metadata = ROOT / "tools" / (project["name"].replace("-", "_") + "-" + project["version"] + ".dist-info")
    metadata.mkdir(exist_ok=True)
    (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: " + project["name"] + "\nVersion: " + project["version"] + "\n")
    entries = project["entry-points"]["aeco_sync.hosts"]
    (metadata / "entry_points.txt").write_text("[aeco_sync.hosts]\n" + "".join(f"{name} = {value}\n" for name, value in entries.items()))
    os.environ.setdefault("AECO_KIND_PLUGIN", str(ROOT / "tests/fixtures/usdAecoKindProto"))
    return roots

setup()

if os.environ.get('USD_SOLID_OCCT_RUNTIME') and not os.environ.get('USDRECORD'):
    from usdaeco_ifc.exact_runtime import native_renderer, RuntimeUnavailable
    try:
        os.environ['USDRECORD'] = str(native_renderer())
        os.environ['PATH'] = str(Path(os.environ['USDRECORD']).parent) + os.pathsep + os.environ.get('PATH', '')
    except RuntimeUnavailable:
        pass  # The gate reports exact rows as NOT RUN.
