import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
"""Convert a supplied generated IFC and report raw USD validation counts.

Run in a fresh process so IFC tessellation precedes USD imports. The IFC
and all output layers stay in the caller's work directory.
"""
from collections import Counter
import json
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "tools"))


def run(source, output):
    from usdaeco_ifc.convert import convert

    output.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    stats = convert(str(source), str(output), threads=1)
    elapsed = time.perf_counter() - start
    stats.pop("out", None)

    from pxr import Usd, UsdValidation
    from usdaeco_tools import validators

    stage = Usd.Stage.Open(str(output))
    registry = UsdValidation.ValidationRegistry()
    metadata = registry.GetValidatorMetadataForKeyword("UsdCoreValidators")
    context = UsdValidation.ValidationContext(
        registry.GetOrLoadValidatorsByName([m.name for m in metadata]))
    builtin, _ = validators.split(context.Validate(stage))
    core, warnings = validators.split(validators.validate_stage(stage, include_builtin=False))
    mismatches = [a for p in stage.Traverse() for a in p.GetAttributes()
                  if len({str(s.typeName) for s in a.GetPropertyStack()}) > 1]
    return {
        "source": source.name,
        "conversion_seconds": round(elapsed, 3),
        "stats": stats,
        "builtin_errors": len(builtin),
        "builtin_error_names": dict(Counter(e.GetName() for e in builtin)),
        "mismatched_property_stacks": len(mismatches),
        "core_errors": len(core),
        "core_error_names": dict(Counter(e.GetName() for e in core)),
        "core_warnings": dict(Counter(e.GetName() for e in warnings)),
        "composition_errors": len(stage.GetCompositionErrors()),
    }


if __name__ == "__main__":
    report = run(Path(sys.argv[1]), Path(sys.argv[2]))
    print(json.dumps(report, indent=2, sort_keys=True))
    sys.exit(1 if any(report[key] for key in
                     ("builtin_errors", "core_errors", "composition_errors",
                      "mismatched_property_stacks")) else 0)
