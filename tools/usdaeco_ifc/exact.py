"""Optional IFC BRep export; the ordinary converter is independent of this pass."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import tempfile
import uuid

from .exact_runtime import PACKAGE, RuntimeUnavailable, run_native, runtime_paths


def export_exact(source_ifc, stage_path, output, *, scope="/", classes=(), identities=None, deflection=0.0001):
    """Export meshable elements selected from an existing USD stage by identity.

    Returns every selected meshable product's outcome, including failures.
    The caller must check ``failed``; no failed class is silently mesh-only.
    """
    runtime_paths()
    if not math.isfinite(deflection) or deflection <= 0:
        raise ValueError("Deflection must be positive and finite")
    import ifcopenshell
    import ifcopenshell.geom
    from pxr import Usd, UsdGeom, Sdf
    stage = Usd.Stage.Open(str(stage_path))
    if not stage or stage.GetCompositionErrors():
        raise ValueError("Source stage does not compose")
    if UsdGeom.GetStageMetersPerUnit(stage) != 1:
        raise ValueError("Exact conversion currently requires a metre stage")
    selection = Sdf.Path(scope)
    if not selection.IsAbsolutePath() or not stage.GetPrimAtPath(selection):
        raise ValueError("Scope must name an existing absolute prim path")
    model = ifcopenshell.open(str(source_ifc))
    products = {str(uuid.UUID(hex=ifcopenshell.guid.expand(p.GlobalId))): p
                for p in model.by_type("IfcElement") if not p.is_a("IfcOpeningElement") and not p.is_a("IfcVirtualElement")}
    selected = []
    for prim in stage.Traverse():
        identity = prim.GetAttribute("aeco:id").Get()
        if identities is not None and identity not in identities:
            continue
        if identity not in products or not prim.GetPath().HasPrefix(selection):
            continue
        product = products[identity]
        if classes and product.is_a() not in classes:
            continue
        # Match the published body's evidence, including nested representations.
        meshes = [str(p.GetPath()) for p in Usd.PrimRange(prim) if p.IsA(UsdGeom.Mesh)
                  and p.GetAttribute("aeco:derived:role").Get() == "body"]
        if meshes:
            selected.append((prim, product, meshes))
    if not selected:
        raise ValueError("Selection contains no meshable IFC products")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    settings = ifcopenshell.geom.settings()
    # USE_BREP_DATA's supported 0.8.5 spelling; no pythonocc dependency.
    settings.set("iterator-output", ifcopenshell.ifcopenshell_wrapper.SERIALIZED)
    counts = Counter(p.is_a() for _, p, _ in selected)
    failures = []
    cache = UsdGeom.XformCache()
    with tempfile.TemporaryDirectory(prefix="aeco-exact-") as temp:
        directory = Path(temp)
        records = []
        for prim, product, meshes in selected:
            row = dict(path=str(prim.GetPath()), id=prim.GetAttribute("aeco:id").Get(),
                       ifcClass=product.is_a(), meshes=meshes)
            try:
                shape = ifcopenshell.geom.create_shape(settings, product)
                # Keep the shape alive while copying its owned Serialization.
                geometry = shape.geometry
                data = geometry.brep_data
                if not data.strip():
                    raise ValueError("SERIALIZED returned empty BRep data")
                filename = row["id"] + ".brep"
                (directory / filename).write_text(data)
                maps = sorted({i.MappingSource.id() for r in product.Representation.Representations
                               for i in r.Items if i.is_a("IfcMappedItem")})
                row.update(brep=filename, hash=hashlib.sha256(data.encode()).hexdigest(),
                           matrix=list(shape.transformation.matrix),
                           elementWorld=[float(x) for r in cache.GetLocalToWorldTransform(prim) for x in r],
                           styles=list(geometry.surface_styles), styleIds=list(geometry.surface_style_ids),
                           mapped=bool(maps), representationMaps=maps)
                records.append(row)
            except Exception as error:
                failures.append(dict(path=row["path"], ifcClass=row["ifcClass"], reason=str(error)))
        request = dict(records=records, output=str(output), deflection=deflection,
                       fallbackPrimTypes={k:list(v) for k,v in (stage.GetMetadata("fallbackPrimTypes") or {}).items()})
        request_path = directory / "request.json"
        request_path.write_text(json.dumps(request))
        run_native(PACKAGE / "exact_worker.py", request_path)
    report = json.loads((output / "exact-report.json").read_text())
    report["failures"] += failures
    successes = Counter(r["ifcClass"] for r in report["bodies"])
    report.update(selected=len(selected), exact=len(report["bodies"]), failed=len(report["failures"]),
                  perClass={name:dict(meshable=count, exact=successes[name], failed=count-successes[name])
                            for name,count in sorted(counts.items())})
    (output / "exact-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, prog="aeco-ifc-exact")
    parser.add_argument("ifc", type=Path)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scope", default="/")
    parser.add_argument("--class", dest="classes", action="append", default=[])
    parser.add_argument("--deflection", type=float, default=0.0001)
    parser.add_argument("--ids", type=Path, help="JSON array of aeco:id values to select")
    args = parser.parse_args(argv)
    print("== stage: IFC exact bodies", flush=True)
    try:
        result = export_exact(args.ifc, args.stage, args.out, scope=args.scope, classes=args.classes,
                              identities=set(json.loads(args.ids.read_text())) if args.ids else None, deflection=args.deflection)
    except RuntimeUnavailable as error:
        print("NOT RUN: " + str(error))
        return 2
    print(json.dumps({k:result[k] for k in ("selected", "exact", "failed", "perClass", "failures")}, indent=2))
    return int(bool(result["failed"]))


if __name__ == "__main__":
    raise SystemExit(main())
