"""aeco-ifc2usd — convert an IFC4 or IFC4X3 file to a usdAeco core stage."""
import argparse
import os
import sys
import time


def main(argv=None):
    ap = argparse.ArgumentParser(prog="aeco-ifc2usd", description=__doc__)
    ap.add_argument("ifc")
    ap.add_argument("-o", "--out",
                    help="output root .usda (default: <ifc>-aeco.usda)")
    ap.add_argument("--overlay-spine", action="store_true",
                    help="author the spatial structure as overs (a "
                         "federated discipline layer over a shared "
                         "spatial structure)")
    ap.add_argument("--no-geometry", action="store_true")
    ap.add_argument("--threads", type=int)
    args = ap.parse_args(argv)

    out = args.out or os.path.splitext(args.ifc)[0] + "-aeco.usda"
    t0 = time.time()
    from . import convert
    stats = convert(args.ifc, out,
                    overlay_spine=args.overlay_spine,
                    geometry=not args.no_geometry,
                    threads=args.threads)
    dt = time.time() - t0
    print("converted %s in %.1fs" % (args.ifc, dt))
    for k in ("spatial", "types", "elements", "systems", "zones",
              "ports", "portLinks", "meshes", "extents", "phases",
              "blankHeadingsOmitted", "unparented", "unclassified"):
        print("  %-12s %d" % (k, stats[k]))
    print("  -> %s" % stats["out"])
    if stats["unparented"]:
        print("  WARNING: %d elements had no spatial container"
              % stats["unparented"], file=sys.stderr)
    if stats["unclassified"]:
        print("  WARNING: %d elements are IfcBuildingElementProxy (kind "
              "unknown — the health metric)" % stats["unclassified"],
              file=sys.stderr)


if __name__ == "__main__":
    main()
