"""Native worker. Never import this module in the family USD-wheel process."""
import json
import hashlib
from pathlib import Path
import sys
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, UsdSolid, UsdSolidOcct as Bridge, Vt
import _exact_native as Native
from usdaeco_ifc.exact_common import mark, fingerprint, mesh_measures, comparison


def matrix(flat):
    return Gf.Matrix4d(*flat)


def author_twin(stage, exact, shape, deflection, transform=None):
    prim = exact.GetPrim() if hasattr(exact, "GetPrim") else exact
    mesh = UsdGeom.Mesh.Define(stage, prim.GetPath().GetParentPath().AppendChild("Body"))
    tess = Bridge.Tessellate(shape, deflection, 0.1)
    mesh.CreatePointsAttr(Vt.Vec3fArray([Gf.Vec3f(p) for p in tess.points]))
    mesh.CreateFaceVertexCountsAttr(tess.faceVertexCounts)
    mesh.CreateFaceVertexIndicesAttr(tess.faceVertexIndices)
    mesh.CreateSubdivisionSchemeAttr("none")
    mesh.CreateDoubleSidedAttr(True)
    mesh.CreatePurposeAttr("proxy")
    mesh.CreateExtentAttr(UsdGeom.PointBased.ComputeExtent(mesh.GetPointsAttr().Get()))
    if transform is not None:
        UsdGeom.Xformable(mesh).MakeMatrixXform().Set(transform)
    mark(mesh.GetPrim(), prim.GetAttribute("aeco:derived:source").Get(), "body", "tessellated", deflection,
         "aeco-ifc-exact 0.2.0 source=" + fingerprint(prim), prim.GetPath())
    return mesh, tess


def author_edges(stage, prim, shape, deflection, transform=None):
    lines = Native.Edges(shape, deflection)
    curves = UsdGeom.BasisCurves.Define(stage, prim.GetPath().GetParentPath().AppendChild("Edges"))
    curves.CreateTypeAttr("linear")
    curves.CreateWrapAttr("nonperiodic")
    curves.CreateCurveVertexCountsAttr([len(line) for line in lines])
    curves.CreatePointsAttr([Gf.Vec3f(*p) for line in lines for p in line])
    curves.CreateWidthsAttr([0.004])
    curves.SetWidthsInterpolation("constant")
    curves.CreatePurposeAttr("guide")
    curves.CreateDisplayColorAttr([(0.04, 0.08, 0.12)])
    if transform is not None:
        UsdGeom.Xformable(curves).MakeMatrixXform().Set(transform)
    mark(curves.GetPrim(), prim.GetAttribute("aeco:derived:source").Get(), "wireframe", "arcSegmented", deflection,
         "aeco-ifc-exact 0.2.0 source=" + fingerprint(prim), prim.GetPath())
    return curves


def materials(exact, twin, tess, info, row):
    """Map serialized representation-item styles to canonical faces and triangles."""
    styles = [row["styles"][i:i+4] for i in range(0, len(row["styles"]), 4)]
    if not styles:
        return 0
    groups = info["itemFaces"]
    if len(styles) == 1:
        groups = [list(range(len(info["faces"])))]
    elif len(styles) != len(groups):
        raise ValueError("Serialized style/item correspondence is not supported")
    if len(info["faces"]) != len(exact.GetPrim().GetAttribute("face:surfaceType").Get()):
        raise ValueError("Writer changed canonical face order/count; material correspondence unproven")
    if sorted(i for group in groups for i in group) != list(range(len(info["faces"]))):
        raise ValueError("Material items do not partition exact faces")
    for index,(style,faces) in enumerate(zip(styles,groups)):
        triangles = [i for i,face in enumerate(tess.sourceFaceIndices) if face in faces]
        for geom,indices in ((exact,faces),(twin,triangles)):
            stage = geom.GetPrim().GetStage()
            key = hashlib.sha256(json.dumps(style).encode()).hexdigest()[:16]
            material = UsdShade.Material.Define(stage, "/ExactMaterials/m_" + key)
            shader = UsdShade.Shader.Define(stage, material.GetPath().AppendChild("Preview"))
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*style[:3]))
            shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(style[3])
            material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
            subset = UsdGeom.Subset.Define(stage, geom.GetPath().AppendChild("Material_" + str(index)))
            subset.CreateElementTypeAttr("face")
            subset.CreateFamilyNameAttr("materialBind")
            subset.CreateIndicesAttr(indices)
            UsdShade.MaterialBindingAPI.Apply(subset.GetPrim()).Bind(material)
            UsdGeom.Subset.SetFamilyType(UsdGeom.Imageable(geom.GetPrim()), "materialBind", "partition")
    return len(styles)


