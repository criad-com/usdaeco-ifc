import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
"""Serialized IFC enumerations must compose without inherited type errors."""
from pathlib import Path

from fixtures import enumeration_fixture


def run(destination, case):
    from usdaeco_ifc.convert import convert

    destination = Path(destination)
    source = destination / "enumerations.ifc"
    output = destination / "enumerations.usda"
    schema = "IFC4X3" if case.endswith("ifc4x3") else "IFC4"
    mixed = case.endswith("mixed")
    enumeration_fixture(schema, mixed).write(str(source))
    # Conversion, including native tessellation, runs before USD is imported.
    stats = convert(str(source), str(output), threads=1)

    from pxr import Usd
    import usdaeco_tools
    from usdaeco_tools import validators

    stage = Usd.Stage.Open(str(output))
    items = {p.GetName(): p for p in usdaeco_tools.iter_elements(stage)}
    types = list(usdaeco_tools.iter_catalog_types(stage))
    status_name = "aeco:props:Pset_FurnishingElementTypeCommon:Status"
    expected = {"Item_0": "NEW", "Item_1": "TEMPORARY", "Item_2": "EXISTING",
                "Item_3": "DEMOLISH", "Item_4": "NEW"}
    if mixed:
        expected = {name: [value] for name, value in expected.items()}
        expected["Item_1"] = ["NEW", "TEMPORARY"]
    status_type = "string[]" if mixed else "string"

    def value(prim, name):
        attr = prim.GetAttribute(name)
        result = attr.Get()
        return list(result) if attr.GetTypeName().isArray else result

    statuses = {name: value(prim, status_name) for name, prim in items.items()}
    stacks = [a.GetPropertyStack() for p in items.values() for a in p.GetAttributes()
              if a.GetName().startswith("aeco:props:")]
    mismatches = sum(len({str(s.typeName) for s in stack}) > 1 for stack in stacks)
    type_statuses = sorted((value(p, status_name) for p in types), key=str)
    expected_types = sorted(([["EXISTING"], ["EXISTING", "TEMPORARY"]] if mixed
                             else ["EXISTING", "EXISTING"]), key=str)
    errors, _ = validators.split(validators.validate_stage(stage))
    item = items["Item_0"]
    arrays = {name: value(item, "aeco:props:" + name) for name in
              ("Pset_Selections:Modes", "Pset_Lists:Labels", "Pset_Lists:Samples")}
    expected_arrays = {"Pset_Selections:Modes": ["Middle", "Zulu"],
                       "Pset_Lists:Labels": ["Keep"], "Pset_Lists:Samples": [42.]}
    phases = {name: p.GetAttribute("aeco:phase").Get() for name, p in items.items()
              if p.GetAttribute("aeco:phase").HasAuthoredValueOpinion()}
    expected_phases = {"Item_0": "proposed", "Item_2": "existing",
                       "Item_3": "demolished", "Item_4": "proposed"}
    if not mixed:
        expected_phases["Item_1"] = "temporary"
    return {
        "name": case + ": quarantined enums keep values and one inherited type [B5/B7/E7]",
        "ok": (stats["types"] == 2 and stats["elements"] == 5
               and statuses == expected and type_statuses == expected_types
               and all(str(p.GetAttribute(status_name).GetTypeName()) == status_type
                       for p in list(items.values()) + types)
               and all(p.GetInherits().GetAllDirectInherits() for p in items.values())
               and mismatches == 0 and not errors and not stage.GetCompositionErrors()
               and arrays == expected_arrays and phases == expected_phases
               and item.GetAttribute("aeco:props:Pset_Lists:Enabled").Get() is False
               and item.GetAttribute("aeco:props:Pset_Lists:Zero").Get() == 0
               and not item.GetAttribute("aeco:props:Pset_Lists:Empty")),
        "detail": {"schema": schema, "types": len(types), "occurrences": len(items),
                   "status_type": status_type, "statuses": statuses,
                   "type_statuses": type_statuses, "arrays": arrays, "phases": phases,
                   "mismatched_property_stacks": mismatches, "errors": len(errors),
                   "error_names": sorted({e.GetName() for e in errors})},
    }
