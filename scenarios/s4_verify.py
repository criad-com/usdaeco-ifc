"""S4 pass criteria: result layer written, zero gaps, refused edits diagnosed, intent holds only refused edits (then empty after withdraw)."""

import sys, json
from usdaeco_ifc._ifc_utils import *


def el(stage):
    out = {}
    for prim in stage.Traverse():
        n = prim.GetAttribute("aeco:class:ifc:name")
        if n and n.Get():
            out[n.Get()] = prim
    return out


def world_xf(prim):
    return UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())


def main(stage_path, phase):
    stage = Usd.Stage.Open(stage_path)
    e = el(stage)
    ok = True

    def check(cond, msg):
        nonlocal ok
        ok &= bool(cond)
        print(("PASS " if cond else "FAIL ") + msg)

    P1, P2, WA, WB = e["Pipe 1"], e["Pipe 2"], e["Wall A"], e["Wall B"]
    check(
        abs(P1.GetAttribute("aeco:axis:length").Get() - 2.5) < 1e-6
        and Gf.IsClose(
            P1.GetAttribute("aeco:axis:end").Get(), Gf.Vec3d(0, 0, 2.5), 1e-6
        ),
        "Pipe 1 axis resolved to 2.5 m",
    )
    res = find_layer(stage, "result.ifc.usda")
    od_res = res.GetAttributeAtPath(
        P1.GetPath().AppendProperty("aeco:pipe:outerDiameter")
    ).default
    check(
        abs(od_res - 0.065) < 1e-9
        and P1.GetAttribute("aeco:pipe:sizeLabel").Get() == "DN65",
        "Pipe 1 derived OD 0.065 / label DN65 in the result layer (composed shows the pending refused opinion until withdrawn: %.3f)"
        % P1.GetAttribute("aeco:pipe:outerDiameter").Get(),
    )
    check(
        abs(P2.GetAttribute("aeco:axis:length").Get() - 1.0) < 1e-6
        and Gf.IsClose(world_xf(P2).ExtractTranslation(), Gf.Vec3d(2.5, 0, 1), 1e-6),
        "Pipe 2 trimmed to 1.0 m, start at x=2.5 (keepConnected closure)",
    )
    # gaps from the USD side: connected ports must coincide in world space
    gaps = []
    for prim in stage.Traverse():
        if prim.GetTypeName() == "AecoPort":
            for t in prim.GetRelationship("aeco:connectedPorts").GetTargets():
                d = (
                    world_xf(prim).ExtractTranslation()
                    - world_xf(stage.GetPrimAtPath(t)).ExtractTranslation()
                ).GetLength()
                gaps.append(d)
    check(
        gaps and max(gaps) < 1e-4,
        "zero gaps between connected ports in the composed stage (%d links, max %.2e m)"
        % (len(gaps) // 2, max(gaps) if gaps else 0),
    )
    check(
        Gf.IsClose(WA.GetAttribute("aeco:axis:end").Get(), Gf.Vec3d(5, 0, 0), 1e-6)
        and Gf.IsClose(world_xf(WB).ExtractTranslation(), Gf.Vec3d(5, 0, 0), 1e-6),
        "Wall B at x=5, Wall A regenerated to 5 m to keep the join",
    )
    check(
        WA.GetAttribute("aeco:axis:length").Get()
        and abs(WA.GetAttribute("aeco:axis:length").Get() - 5.0) < 1e-6,
        "Wall A derived length 5.0",
    )
    diags = {
        p.GetAttribute("aeco:diag:code").Get(): p
        for p in stage.GetPrimAtPath("/Sync/Diagnostics/ifc").GetChildren()
    }
    codes = sorted(diags)
    if phase == "first":
        check(
            "sync:derivedAuthored" in codes and "sync:joinedEnd" in codes,
            "refused edits diagnosed: derivedAuthored, joinedEnd",
        )
        check(
            "sync:gap" not in codes and "ifc:rule" not in codes,
            "no gap, no IFC rule violation",
        )
        check(
            "pipePortSizeMismatch" in codes and "sync:neighbourMoved" in codes,
            "size mismatch warned, neighbour moves reported",
        )
        intent = find_layer(stage, "intent.usda")
        left = [
            (str(p.path), [pp.name for pp in p.properties])
            for p in _prims(intent)
            if p.properties
        ]
        check(
            sorted(n for _, ns in left for n in ns)
            == ["aeco:axis:end", "aeco:pipe:outerDiameter"],
            "intent holds exactly the two refused opinions: %s" % left,
        )
    else:
        intent = find_layer(stage, "intent.usda")
        check(
            sum(len(p.properties) for p in _prims(intent)) == 0,
            "intent empty after withdrawing the refused edits",
        )
        check(
            "sync:derivedAuthored" not in codes and "sync:joinedEnd" not in codes,
            "refusal diagnostics gone",
        )
    geom = stage.GetPrimAtPath(P1.GetPath().AppendChild("Geom"))
    check(
        geom
        and geom.HasAPI(T("AecoDerivedGeometryAPI"))
        and geom.GetAttribute("aeco:derived:role").Get() == "body"
        and len(UsdGeom.Mesh(geom).GetPointsAttr().Get()) > 0,
        "Pipe 1 Body mesh from the host, marked derived (%s)"
        % geom.GetAttribute("aeco:derived:stamp").Get(),
    )
    proxy = stage.GetPrimAtPath(P1.GetPath().AppendChild("Proxy"))
    check(
        proxy
        and abs(UsdGeom.Cylinder(proxy).GetRadiusAttr().Get() - 0.0325) < 1e-9
        and abs(UsdGeom.Cylinder(proxy).GetHeightAttr().Get() - 2.5) < 1e-9,
        "Pipe 1 Proxy cylinder re-derived stage-side (r=0.0325, h=2.5)",
    )
    result = find_layer(stage, "result.ifc.usda")
    check(
        result and result.customLayerData.get("aeco:sync:version"),
        (
            "result layer carries host/version metadata: %s"
            % dict(result.customLayerData)
            if result
            else "no result layer"
        ),
    )
    print("S4 %s: %s" % (phase, "PASS" if ok else "FAIL"))
    return ok


def _prims(layer):
    out = []

    def walk(p):
        out.append(p)
        [walk(c) for c in p.nameChildren]

    for p in layer.rootPrims:
        walk(p)
    return out


if __name__ == "__main__":
    sys.exit(0 if main(sys.argv[1], sys.argv[2]) else 1)
