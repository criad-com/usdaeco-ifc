"""ifc2usdaeco — IFC4/IFC4X3 -> conformant usdAeco core stage.

EXAMPLE exchange code: the reference converter behind
docs/05-ifc-mapping.md. The concept map is bijective, so this converter
is a traversal plus lookups: spatial aggregates become the typed spatial structure,
elements become Xform prims wearing AecoElementAPI classified by their
IFC class, type objects become catalog class prims composed by inherits,
systems/zones become group prims with member collections, distribution
ports become AecoPort child prims, and Psets land in the quarantined
aeco:props: tier. Core output only; downstream libraries decorate on top.

Process discipline: ifcopenshell GEOMETRY runs before pxr is imported
anywhere in the process (the two native libraries can conflict when
loaded in the other order), which is why the geometry pass is a separate
module that never imports pxr.

Requires: ifcopenshell (>= 0.8), numpy, usd-core, and the usdAeco plugin
built by build.sh (tools/ must be on sys.path for usdaeco_tools).
"""


def convert(ifc_path, out_path, overlay_spine=False, geometry=True,
            threads=None):
    """Convert `ifc_path` to a usdAeco stage rooted at `out_path`
    (.usda). Writes <out>.semantics.usda + <out>.geometry.usdc
    sublayers next to it. Returns a stats dict."""
    import ifcopenshell
    return convert_file(ifcopenshell.open(ifc_path), out_path,
                        overlay_spine=overlay_spine, geometry=geometry,
                        threads=threads)


def convert_file(ifc, out_path, overlay_spine=False, geometry=True,
                 threads=None):
    """Convert an already-open ifcopenshell file (e.g. Bonsai's
    IfcStore.get_file(), carrying unsaved edits) to `out_path`."""
    geo = {}
    if geometry:
        from .geometry import extract_geometry   # pure ifcopenshell
        geo = extract_geometry(ifc, threads=threads)

    from .author import author                   # imports pxr
    return author(ifc, geo, out_path, overlay_spine=overlay_spine)
