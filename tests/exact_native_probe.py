"""Native regression: IFC serialization survives Write/Build with measured tolerance."""
import json
from pathlib import Path
import sys
from pxr import Usd, UsdSolid, UsdSolidOcct as Bridge
import _exact_native as Native

rows = []
for path in sorted(Path(sys.argv[1]).glob("*.brep")):
    original = Native.ReadBrep(path.read_text())
    stage = Usd.Stage.CreateInMemory()
    brep = UsdSolid.BrepArray.Define(stage, "/BodyExact")
    Bridge.Write(original, brep)
    rebuilt = Bridge.Build(brep)
    assert Bridge.IsValid(rebuilt) and Bridge.SolidCount(rebuilt) == 1
    assert abs(Bridge.Volume(rebuilt)-Bridge.Volume(original)) <= Bridge.Volume(original)*1e-9
    assert Native.Inspect(original)["tolerance"] > 0
    assert Native.Edges(rebuilt, 0.0001)
    rows.append(dict(name=path.stem, volume=Bridge.Volume(rebuilt), tolerance=Native.Inspect(original)["tolerance"]))
try:
    Native.ReadBrep("not a BRep")
except ValueError:
    pass
else:
    raise AssertionError("Malformed BRep was accepted")
print(json.dumps(rows))
