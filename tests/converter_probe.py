import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
from usdaeco_ifc.runtime import python as run_python
"""Run native tessellation before importing USD; shared check.py/pytest probe."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from converter_cases import CASES, ORDERING_CASES, ENUMERATION_CASES

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "tools"))
from fixtures import building, pset, assign_type
from usdaeco_ifc.convert.geometry import extract_geometry


def _run(destination):
    destination.mkdir(parents=True, exist_ok=True)
    cases = {}
    repeats = {}
    for name, options in {
        "ifc4": {"millimetres": True},
        "ifc4x3": {"schema": "IFC4X3", "spaces": True},
        "ifc4_spaces": {"spaces": True, "millimetres": True},
        "unmapped": {"wrapped": True},
        "unknown_status": {},
        "status_precedence": {},
    }.items():
        model, elements, rooms = building(**options)
        if name == "ifc4x3":
            for entity, status in zip(elements, (["NEW"], "EXISTING", "DEMOLISH")):
                pset(model, entity, "Pset_FurnishingElementCommon", {"Status": status})
            assign_type(model, [elements[3]], "TEMPORARY")
            pset(model, elements[0], "Pset_DemoUI", {
                "": "heading", " \t ": "heading", "---": "",
                "FOV Pan": 42., "FOV_Pan": "  ", "Actual": 2.5,
                "Actual!!!": "", "Enabled": False, "Zero": 0,
                "Genuine A": 12., "Genuine_A": 13.,
            })
            pset(model, elements[0], "Pset_Same Set", {"Value": 17.})
            pset(model, elements[0], "Pset_Same_Set", {"Value": "retained"})
        elif name == "unknown_status":
            assign_type(model, elements)
            for entity, status in zip(elements, ("OTHER", "NOTKNOWN", "UNSET", None)):
                pset(model, entity, "Pset_FurnishingElementCommon", {"Status": status})
        elif name == "status_precedence":
            assign_type(model, elements)
            pset(model, elements[0], "Pset_AnyClassCommon", {"Status": "NEW"})
            pset(model, elements[1], "Pset_AnyClassCommon", {"Status": "NEW"})
            pset(model, elements[1], "Pset_OtherClassCommon", {"Status": "DEMOLISH"})
            pset(model, elements[2], "Pset_AnyClassCommon", {"Status": ["NEW", "EXISTING"]})
            pset(model, elements[3], "Pset_NotCommonData", {"Status": "DEMOLISH"})
            pset(model, elements[4], "Pset_AnyClassCommon", {"Status": " temporary "})
        source = destination / (name + ".ifc")
        model.write(str(source))
        # Reopen the serialized fixture, as a file converter does.
        import ifcopenshell
        model = ifcopenshell.open(str(source))
        cases[name] = (model, extract_geometry(model, threads=1))
        if name == "ifc4x3":
            # Independent serialization and threaded tessellation before USD loads.
            other = ifcopenshell.file.from_string(model.to_string())
            repeats[name] = (other, extract_geometry(other, threads=2))

    from usdaeco_ifc.convert.author import author, guid_to_uuid
    import ifcopenshell.util.placement
    import ifcopenshell.util.unit
    import numpy as np
    import usdaeco_tools
    usdaeco_tools.register_plugins()
    from pxr import Usd, UsdGeom, Sdf, Plug
    from usdaeco_tools import validators
    claims = {}
    stages, summaries = {}, {}
    for name, (model, geometry) in cases.items():
        path = destination / (name + ".usda")
        stats = author(model, geometry, str(path))
        stage = Usd.Stage.Open(str(path))
        stages[name], summaries[name] = stage, stats
        by_id = {p.GetAttribute("aeco:id").Get(): p for p in stage.Traverse()
                 if p.GetAttribute("aeco:id")}
        expected_spaces = 30 if name in ("ifc4x3", "ifc4_spaces") else 0
        counts = {t: sum(p.GetTypeName() == t for p in stage.Traverse())
                  for t in ("AecoSite", "AecoFacility", "AecoLevel", "AecoSpace")}
        unit = ifcopenshell.util.unit.calculate_unit_scale(model)
        error = 0.
        parents_ok = True
        for rel in model.by_type("IfcRelContainedInSpatialStructure"):
            parent = by_id[guid_to_uuid(rel.RelatingStructure.GlobalId)]
            for entity in rel.RelatedElements:
                prim = by_id[guid_to_uuid(entity.GlobalId)]
                parents_ok &= prim.GetParent() == parent
                expected = ifcopenshell.util.placement.get_local_placement(entity.ObjectPlacement)
                expected[:3, 3] *= unit
                actual = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())).T
                error = max(error, float(np.max(np.abs(actual - expected))))
        errs, _ = validators.split(validators.validate_stage(stage))
        roof = stage.GetPrimAtPath("/DemoProject/Site/Building/Roof")
        if name in ("unknown_status", "status_precedence"):
            continue
        claims[name] = {
            "name": "%s spatial tree, empty roof, containment and world placement [B1/B7]" % name,
            "ok": counts == {"AecoSite": 1, "AecoFacility": 1, "AecoLevel": 3, "AecoSpace": expected_spaces}
                  and stats["unparented"] == 0 and stats["elements"] == 5
                  and parents_ok and error <= 1e-6 and not errs
                  and bool(roof) and not roof.GetChildren(),
            "detail": {"counts": counts, "unparented": stats["unparented"],
                       "placement_error_m": error, "errors": len(errs)},
        }

    def claim(key, name, ok, **detail):
        claims[key] = {"name": name, "ok": bool(ok), "detail": detail}

    def authored_phases(stage):
        return {p.GetName(): p.GetAttribute("aeco:phase").Get()
                for p in usdaeco_tools.iter_elements(stage)
                if p.GetAttribute("aeco:phase").HasAuthoredValueOpinion()}

    stage = stages["ifc4x3"]
    phases = authored_phases(stage)
    claim("phases", "Status authors exactly four uniform token phases; absent stays unauthored",
          phases == {"Item_0": "proposed", "Item_1": "existing", "Item_2": "demolished", "Item_3": "temporary"}
          and summaries["ifc4x3"]["phases"] == 4
          and all(p.GetAttribute("aeco:phase").GetVariability() == Sdf.VariabilityUniform
                  and p.GetAttribute("aeco:phase").GetTypeName() == Sdf.ValueTypeNames.Token
                  for p in usdaeco_tools.iter_elements(stage)), authored=phases)
    unknown = authored_phases(stages["unknown_status"])
    claim("unknown_status", "OTHER/NOTKNOWN/UNSET/null do not borrow the type phase",
          unknown == {"Item_4": "existing"}, authored=unknown)
    precedence = authored_phases(stages["status_precedence"])
    claim("status_precedence", "Occurrence Common Status wins; ambiguous statuses stay unauthored",
          precedence == {"Item_0": "proposed", "Item_3": "existing", "Item_4": "temporary"}, authored=precedence)

    item = next(p for p in usdaeco_tools.iter_elements(stage) if p.GetName() == "Item_0")
    props = {a.GetName(): a.Get() for a in item.GetAttributes() if a.GetName().startswith("aeco:props:Pset_DemoUI:")}
    collisions = [a.Get() for a in item.GetAttributes() if a.GetName().startswith("aeco:props:Pset_Same_Set:Value")]
    claim("headings", "Blank UI headings cannot overwrite real values; genuine collisions survive",
          summaries["ifc4x3"]["blankHeadingsOmitted"] == 5 and len(props) == 6
          and props.get("aeco:props:Pset_DemoUI:FOV_Pan") == 42.
          and props.get("aeco:props:Pset_DemoUI:Actual") == 2.5
          and props.get("aeco:props:Pset_DemoUI:Enabled") is False
          and props.get("aeco:props:Pset_DemoUI:Zero") == 0
          and sorted(v for k, v in props.items() if "Genuine_A" in k) == [12., 13.]
          and len(collisions) == 2 and 17. in collisions and "retained" in collisions,
          omitted=summaries["ifc4x3"]["blankHeadingsOmitted"], retained=props, set_collisions=collisions)

    for case, key in (("ifc4x3", "extents"), ("ifc4_spaces", "extents_mm")):
        model, geometry = cases[case]
        stage = stages[case]
        extents = [p for p in stage.Traverse() if p.GetName() == "Extent"]
        extent_error = 0.
        extents_ok = len(extents) == summaries[case]["extents"] == 2
        for room in model.by_type("IfcSpace"):
            parent = next(p for p in usdaeco_tools.iter_spatial(stage)
                          if p.GetAttribute("aeco:id").Get() == guid_to_uuid(room.GlobalId))
            extent = stage.GetPrimAtPath(parent.GetPath().AppendChild("Extent"))
            if not room.Representation:
                extents_ok &= not extent
                continue
            extents_ok &= bool(extent) and extent.IsA(UsdGeom.Mesh) and extent.HasAPI("AecoDerivedGeometryAPI")
            if not extent:
                continue
            extents_ok &= (extent.GetAttribute("aeco:derived:role").Get() == "extent"
                           and extent.GetAttribute("aeco:derived:source").Get() == guid_to_uuid(room.GlobalId)
                           and extent.GetAttribute("aeco:derived:stamp").Get().startswith("ifc2usdaeco (ifcopenshell ")
                           and UsdGeom.Imageable(extent).ComputePurpose() == "guide"
                           and not extent.HasAPI("AecoElementAPI"))
            expected = ifcopenshell.util.placement.get_local_placement(room.ObjectPlacement)
            expected[:3, 3] *= ifcopenshell.util.unit.calculate_unit_scale(model)
            actual = np.array(UsdGeom.Xformable(extent).ComputeLocalToWorldTransform(Usd.TimeCode.Default())).T
            extent_error = max(extent_error, float(np.max(np.abs(actual - expected))))
            mesh = UsdGeom.Mesh(extent)
            points = np.array(mesh.GetPointsAttr().Get())
            extents_ok &= len(mesh.GetFaceVertexIndicesAttr().Get()) == 36
            extents_ok &= bool(np.allclose(points.min(axis=0), [0., 0., 0.], atol=1e-6))
            extents_ok &= bool(np.allclose(points.max(axis=0), [4., 1., 3.], atol=1e-6))
        sem = Sdf.Layer.FindOrOpen(str(destination / (case + ".semantics.usda")))
        extents_ok &= all(sem.GetPrimAtPath(p.GetPath()) is None for p in extents)
        claim(key, case + " space bodies are placed Extent guide meshes in the geometry layer [E10/E14]",
              extents_ok and extent_error <= 1e-6, extents=len(extents), placement_error_m=extent_error)

    clean_env = {k: v for k, v in os.environ.items() if k not in ("PXR_PLUGINPATH_NAME", "PYTHONPATH")}
    vanilla_reports, vanilla_ok = {}, True
    for case in ("ifc4x3", "ifc4_spaces"):
        stage = stages[case]
        probe = run_python([ str(Path(__file__).with_name("vanilla_probe.py")),
                                str(destination / (case + ".usda"))], env=clean_env,
                               check=True, capture_output=True, text=True)
        vanilla = json.loads(probe.stdout)
        same = all(np.array_equal(np.array(matrix), np.array(UsdGeom.Xformable(stage.GetPrimAtPath(path)).ComputeLocalToWorldTransform(Usd.TimeCode.Default())))
                   for path, matrix in vanilla.pop("transforms").items())
        vanilla_reports[case] = vanilla
        vanilla_ok &= (
              vanilla == {"no_core_plugin": True, "extents": 2, "visible_extents": 0,
                          "ordinary_bound_empty": True, "guide_bound_present": True, "fallbacks": True}
              and same)
        vanilla["identical_transforms"] = same

    claim("vanilla", "Vanilla USD composes all fallbacks and ignores extents for ordinary imaging [B7]",
          vanilla_ok, **vanilla_reports)

    model, geometry = cases["ifc4x3"]
    no_geo = author(model, {}, str(destination / "no_geometry.usda"))
    claim("no_geometry", "Semantic-only conversion keeps phases and omits all meshes",
          no_geo["extents"] == no_geo["meshes"] == 0 and no_geo["phases"] == 4,
          extents=no_geo["extents"], meshes=no_geo["meshes"], phases=no_geo["phases"])

    repeat_dir = destination / "repeat"
    repeat_dir.mkdir(exist_ok=True)
    repeat_model, repeat_geometry = repeats["ifc4x3"]
    author(repeat_model, repeat_geometry, str(repeat_dir / "ifc4x3.usda"))
    matches = {suffix: (destination / ("ifc4x3" + suffix)).read_bytes() ==
               (repeat_dir / ("ifc4x3" + suffix)).read_bytes()
               for suffix in (".usda", ".semantics.usda", ".geometry.usdc")}
    claim("determinism", "Independent IFC conversion with 1/2 threads is byte-identical in all three layers",
          all(matches.values()), layers=matches)

    roles_stage = Usd.Stage.CreateInMemory()
    owner = roles_stage.DefinePrim("/Space", "AecoSpace")
    owner.GetAttribute("aeco:id").Set("1f305cbf-7296-509a-9ab3-c5f3f542a890")
    accepted, refused = [], []
    for role in ("body", "proxy", "axis", "footprint", "symbol", "extent", "sector", "coverage"):
        mesh = UsdGeom.Mesh.Define(roles_stage, "/Space/" + role).GetPrim()
        mesh.ApplyAPI("AecoDerivedGeometryAPI")
        mesh.GetAttribute("aeco:derived:source").Set(owner.GetAttribute("aeco:id").Get())
        mesh.GetAttribute("aeco:derived:role").Set(role)
        mesh.GetAttribute("purpose").Set("proxy" if role == "proxy" else "guide" if role in ("axis", "extent", "sector", "coverage") else "default")
        if not validators._validate_derived_geometry(mesh, None):
            accepted.append(role)
        if role in ("extent", "sector", "coverage"):
            mesh.GetAttribute("purpose").Set("default")
            if [e.GetName() for e in validators._validate_derived_geometry(mesh, None)] == ["derivedGeometryPurpose"]:
                refused.append(role)
    mesh.GetAttribute("aeco:derived:role").Set("unknown")
    unknown_role = [e.GetName() for e in validators._validate_derived_geometry(mesh, None)]
    claim("roles", "All eight roles validate; analytic/extent purpose and unknown roles are checked",
          len(accepted) == 8 and len(refused) == 3 and unknown_role == ["derivedGeometryRole"],
          accepted=accepted, purpose_refusals=refused, unknown_role=unknown_role)
    manifest = json.loads((Path(usdaeco_tools.ROOT) / "library.json").read_text())
    metadata = Plug.Registry().GetPluginWithName("usdAeco").metadata["aeco"]
    expected_core = json.loads((Path(__file__).resolve().parents[1] / 'dependencies.json').read_text())['repos']['core']['ref'].removeprefix('v')
    claim("version", "Codeless plugin version matches its tested dependency pin and library.json [E9]",
          metadata == {k: manifest[k] for k in ("version", "tier", "requires")}
          and metadata["version"] == expected_core, metadata=metadata)
    return claims


def run(destination, case):
    """One claim owns one fresh directory, even when the caller has residue."""
    if case not in CASES:
        raise ValueError("Unknown converter claim: " + case)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=case + "-", dir=destination) as tmp:
        if case in ENUMERATION_CASES:
            from enumeration_probe import run as enumerations
            return enumerations(tmp, case)
        if case in ORDERING_CASES:
            from ordering_probe import run as ordering
            return ordering(tmp)[case]
        return _run(Path(tmp))[case]


if __name__ == "__main__":
    destination = Path(sys.argv[1])
    if len(sys.argv) > 2:
        result = run(destination, sys.argv[2])
    else:
        # Keep the aggregate probe CLI, isolating native state for every claim.
        result = {}
        for case in CASES:
            probe = run_python([ str(Path(__file__).resolve()),
                                    str(destination), case], check=True,
                                   capture_output=True, text=True)
            result[case] = json.loads(probe.stdout)
    print(json.dumps(result, sort_keys=True))
