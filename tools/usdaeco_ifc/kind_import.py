"""Importer kind pass (docs/12 §12.3/§12.4): core stage + IFC -> kind.usda (drivers, bindings), derived.usda (Axis/Proxy), stage.usda (layer stack)."""

import sys, os
import ifcopenshell.util.unit
from ._ifc_utils import *
from aeco_sync.derive import derive_gprims
from aeco_sync.stack import create, digest, facts

TABLE = {
    "nominal": [0.05, 0.065, 0.08],
    "outer": [0.05, 0.065, 0.08],
    "inner": [0.044, 0.059, 0.074],
}


def main(ifc_path, model_usda, outdir):
    if T("AecoPipeAPI").isUnknown or T("AecoWallAPI").isUnknown:
        raise ValueError(
            "Kind import requires AECO_KIND_PLUGIN (see the README prototype fixture setup)"
        )
    f = ifcopenshell.open(ifc_path)
    if abs(ifcopenshell.util.unit.calculate_unit_scale(f) - 1.0) > 1e-12:
        raise ValueError(
            "The prototype kind importer currently requires IFC length units in metres"
        )
    session = create(model_usda, ifc_path, outdir)
    stage = session.stage
    root = session.root
    idx = index_ids(stage)
    version = digest(ifc_path)
    kind = session.layer("kind.usda")
    derived = session.layer("derived.usda")

    def bind(prim, e):
        prim.ApplyAPI(T("AecoHostBindingAPI"), "ifc")
        prim.GetAttribute("aeco:host:ifc:ref").Set(e.GlobalId)
        prim.GetAttribute("aeco:host:ifc:localRef").Set("#%d" % e.id())
        prim.GetAttribute("aeco:host:ifc:version").Set(version)
        prim.GetAttribute("aeco:host:ifc:document").Set(os.path.abspath(ifc_path))

    stage.SetEditTarget(Usd.EditTarget(kind))
    n = 0
    for e in f.by_type("IfcElement"):
        prim = prim_for(idx, e)
        if prim is None:
            continue
        bind(prim, e)
        n += 1
        tpaths = prim.GetInherits().GetAllDirectInherits()
        tprim = stage.GetPrimAtPath(tpaths[0]) if tpaths else None
        if tprim is not None:
            native_type = uel.get_type(e)
            if native_type:
                bind(tprim, native_type)
        if e.is_a("IfcPipeSegment"):
            prof = pipe_profile(e)
            if prof is None:
                continue  # retain the core review representation for unsupported sections
            s, en, d = pipe_axis_local(e)
            r = float(prof.Radius)
            t = float(getattr(prof, "WallThickness", prof.Radius))
            prim.ApplyAPI(T("AecoAxisAPI"))
            prim.GetAttribute("aeco:axis:start").Set(Gf.Vec3d(*s))
            prim.GetAttribute("aeco:axis:end").Set(Gf.Vec3d(*en))
            prim.GetAttribute("aeco:axis:length").Set(d)
            prim.ApplyAPI(T("AecoPipeAPI"))
            prim.GetAttribute("aeco:pipe:nominalDiameter").Set(2 * r)
            prim.GetAttribute("aeco:pipe:outerDiameter").Set(2 * r)
            prim.GetAttribute("aeco:pipe:innerDiameter").Set(2 * (r - t))
            prim.GetAttribute("aeco:pipe:sizeLabel").Set(prof.ProfileName or "")
            if tprim is not None:
                tprim.ApplyAPI(T("AecoPipeTypeAPI"))
                mat = uel.get_material(e, should_skip_usage=True)
                tprim.GetAttribute("aeco:pipeType:material").Set(
                    mat.MaterialProfiles[0].Material.Name
                    if mat and mat.is_a("IfcMaterialProfileSet") and mat.MaterialProfiles and mat.MaterialProfiles[0].Material
                    else ""
                )
                tprim.GetAttribute("aeco:pipeType:nominalDiameters").Set(
                    Vt.DoubleArray(TABLE["nominal"])
                )
                tprim.GetAttribute("aeco:pipeType:outerDiameters").Set(
                    Vt.DoubleArray(TABLE["outer"])
                )
                tprim.GetAttribute("aeco:pipeType:innerDiameters").Set(
                    Vt.DoubleArray(TABLE["inner"])
                )
            for port, _ in ports_of(e):
                pp = prim_for(idx, port)
                if pp is None:
                    continue
                bind(pp, port)
                pp.ApplyAPI(T("AecoPipePortAPI"))
                pp.GetAttribute("aeco:pipePort:nominalDiameter").Set(2 * r)
            stage.SetEditTarget(Usd.EditTarget(derived))
            derive_gprims(stage, prim, "pipe", "kind_import")
            stage.SetEditTarget(Usd.EditTarget(kind))
        elif e.is_a("IfcWall"):
            s, en = wall_axis_local(e)
            L = float(np.linalg.norm(en - s))
            th = wall_thickness(e)
            h = wall_height(e)
            usage = uel.get_material(e)
            prim.ApplyAPI(T("AecoAxisAPI"))
            prim.GetAttribute("aeco:axis:start").Set(Gf.Vec3d(*s))
            prim.GetAttribute("aeco:axis:end").Set(Gf.Vec3d(*en))
            prim.GetAttribute("aeco:axis:length").Set(L)
            prim.ApplyAPI(T("AecoWallAPI"))
            prim.GetAttribute("aeco:wall:height").Set(h)
            prim.GetAttribute("aeco:wall:thickness").Set(th)
            prim.GetAttribute("aeco:wall:flipped").Set(
                bool(
                    usage
                    and usage.is_a("IfcMaterialLayerSetUsage")
                    and usage.DirectionSense == "NEGATIVE"
                )
            )
            off = (
                float(usage.OffsetFromReferenceLine or 0.0)
                if usage and usage.is_a("IfcMaterialLayerSetUsage")
                else 0.0
            )
            prim.GetAttribute("aeco:wall:locationLine").Set(
                "centerline" if abs(off + th / 2) < 1e-6 else "finishFaceExterior"
            )
            prim.GetAttribute("aeco:wall:grossSideArea").Set(L * h)
            prim.GetAttribute("aeco:wall:grossVolume").Set(L * h * th)
            for rel in e.ConnectedTo:
                if rel.is_a("IfcRelConnectsPathElements"):
                    other = prim_for(idx, rel.RelatedElement)
                    end = rel.RelatingConnectionType
                    if other is not None and end in ("ATSTART", "ATEND", "ATPATH"):
                        prim.GetRelationship(
                            {
                                "ATSTART": "aeco:wall:joinAtStart",
                                "ATEND": "aeco:wall:joinAtEnd",
                                "ATPATH": "aeco:wall:joinAlongPath",
                            }[end]
                        ).AddTarget(other.GetPath())
            for rel in e.ConnectedFrom:
                if rel.is_a("IfcRelConnectsPathElements"):
                    other = prim_for(idx, rel.RelatingElement)
                    end = rel.RelatedConnectionType
                    if other is not None and end in ("ATSTART", "ATEND", "ATPATH"):
                        prim.GetRelationship(
                            {
                                "ATSTART": "aeco:wall:joinAtStart",
                                "ATEND": "aeco:wall:joinAtEnd",
                                "ATPATH": "aeco:wall:joinAlongPath",
                            }[end]
                        ).AddTarget(other.GetPath())
            if tprim is not None:
                layers = uel.get_material_layers(e)
                tprim.ApplyAPI(T("AecoBuildUpAPI"))
                tprim.GetAttribute("aeco:buildUp:thicknesses").Set(
                    Vt.DoubleArray([float(l.thickness) for l in layers])
                )
                tprim.GetAttribute("aeco:buildUp:priorities").Set(
                    Vt.IntArray([int(l.priority or 0) for l in layers])
                )
                tprim.GetAttribute("aeco:buildUp:functions").Set(
                    Vt.TokenArray(["structure"] * len(layers))
                )
                tprim.GetAttribute("aeco:buildUp:totalThickness").Set(th)
            stage.SetEditTarget(Usd.EditTarget(derived))
            derive_gprims(stage, prim, "wall", "kind_import")
            stage.SetEditTarget(Usd.EditTarget(kind))
    # Imported derived values live with guides, separate from editable drivers.
    from aeco_sync.stack import all_specs
    derived_paths = [prop.path for spec in all_specs(kind) for prop in spec.properties.values()
                     if stage.GetPropertyAtPath(prop.path).GetMetadata("aecoDerived")]
    for path in derived_paths:
        Sdf.CreatePrimInLayer(derived, path.GetPrimPath())
        Sdf.CopySpec(kind, path, derived, path)
        prop = kind.GetPropertyAtPath(path)
        prop.owner.RemoveProperty(prop)
    kind.customLayerData = facts(
        "ifc", os.path.abspath(ifc_path), version, session.policy
    )
    kind.Save()
    derived.Save()
    root.Save()
    return session


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
