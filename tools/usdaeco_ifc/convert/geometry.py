"""Geometry pass — pure ifcopenshell, runs BEFORE pxr is imported.

The iterator is the bulk engine (all products, threaded, booleans
applied); create_shape is the per-product fallback for anything the
iterator skips. Verts stay LOCAL; the shape's 16-float row-major matrix
becomes the element prim's xformOp so meshes compose like authored USD
rather than a world-space soup.

Scale note: this authors per-element geometry. Deduplicating identical
products via IFC representation maps into USD prototypes (instanceable
references) is the scale mechanism for large models; local verts plus a
per-element xformOp is exactly the shape that makes that swap mechanical.
"""
import multiprocessing


def _rgb(material):
    d = getattr(material, "diffuse", None)
    if d is None:
        return None
    try:                       # 0.8 colour object: callables or attrs
        r, g, b = d.r(), d.g(), d.b()
        return (float(r), float(g), float(b))
    except TypeError:
        try:
            return (float(d.r), float(d.g), float(d.b))
        except Exception:
            pass
    except AttributeError:
        pass
    try:
        t = tuple(d)
        if len(t) >= 3:
            return tuple(float(x) for x in t[:3])
    except Exception:
        pass
    return None


def _dominant_color(geometry):
    mats = list(geometry.materials)
    if not mats:
        return None
    ids = list(geometry.material_ids)
    if not ids:
        return _rgb(mats[0])
    counts = {}
    for i in ids:
        counts[i] = counts.get(i, 0) + 1
    best = max(counts, key=counts.get)
    if 0 <= best < len(mats):
        return _rgb(mats[best])
    return _rgb(mats[0])


def _entry(shape):
    g = shape.geometry
    return {
        "verts": list(g.verts),            # local, metres, flat xyz
        "faces": list(g.faces),            # triangles, flat indices
        "matrix": list(shape.transformation.matrix),   # 16, row-major
        "color": _dominant_color(g),
    }


def extract_geometry(ifc, threads=None):
    """{GlobalId: {verts, faces, matrix, color}} for every meshable
    element body and space extent (excluding openings and virtual elements)."""
    import ifcopenshell.geom
    settings = ifcopenshell.geom.settings()
    settings.set("weld-vertices", True)
    exclude = ifc.by_type("IfcOpeningElement") + ifc.by_type("IfcVirtualElement")
    out = {}
    it = ifcopenshell.geom.iterator(
        settings, ifc, threads or multiprocessing.cpu_count(),
        exclude=exclude or None)
    if it.initialize():
        while True:
            shape = it.get()
            out[shape.guid] = _entry(shape)
            if not it.next():
                break
    # fallback sweep: products with a representation the iterator missed
    missed = [p for p in ifc.by_type("IfcElement") + ifc.by_type("IfcSpace")
              if p.Representation is not None
              and p.GlobalId not in out
              and not p.is_a("IfcOpeningElement")
              and not p.is_a("IfcVirtualElement")]
    for p in missed:
        try:
            shape = ifcopenshell.geom.create_shape(settings, p)
            entry = _entry(shape)
            if entry["verts"]:
                out[p.GlobalId] = entry
        except Exception:
            continue
    return out
