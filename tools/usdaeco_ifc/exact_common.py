"""Representation correlation shared by exact export and consuming tools."""
import hashlib
import json
import math


def _plain(value):
    """Canonical numeric content, independent of USD array display formatting."""
    if isinstance(value, float):
        return 0.0 if value == 0 else value
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return [_plain(item) for item in value]


def fingerprint(prim):
    """Hash resolved authored geometry, transform and producer stamp.

    Appearance, purpose and proxy links do not change an exact body's shape.
    Correlation uses content, so deterministic rebuilds need no wall-clock stamp.
    """
    excluded = ("primvars:", "aeco:", "subsetFamily:")
    fields = [(a.GetName(), _plain(a.Get())) for a in prim.GetAttributes()
              if a.HasAuthoredValueOpinion() and not a.GetName().startswith(excluded)
              and a.GetName() not in ("purpose", "visibility", "extent", "doubleSided")]
    fields.append(("stamp", str(prim.GetAttribute("aeco:derived:stamp").Get())))
    return hashlib.sha256(json.dumps(sorted(fields), separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def mark(prim, source, role, approx, tolerance, stamp, origin=None):
    from pxr import Sdf
    prim.ApplyAPI("AecoDerivedGeometryAPI")
    for name, value, typ in (("source", source, Sdf.ValueTypeNames.String),
                             ("role", role, Sdf.ValueTypeNames.Token), ("approx", approx, Sdf.ValueTypeNames.Token),
                             ("tolerance", tolerance, Sdf.ValueTypeNames.Double), ("stamp", stamp, Sdf.ValueTypeNames.String)):
        prim.CreateAttribute("aeco:derived:" + name, typ, custom=False).Set(value)
    if origin:
        prim.CreateRelationship("aeco:derived:from", custom=False).SetTargets([origin])


def mesh_measures(mesh, transform=None):
    """Oriented triangle volume about a nearby origin, and triangle area."""
    from pxr import Gf
    points = mesh.GetPointsAttr().Get()
    counts = mesh.GetFaceVertexCountsAttr().Get()
    indices = mesh.GetFaceVertexIndicesAttr().Get()
    if not points or not counts or any(c != 3 for c in counts):
        raise ValueError("Expected a nonempty triangular twin")
    if len(indices) != sum(counts) or any(i < 0 or i >= len(points) for i in indices):
        raise ValueError("Invalid twin triangle indices")
    if transform is not None:
        points = [transform.Transform(Gf.Vec3d(p)) for p in points]
    centre = Gf.Vec3d(points[0])
    volume = area = 0.0
    for i in range(0, len(indices), 3):
        a,b,c = [Gf.Vec3d(points[j])-centre for j in indices[i:i+3]]
        volume += Gf.Dot(a, Gf.Cross(b,c))/6
        area += Gf.Cross(b-a,c-a).GetLength()/2
    return abs(volume), area


def comparison(volume, area, mesh_volume, mesh_area, tolerance):
    if not all(math.isfinite(v) and v > 0 for v in (volume, area, tolerance)):
        raise ValueError("Comparison requires a positive solid and finite tolerance")
    length = 3 * volume / area
    volume_budget = tolerance * area
    area_budget = tolerance * area / length
    return dict(volume=volume, area=area, twinVolume=mesh_volume, twinArea=mesh_area,
                volumeError=abs(mesh_volume-volume), areaError=abs(mesh_area-area),
                volumeBudget=volume_budget, areaBudget=area_budget,
                withinTolerance=abs(mesh_volume-volume) <= volume_budget and abs(mesh_area-area) <= area_budget)
