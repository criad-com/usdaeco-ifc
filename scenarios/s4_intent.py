"""The 'editor': author driver edits in intent.usda. --withdraw removes the edits the adapter refused."""

import sys
from usdaeco_ifc._ifc_utils import *


def elements(stage):
    out = {}
    for prim in stage.Traverse():
        n = prim.GetAttribute("aeco:class:ifc:name")
        if n and n.Get():
            out[n.Get()] = prim
    return out


def main(stage_path, withdraw=False):
    stage = Usd.Stage.Open(stage_path)
    intent = find_layer(stage, "intent.usda")
    stage.SetEditTarget(Usd.EditTarget(intent))
    el = elements(stage)
    P1, WA, WB = el["Pipe 1"], el["Wall A"], el["Wall B"]
    if withdraw:
        for path, name in (
            (P1.GetPath(), "aeco:pipe:outerDiameter"),
            (WA.GetPath(), "aeco:axis:end"),
        ):
            prim_spec = intent.GetPrimAtPath(path)
            if prim_spec is not None and name in prim_spec.properties:
                prim_spec.RemoveProperty(prim_spec.properties[name])
        intent.RemoveInertSceneDescription()
        intent.Save()
        print("withdrew refused edits; intent specs:", count(intent))
        return
    P1.GetAttribute("aeco:axis:end").Set(
        Gf.Vec3d(0, 0, 2.5)
    )  # extend a CONNECTED end by 0.5 m
    P1.GetAttribute("aeco:pipe:nominalDiameter").Set(
        0.065
    )  # DN50 -> DN65 (in the type's table)
    P1.GetAttribute("aeco:pipe:outerDiameter").Set(0.09)  # DERIVED -> must be refused
    WA.GetAttribute("aeco:axis:end").Set(
        Gf.Vec3d(5, 0, 0)
    )  # joined end -> must be refused
    m = WB.GetAttribute("xformOp:transform").Get()
    m = Gf.Matrix4d(m)
    m.SetTranslateOnly(m.ExtractTranslation() + Gf.Vec3d(1, 0, 0))
    WB.GetAttribute("xformOp:transform").Set(
        m
    )  # move Wall B +1 m: Wall A must follow (keepConnected)
    intent.Save()
    print("intent authored; property specs:", count(intent))


def count(layer):
    n = 0

    def walk(prim):
        nonlocal n
        n += len(prim.properties)
        for c in prim.nameChildren:
            walk(c)

    for p in layer.rootPrims:
        walk(p)
    return n


if __name__ == "__main__":
    main(sys.argv[1], withdraw="--withdraw" in sys.argv)
