#!/usr/bin/env python3
"""Convert, edit, export, reimport, compare, then render the edited corner."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
import bootstrap
from usdaeco_check.example import run_example
from usdaeco_ifc.acceptance import build_base, generator_root
from usdaeco_ifc.roundtrip import frame_camera, roundtrip


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish',action='store_true')
    parser.add_argument('--source-ifc',type=Path)
    parser.add_argument('--source-stage',type=Path)
    args = parser.parse_args(argv)
    if args.source_stage and not args.source_ifc:
        parser.error('--source-stage requires --source-ifc')
    example = Path(__file__).resolve().parent
    if os.environ.get("USDRECORD"):
        os.environ["PATH"] = str(Path(os.environ["USDRECORD"]).resolve().parent) + os.pathsep + os.environ.get("PATH", "")
    # Load the released kind libraries before any schema registry is created.
    # The protocol test fixture is deliberately not used for this example.
    from aeco_sync import register_plugins
    from pxr import Plug
    plugins = []
    for name, library in (("buildup", "usdAecoBuildUp"), ("wall", "usdAecoWall"), ("pipe", "usdAecoPipe")):
        sibling = Path(os.environ.get("AECO_" + name.upper() + "_ROOT", ROOT.parent / ("usdaeco-" + name)))
        candidates = [sibling / "out/plugins" / library / "resources",
                      sibling / library, sibling / "plugins" / library / "resources"]
        resource = next((path for path in candidates if (path / "plugInfo.json").is_file()), candidates[0])
        if not (resource / "plugInfo.json").is_file():
            raise FileNotFoundError("Build the released " + library + " plugin")
        plugins.append(resource.resolve())
    core = Path(os.environ.get("AECO_CORE", os.environ.get("AECO_CORE_ROOT", ROOT.parent / "usdaeco-core")))
    axis = Path(os.environ.get("AECO_AXIS_ROOT", ROOT.parent / "usdaeco-axis"))
    core_plugin = Path(os.environ.get("CORE_PLUGIN_DIR", core / "out/plugins/usdAeco/resources"))
    axis_plugin = Path(os.environ.get("AXIS_PLUGIN_DIR", axis / "out/plugins/usdAecoAxis/resources"))
    os.environ["PXR_PLUGINPATH_NAME"] = os.pathsep.join([str(core_plugin.resolve()), str(axis_plugin.resolve()), *map(str,plugins)])
    os.environ["AECO_KIND_PLUGIN"] = str(plugins[0])
    register_plugins()
    for plugin in plugins[1:]: Plug.Registry().RegisterPlugins(str(plugin))
    work = example / '.work'
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    with tempfile.TemporaryDirectory(prefix='aeco-ifc-example-') as temporary:
        temporary = Path(temporary)
        if args.source_ifc:
            source = args.source_ifc.resolve()
            model = args.source_stage.resolve() if args.source_stage else generator_root() / 'dist/base/dc.usda'
        else:
            print('== stage: generate base variant',flush=True)
            source, manifest = build_base(temporary/'base')
            model = generator_root() / 'dist/base/dc.usda'
        if args.source_stage:
            os.environ['AECO_DATACENTRE_STAGE'] = str(model)
        else:
            os.environ.pop('AECO_DATACENTRE_STAGE', None)
            os.environ['AECO_DATACENTRE_ROOT'] = str(generator_root().resolve())
        def hook(stage,out):
            local = work/'source';local.mkdir()
            shutil.copy2(source,local/'model.ifc')
            session, comparison, selected = roundtrip(local/'model.ifc',model,work/'roundtrip', review=out)
            from pxr import Sdf, Usd, UsdGeom, Gf
            display = Sdf.Layer.CreateNew(str(out/'display.usda'))
            # Compose the reimported geometry with its imported drivers and guides.
            # Earlier transaction layers remain reviewable without overriding it.
            stage.GetRootLayer().subLayerPaths = [
                str(example/'inputs/cameras.usda'), str(display.realPath),
                str(out/'reimport/derived.usda'), str(out/'reimport/kind.usda'),
                str(work/'roundtrip/reimport/model.usda')]
            (out/'final-stage.json').write_text(json.dumps(dict(
                state='reimported', elements=selected,
                geometry='roundtrip/reimport/model.geometry.usdc'),indent=2)+'\n')
            with Usd.EditContext(stage,display):
                selected_paths = {row['path'] for row in selected.values()}
                for prim in stage.Traverse():
                    if prim.HasAPI('AecoElementAPI') and str(prim.GetPath()) not in selected_paths:
                        UsdGeom.Imageable(prim).GetVisibilityAttr().Set('invisible')
                for name, row in selected.items():
                    prim = stage.GetPrimAtPath(row['path'])
                    for child in prim.GetChildren():
                        if child.GetName() in ('Axis','Proxy'):
                            UsdGeom.Imageable(child).GetVisibilityAttr().Set('invisible')
                    mesh = UsdGeom.Mesh(prim.GetChild('Geom'))
                    mesh.GetDisplayColorAttr().Set([Gf.Vec3f(.82,.58,.30) if name=='wall' else Gf.Vec3f(.10,.67,.73)])
                print('== stage: frame edited body bounds',flush=True)
                frame = frame_camera(stage, sorted(selected_paths))
                (out/'render-frame.json').write_text(json.dumps(frame,indent=2)+'\n')
            display.Save()
            return [comparison]
        return run_example(example,hook,publish=args.publish)

if __name__=='__main__': main()
