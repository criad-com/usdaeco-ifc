"""Schema-free imaging-purpose and transform probe, always a fresh process."""
import json
import sys
from pxr import Usd, UsdGeom

stage = Usd.Stage.Open(sys.argv[1])
extents = [p for p in stage.Traverse() if p.GetName() == "Extent"]
default_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])
guide_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["guide"])
room = stage.GetPrimAtPath("/DemoProject/Site/Building/Ground/Room_01")
print(json.dumps({
    "no_core_plugin": Usd.SchemaRegistry().FindConcretePrimDefinition("AecoSpace") is None,
    "extents": len(extents),
    "visible_extents": sum(UsdGeom.Imageable(p).ComputePurpose() in ("default", "render") for p in extents),
    "ordinary_bound_empty": default_cache.ComputeWorldBound(room).GetRange().IsEmpty(),
    "guide_bound_present": not guide_cache.ComputeWorldBound(room).GetRange().IsEmpty(),
    "fallbacks": all(p.GetPrimTypeInfo().GetSchemaTypeName() == "Xform"
                     for p in stage.Traverse() if p.GetTypeName().startswith("Aeco")),
    "transforms": {str(p.GetPath()): [list(row) for row in UsdGeom.Xformable(p).ComputeLocalToWorldTransform(Usd.TimeCode.Default())]
                   for p in stage.Traverse() if p.IsA(UsdGeom.Xformable)},
}))