def make_prototype(stage, prim, key):
    """Share representation data; identity, placement and correlation stay local."""
    original = prim.GetPath()
    container = Sdf.Path("/__ExactPrototypes/p_" + key)
    target = container.AppendChild(prim.GetName())
    root = stage.GetRootLayer()
    if not stage.GetPrimAtPath(target):
        stage.CreateClassPrim(container)
        Sdf.CopySpec(root, original, root, target)
        prototype = stage.GetPrimAtPath(target)
        for prop in list(prototype.GetProperties()):
            if prop.GetName().startswith(("aeco:", "xformOp")) or prop.GetName() == "proxyPrim":
                prototype.RemoveProperty(prop.GetName())
        prototype.RemoveAPI("AecoDerivedGeometryAPI")
    # Preserve occurrence-local metadata and transforms around an internal reference.
    keep = [p.GetName() for p in prim.GetProperties() if p.GetName().startswith(("aeco:", "xformOp")) or p.GetName() == "proxyPrim"]
    for child in list(prim.GetChildren()):
        stage.RemovePrim(child.GetPath())
    for prop in list(prim.GetProperties()):
        if prop.GetName() not in keep:
            prim.RemoveProperty(prop.GetName())
    prim.GetReferences().AddInternalReference(target)
    prim.SetInstanceable(True)
    return target


def main(request_path):
    request_path = Path(request_path)
    request = json.loads(request_path.read_text())
    output = Path(request["output"])
    exact_stage = Usd.Stage.CreateInMemory("exact.usda")
    twin_stage = Usd.Stage.CreateInMemory("twins.usda")
    fallbacks = {k:Vt.TokenArray(v) for k,v in request["fallbackPrimTypes"].items()}
    fallbacks["BrepArray"] = Vt.TokenArray(["Xform"])
    for stage in (exact_stage, twin_stage):
        stage.SetMetadata("fallbackPrimTypes", fallbacks)
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        UsdGeom.SetStageUpAxis(stage, "Z")
    report = dict(bodies=[], failures=[], materialSubsets=0, mappedProducts=0, prototypes=0)
    prototype_keys = set()
    for row in request["records"]:
        try:
            shape = Native.ReadBrep((request_path.parent / row["brep"]).read_text())
            if not Bridge.IsValid(shape) or not Bridge.SolidCount(shape):
                raise ValueError("IFC BRep is not a valid solid")
            source_volume = Bridge.Volume(shape)
            info = Native.Inspect(shape)
            shape = Native.SplitClosed(shape)
            canonical_info = Native.Inspect(shape)
            path = Sdf.Path(row["path"]).AppendChild("BodyExact")
            exact = UsdSolid.BrepArray.Define(exact_stage, path)
            Bridge.Write(shape, exact)
            rebuilt = Bridge.Build(exact)
            if not Bridge.IsValid(rebuilt) or not Bridge.SolidCount(rebuilt):
                raise ValueError("BrepArray did not rebuild as a valid solid")
            volume = Bridge.Volume(rebuilt)
            if abs(volume-source_volume) > max(abs(source_volume)*1e-9, 1e-12):
                raise ValueError("BrepArray volume differs from IFC BRep")
            transform = matrix(row["matrix"]) * matrix(row["elementWorld"]).GetInverse()
            UsdGeom.Xformable(exact).MakeMatrixXform().Set(transform)
            exact.CreatePurposeAttr("render")
            tolerance = max(info["tolerance"], Native.Inspect(rebuilt)["tolerance"])
            mark(exact.GetPrim(), row["id"], "body", "exact", tolerance,
                 "aeco-ifc-exact 0.2.0 occt 7.9.3 sha256=" + row["hash"])
            twin, tess = author_twin(twin_stage, exact, rebuilt, request["deflection"], transform)
            exact.CreateProxyPrimRel().SetTargets([twin.GetPath()])
            author_edges(twin_stage, exact.GetPrim(), rebuilt, request["deflection"], transform)
            color = row["styles"][:3] if row["styles"] else [0.65, 0.7, 0.75]
            twin.CreateDisplayColorAttr([color])
            report["materialSubsets"] += materials(exact, twin, tess, canonical_info, row)
            if row["mapped"]:
                key = hashlib.sha256(json.dumps([row["representationMaps"],row["hash"],row["styles"]]).encode()).hexdigest()[:16]
                make_prototype(exact_stage, exact.GetPrim(), key)
                make_prototype(twin_stage, twin.GetPrim(), key)
                prototype_keys.add(key)
                report["mappedProducts"] += 1
            # The default converter's bodies are superseded only in this overlay.
            for old in row["meshes"]:
                UsdGeom.Imageable(twin_stage.OverridePrim(old)).CreateVisibilityAttr("invisible")
            metrics = comparison(volume, Bridge.Area(rebuilt), *mesh_measures(twin), request["deflection"])
            report["bodies"].append(dict(path=row["path"], id=row["id"], ifcClass=row["ifcClass"],
                tolerance=tolerance, sourceVolume=source_volume, valid=True, solidCount=Bridge.SolidCount(rebuilt),
                faces=Bridge.FaceCount(rebuilt), mapped=row["mapped"], **metrics))
        except Exception as error:
            exact_stage.RemovePrim(Sdf.Path(row["path"]).AppendChild("BodyExact"))
            for child in ("Body", "Edges"):
                twin_stage.RemovePrim(Sdf.Path(row["path"]).AppendChild(child))
            for old in row["meshes"]:
                twin_stage.RemovePrim(old)
            report["failures"].append(dict(path=row["path"], ifcClass=row["ifcClass"], reason=str(error)))
    report["prototypes"] = len(prototype_keys)
    exact_stage.GetRootLayer().Export(str(output / "exact.usda"))
    twin_stage.GetRootLayer().Export(str(output / "twins.usda"))
    (output / "exact-report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main(sys.argv[1])
