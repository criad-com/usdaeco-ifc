import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
from usdaeco_ifc.runtime import python as run_python
"""Fresh-process fixture builds; compare emitted bytes without normalization."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parents[1]


def build(destination, seed, threads):
    sys.path.insert(0, str(HERE / "tools"))
    from fixtures import ordering_fixture
    import ifcopenshell
    from usdaeco_ifc.convert.geometry import extract_geometry

    model = ordering_fixture(seed)
    source = destination / "fixture.ifc"
    model.write(str(source))
    model = ifcopenshell.open(str(source))
    geometry = extract_geometry(model, threads=threads)
    # Also exercise the authoring API with opposite mesh insertion orders.
    geometry = dict(sorted(geometry.items(), reverse=threads == 2))
    from usdaeco_ifc.convert.author import author, guid_to_uuid
    stats = author(model, geometry, str(destination / "fixture.usda"))
    stats.pop("out")
    from pxr import Gf, Sdf, Usd, UsdGeom
    stage = Usd.Stage.Open(str(destination / "fixture.usda"))
    by_id = {p.GetAttribute("aeco:id").Get(): p for p in stage.Traverse()
             if p.GetAttribute("aeco:id")}

    def prim(entity):
        return by_id[guid_to_uuid(entity.GlobalId)]

    def paths(entities):
        return [prim(e).GetPath() for e in entities]

    relationships = 0
    preserved = True
    for group in model.by_type("IfcSystem"):
        expected = [obj for rel in group.IsGroupedBy for obj in rel.RelatedObjects]
        preserved &= prim(group).GetRelationship("collection:members:includes").GetTargets() == paths(expected)
        expected = [obj for rel in group.ServicesBuildings for obj in rel.RelatedBuildings]
        preserved &= prim(group).GetRelationship("aeco:serves").GetTargets() == paths(expected)
        relationships += 2
    for rel in model.by_type("IfcRelReferencedInSpatialStructure"):
        for element in rel.RelatedElements:
            preserved &= prim(element).GetRelationship("aeco:referencedContainers").GetTargets() == paths([rel.RelatingStructure])
            relationships += 1
    for rel in model.by_type("IfcRelConnectsPorts"):
        for a, b in ((rel.RelatingPort, rel.RelatedPort), (rel.RelatedPort, rel.RelatingPort)):
            preserved &= prim(a).GetRelationship("aeco:connectedPorts").GetTargets() == paths([b])
            relationships += 1
    ordered = next(p for p in by_id.values() if p.GetAttribute("aeco:props:Pset_Ordered:Tour"))
    preserved &= list(ordered.GetAttribute("aeco:props:Pset_Ordered:Tour").Get()) == ["Zulu", "Alpha", "Middle"]
    preserved &= list(ordered.GetAttribute("aeco:props:Pset_Ordered:Samples").Get()) == ["third", "first", "second"]
    for entity in model.by_type("IfcElement") + model.by_type("IfcSpace"):
        entry = geometry.get(entity.GlobalId)
        if not entry:
            continue
        owner = prim(entity)
        extent = entity.is_a("IfcSpace")
        mesh = stage.GetPrimAtPath(owner.GetPath().AppendChild("Extent" if extent else "Geom"))
        preserved &= list(mesh.GetAttribute("faceVertexIndices").Get()) == entry["faces"]
        preserved &= list(mesh.GetAttribute("points").Get()) == [Gf.Vec3f(*entry["verts"][i:i+3])
                                                                for i in range(0, len(entry["verts"]), 3)]
        xf = UsdGeom.Xformable(mesh if extent else owner)
        preserved &= list(xf.GetXformOpOrderAttr().Get()) == ["xformOp:transform"]
        preserved &= xf.GetLocalTransformation() == Gf.Matrix4d(*entry["matrix"])

    # Downstream explicit child/property reorders, op order and target order
    # must still compose over the converter's declaration order.
    overlay = Sdf.Layer.CreateAnonymous("ordered-overlay.usda")
    overlay.subLayerPaths = [stage.GetRootLayer().identifier]
    composed = Usd.Stage.Open(overlay)
    root = composed.GetPrimAtPath(stage.GetDefaultPrim().GetPath())
    child_order = ["Systems", "Site", "_TypeCatalog"]
    root.SetChildrenReorder(child_order)
    owner = composed.GetPrimAtPath(ordered.GetPath())
    xf = UsdGeom.Xformable(owner)
    xf.AddTranslateOp().Set(Gf.Vec3d(1, 2, 3))
    xf.AddRotateXYZOp().Set(Gf.Vec3f(10, 20, 30))
    xf.SetXformOpOrder([xf.GetTranslateOp(), xf.GetRotateXYZOp()], resetXformStack=True)
    owner.SetPropertyOrder(["xformOp:translate", "xformOp:rotateXYZ"])
    targets = [root.GetPath().AppendChild("Systems"), root.GetPath().AppendChild("Site")]
    owner.CreateRelationship("orderedTargets").SetTargets(targets)
    before = overlay.ExportToString()
    composed.Flatten()
    preserved &= overlay.ExportToString() == before
    preserved &= [p.GetName() for p in root.GetAllChildren()] == child_order
    preserved &= owner.GetPropertyOrder() == ["xformOp:translate", "xformOp:rotateXYZ"]
    preserved &= list(xf.GetXformOpOrderAttr().Get()) == ["!resetXformStack!", "xformOp:translate", "xformOp:rotateXYZ"]
    preserved &= owner.GetRelationship("orderedTargets").GetTargets() == targets

    return {"stats": stats, "preserved": bool(preserved), "relationships": relationships,
            "numbering": {e.GlobalId: e.id() for e in model.by_type("IfcRoot")},
            "creation_order": [e.GlobalId for e in model if e.is_a("IfcRoot")]}


def run(destination):
    # A caller may supply a directory containing old outputs; never open them.
    with tempfile.TemporaryDirectory(prefix="ordering-", dir=destination) as tmp:
        directories, reports = [], []
        for seed, threads in ((11, 1), (29, 2)):
            directory = Path(tmp) / str(seed)
            directory.mkdir()
            env = dict(os.environ, PYTHONHASHSEED=str(seed))
            env.pop("PYTHONPATH", None)
            result = run_python([ str(Path(__file__).resolve()), "--build",
                                     str(directory), str(seed), str(threads)], cwd=directory,
                                    env=env, check=True, capture_output=True, text=True)
            reports.append(json.loads(result.stdout))
            directories.append(directory)
        a, b = directories
        suffixes = (".usda", ".semantics.usda", ".geometry.usdc")
        matches = {s: (a / ("fixture" + s)).read_bytes() == (b / ("fixture" + s)).read_bytes()
                   for s in suffixes}
        from pxr import Sdf, Usd
        texts = {s: Sdf.Layer.FindOrOpen(str(a / ("fixture" + s))).ExportToString() ==
                 Sdf.Layer.FindOrOpen(str(b / ("fixture" + s))).ExportToString() for s in suffixes}
        # Omit USD's generated comment containing the absolute source filename.
        flat = Usd.Stage.Open(str(a / "fixture.usda")).Flatten(addSourceFileComment=False).ExportToString() == \
               Usd.Stage.Open(str(b / "fixture.usda")).Flatten(addSourceFileComment=False).ExportToString()
        one, two = reports
        ids = one["numbering"].keys() == two["numbering"].keys()
        renumbered = sum(one["numbering"][k] != two["numbering"].get(k) for k in one["numbering"])
        changed = one["creation_order"] != two["creation_order"] and renumbered > 0
        expected = {"elements": 5, "spatial": 35, "types": 3, "systems": 3,
                    "ports": 3, "portLinks": 1, "meshes": 7, "extents": 2, "unparented": 0}
        census = one["stats"] == two["stats"] and all(one["stats"][k] == v for k, v in expected.items())
        return {
            "independent_order": {
                "name": "Shuffled independent IFC builds emit identical bytes, layer texts and flattened text",
                "ok": ids and changed and census and all(matches.values()) and all(texts.values()) and flat,
                "detail": {"layers": matches, "layer_texts": texts, "flattened": flat,
                           "same_globalids": ids, "renumbered_roots": renumbered,
                           "creation_order_changed": changed, "stats": one["stats"],
                           "sha256": {s: hashlib.sha256((a / ("fixture" + s)).read_bytes()).hexdigest()
                                      for s in suffixes}}},
            "ordered_values": {
                "name": "Declaration sorting preserves relationships, mesh arrays, transforms and explicit reorders",
                "ok": all(r["preserved"] and r["relationships"] == 10 for r in reports),
                "detail": {"preserved": [r["preserved"] for r in reports],
                           "relationships_per_build": one["relationships"]}},
        }


if __name__ == "__main__":
    if sys.argv[1] == "--build":
        result = build(Path(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
    else:
        result = run(Path(sys.argv[1]))
    print(json.dumps(result, sort_keys=True))
