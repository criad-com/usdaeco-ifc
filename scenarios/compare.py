"""S5 parity: resolved drivers, derived data, topology and body bounds."""

import argparse
import json
from pxr import Sdf
from aeco_sync.stack import all_specs, value

TOL = 1e-4


def values(layer):
    result = {}
    for prim in all_specs(layer):
        for prop in prim.properties:
            if prop.name.startswith("aeco:host:") or prop.name == "aeco:derived:stamp":
                continue
            if isinstance(prop, Sdf.RelationshipSpec):
                result[str(prop.path)] = [
                    str(p) for p in prop.targetPathList.GetAppliedItems()
                ]
            elif prop.HasInfo("default"):
                if prop.name in ("faceVertexIndices", "faceVertexCounts"):
                    continue
                result[str(prop.path)] = (
                    len(prop.default) if prop.name == "points" else value(prop.default)
                )
    return result


def close(a, b, tolerance=TOL):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tolerance
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(close(a[k], b[k], tolerance) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(close(x, y, tolerance) for x, y in zip(a, b))
    return a == b


def numeric_error(a, b):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b)
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return max((numeric_error(x, y) for x, y in zip(a, b)), default=0)
    return 0


def compare(a_path, b_path, tolerance=TOL):
    a, b = [values(Sdf.Layer.FindOrOpen(str(p))) for p in (a_path, b_path)]
    keys = sorted(set(a) | set(b))
    differences = [
        k for k in keys if k not in a or k not in b or not close(a[k], b[k], tolerance)
    ]
    return {
        "compared": len(keys),
        "equal": len(keys) - len(differences),
        "differences": differences,
        "tolerance": tolerance,
        "maxNumericError": max(
            (numeric_error(a[k], b[k]) for k in keys if k in a and k in b), default=0
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("ifc_result")
    parser.add_argument("bonsai_result")
    args = parser.parse_args()
    result = compare(args.ifc_result, args.bonsai_result)
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result["differences"]))
