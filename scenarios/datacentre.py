from usdaeco_ifc.runtime import python as run_python
"""Generated demo data centre camera transactions on IFC and native Bonsai."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import ifcopenshell
import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom
from aeco_sync import engine
from aeco_sync.cctv import SENSOR, PRESET, is_sensor, set_values
from usdaeco_ifc import _ifc_cctv as native
from aeco_sync.identity import uuid_to_guid
from aeco_sync.readback import bind
from aeco_sync.stack import create, digest, value
from scenarios.cctv import native_state

CASES = ("D-create", "D-move", "D-pan-tilt", "D-focal-refused", "D-preset",
         "D-type-swap", "D-delete", "D-repeat-apply", "D-converge")


def generator_root():
    root = Path(os.environ.get("AECO_DATACENTRE_ROOT", Path(__file__).resolve().parents[2] / "usdaeco-datacentre"))
    if not (root / "src/dcbuild").is_dir():
        raise FileNotFoundError("Set AECO_DATACENTRE_ROOT to the data-centre generator checkout")
    return root


# Resolve once: the IFC builder and all expectation artifacts consume this plan.
# Run outside the source checkout and disable bytecode before importing it.
BUILD = """
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
import json
from pathlib import Path
from dcbuild import layout, spec
from dcbuild.ifc.build import build
from dcbuild.qa.manifest import manifest
from dcbuild.ids import guid
from dcbuild.camera_contract import type_payload, occurrence_payload
plan = layout.resolve(spec.load())
plan.write_json(Path('out/build_plan.json'))
plan.write_targets(Path('out/targets.json'))
Path('manifests').mkdir()
Path('manifests/resolved.json').write_text(json.dumps(manifest(plan), sort_keys=True))
contracts = {
    'spaces': {s.id: guid(s.id) for s in plan.spaces},
    'types': {name: {'GlobalId': guid('type.sec.cam.' + name), **type_payload(name, cfg)}
              for name, cfg in plan.security['camera_types'].items()},
    'cameras': {c.id: occurrence_payload(c) for c in plan.cameras},
}
Path('out/camera_contracts.json').write_text(json.dumps(contracts, sort_keys=True))
build(plan, Path('ifc'))
"""


def resolve_expectations(directory):
    """Read generator evidence, independent of the IFC writer and USD importer."""
    directory = Path(directory)
    plan = json.loads((directory / "out/build_plan.json").read_text())
    manifest = json.loads((directory / "manifests/resolved.json").read_text())
    targets = json.loads((directory / "out/targets.json").read_text())
    contracts = json.loads((directory / "out/camera_contracts.json").read_text())
    cameras = sorted(plan["cameras"], key=lambda c: c["id"])
    camera_ids = {c["id"]: c["global_id"] for c in cameras}
    assert camera_ids == manifest["camera_ids"] == {c["id"]: c["GlobalId"] for c in targets["cameras"]}, "Generator camera identities disagree"
    counts = dict(cameras=manifest["counts"]["cameras"], sensors=manifest["heads"], heads=manifest["heads"],
                  types=manifest["camera_types"], presets=manifest["presets"],
                  doors=manifest["counts"]["doors"], spaces=manifest["counts"]["spaces"])
    assert counts["cameras"] == len(cameras) == len(contracts["cameras"]), "Generator camera counts disagree"
    assert counts["doors"] == len(plan["doors"]) == len(targets["doors"]), "Generator door counts disagree"
    assert counts["spaces"] == len(plan["spaces"]) == len(contracts["spaces"]), "Generator space counts disagree"
    assert counts["types"] == len(plan["security"]["camera_types"]) == len(contracts["types"]), "Generator type counts disagree"
    heads = [s for c in contracts["cameras"].values() for s in c["Sensors"]]
    assert counts["heads"] == len(heads), "Generator head counts disagree"
    assert counts["presets"] == sum(len(s.get("presets", {})) for s in heads), "Generator preset counts disagree"

    # Select by spec identity and capabilities, never by USD traversal order.
    choices = []
    for c in cameras:
        sensor = contracts["types"][c["type"]]["Sensors"][0]
        if c["space"] not in contracts["spaces"] or sensor["drivers"][SENSOR + "motorised"]:
            continue
        lo, hi = sensor["drivers"][SENSOR + "focalRange"]
        for name, typ in sorted(contracts["types"].items()):
            if name == c["type"]:
                continue
            other_lo, other_hi = typ["Sensors"][0]["drivers"][SENSOR + "focalRange"]
            low, high = max(lo, other_lo), min(hi, other_hi)
            if low <= high and lo < hi:
                choices.append((c, sensor["name"], name, (low + high) / 2, lo, hi))
    if not choices:
        raise ValueError("D-* requires a fixed camera in a space and an alternate type with overlapping focal envelopes")
    c, sensor, alternate, swap_focal, lo, hi = choices[0]
    focal = (lo + hi) / 2
    if focal == c["focal_length"]:
        focal = (lo + focal) / 2
    preset_choices = [(c, s, name) for c in cameras for s in contracts["cameras"][c["id"]]["Sensors"]
                      for name in sorted(s.get("presets", {}))]
    if not preset_choices:
        raise ValueError("D-preset requires a camera with a preset in the generator plan")
    pc, ps, preset = preset_choices[0]
    selection = dict(camera=c["id"], cameraGuid=c["global_id"], sensor=sensor,
                     space=c["space"], spaceGuid=contracts["spaces"][c["space"]],
                     type=c["type"], typeGuid=contracts["types"][c["type"]]["GlobalId"],
                     alternateType=alternate, alternateTypeGuid=contracts["types"][alternate]["GlobalId"],
                     focal=focal, swapFocal=swap_focal, refusedFocal=hi + max(1., hi - lo),
                     pan=35. if c["pan"] != 35. else 36., tilt=40. if c["tilt"] != 40. else 41.,
                     presetCamera=pc["id"], presetCameraGuid=pc["global_id"], presetSensor=ps["name"], preset=preset,
                     presetPan=ps["presets"][preset]["pan"] + 1., presetDwell=ps["presets"][preset]["dwell"] + 1.)
    return dict(counts=counts, selection=selection, cameraIds=camera_ids,
                doorIds={d["id"]: d["GlobalId"] for d in targets["doors"]},
                spaceIds=contracts["spaces"], typeIds={n: t["GlobalId"] for n, t in contracts["types"].items()},
                cameraHeads={n: len(c["Sensors"]) for n, c in contracts["cameras"].items()},
                cameraPresets={n: sum(len(s.get("presets", {})) for s in c["Sensors"])
                               for n, c in contracts["cameras"].items()},
                typeHeads={n: len(t["Sensors"]) for n, t in contracts["types"].items()})


def assert_imported(imported, counts):
    for key in ("cameras", "sensors", "types", "presets"):
        assert imported[key] == counts[key], (key, imported[key], counts[key])
    assert imported["unmatched"] == 0, imported


def native_census(f):
    cameras = [e for e in f.by_type("IfcAudioVisualAppliance") if native.is_camera(e)]
    sensors = [s for c in cameras for s in native.load(c, "Sensors", [])]
    types = [e for e in f.by_type("IfcAudioVisualApplianceType") if e.PredefinedType == "CAMERA"]
    return dict(cameras=len(cameras), sensors=len(sensors), heads=len(sensors), types=len(types),
                presets=sum(len(s.get("presets", {})) for s in sensors),
                doors=len(f.by_type("IfcDoor")), spaces=len(f.by_type("IfcSpace")))


def prepare(directory, *, spec_directory=None):
    """Build in a temporary directory using only the pinned checkout's source/spec."""
    from usdaeco_ifc.convert import convert
    from usdaeco_cctv.importer import import_cctv
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    root = generator_root()
    shutil.copytree(spec_directory or root / "spec", directory / "spec")
    print("== stage: generate data-centre plan, manifest and IFC", flush=True)
    start = time.perf_counter()
    result = run_python([ "-c", BUILD, str(root / "src")], cwd=directory,
                            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"}, capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError("Demo generator failed: " + result.stderr)
    build_seconds = time.perf_counter() - start
    expectations = resolve_expectations(directory)
    source = directory / "ifc/demo-datacentre-01.ifc"
    core, kind = directory / "core.usda", directory / "cctv.usda"
    start = time.perf_counter()
    converted = convert(str(source), str(core))
    convert_seconds = time.perf_counter() - start
    imported = import_cctv(core, source, kind)
    assert_imported(imported, expectations["counts"])
    # An additive seed binds the converter's identity paths and inherited types.
    layer = Sdf.Layer.CreateNew(str(directory / "bound.usda"))
    layer.subLayerPaths = ["cctv.usda"]
    stage = Usd.Stage.Open(layer)
    f, version = ifcopenshell.open(str(source)), digest(source)
    assert native_census(f) == expectations["counts"], (native_census(f), expectations["counts"])
    for ids, ifc_class in (("cameraIds", "IfcAudioVisualAppliance"), ("doorIds", "IfcDoor"),
                           ("spaceIds", "IfcSpace"), ("typeIds", "IfcAudioVisualApplianceType")):
        actual = {e.GlobalId for e in f.by_type(ifc_class)}
        assert actual == set(expectations[ids].values()), (ids, actual ^ set(expectations[ids].values()))
    for prim in stage.Traverse():
        identity = prim.GetAttribute("aeco:id")
        if not identity or not identity.Get():
            continue
        ref = uuid_to_guid(identity.Get())
        if prim.HasAPI("AecoCctvCameraAPI") or prim.GetTypeName() in ("AecoSite", "AecoFacility", "AecoLevel", "AecoSpace"):
            bind(prim, "ifc", ref, "", version, source)
        if prim.HasAPI("AecoCctvCameraAPI"):
            typ = native.uel.get_type(f.by_guid(ref))
            catalog = stage.GetPrimAtPath(prim.GetInherits().GetAllDirectInherits()[0])
            bind(catalog, "ifc", typ.GlobalId, "", version, source)
    layer.Save()
    evidence = dict(**expectations["counts"], expectations=expectations,
                    buildSeconds=build_seconds, convertSeconds=convert_seconds)
    (directory / "seed.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return directory


def session_from_seed(seed, directory):
    return create(seed / "bound.usda", seed / "ifc/demo-datacentre-01.ifc", directory)


def by_guid(stage, guid):
    matches = [p for p in stage.TraverseAll() if p.GetAttribute("aeco:host:ifc:ref").Get() == guid]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one bound prim for generator identity {guid}; found {len(matches)}")
    return matches[0]


def publishable_catalog(session, original):
    path = Sdf.Path("/_Types/DemoPublished")
    with Usd.EditContext(session.stage, session.layer("kind.usda")):
        prim = session.stage.CreateClassPrim(path)
        prim.ApplyAPI("AecoTypeAPI")
        prim.ApplyAPI("AecoCctvCameraTypeAPI")
        set_values(prim, {a.GetName(): value(a.Get()) for a in original.GetAttributes()
                         if a.GetName().startswith(("aeco:cctvType:", "aeco:type:")) and a.HasValue() and not a.GetMetadata("aecoDerived")})
        for child in original.GetAllChildren():
            if is_sensor(child):
                sensor = UsdGeom.Camera.Define(session.stage, path.AppendChild(child.GetName())).GetPrim()
                sensor.ApplyAPI("AecoCctvSensorAPI")
                set_values(sensor, {a.GetName(): value(a.Get()) for a in child.GetAttributes()
                                    if a.GetName().startswith(SENSOR) and a.HasValue() and not a.GetMetadata("aecoDerived")})
    session.layer("kind.usda").Save()
    return path


def run_case(directory, case, host="ifc", *, seed=None):
    if case not in CASES:
        raise ValueError(case)
    directory = Path(directory)
    seed = Path(seed) if seed is not None else prepare(directory / "seed")
    expectations = resolve_expectations(seed)
    counts, selected = expectations["counts"], expectations["selection"]
    expected = dict(counts)
    chosen = selected["presetCamera"] if case == "D-preset" else selected["camera"]
    if case in ("D-create", "D-delete"):
        delta = 1 if case == "D-create" else -1
        expected["cameras"] += delta
        expected["sensors"] += delta * expectations["cameraHeads"][chosen]
        expected["heads"] = expected["sensors"]
        expected["presets"] += delta * expectations["cameraPresets"][chosen] if delta < 0 else 0
        expected["types"] += int(case == "D-create")
    elif case == "D-type-swap":
        expected["sensors"] += expectations["typeHeads"][selected["alternateType"]] - expectations["cameraHeads"][chosen]
        expected["heads"] = expected["sensors"]
    evidence = dict(counts=counts, after=expected, selection=selected)
    print(f"== stage: {case} [{host}] resolved " + json.dumps(evidence, sort_keys=True), flush=True)
    session = session_from_seed(seed, directory / "sync")
    cp = by_guid(session.stage, selected["presetCameraGuid"] if case == "D-preset" else selected["cameraGuid"])
    path = cp.GetPath()
    sensor = cp.GetChild(selected["presetSensor"] if case == "D-preset" else selected["sensor"])
    original_catalog = session.stage.GetPrimAtPath(cp.GetInherits().GetAllDirectInherits()[0])
    file_before = session.document(host)
    baseline_hash = digest(file_before)
    assert native_census(ifcopenshell.open(file_before)) == counts
    catalog = publishable_catalog(session, original_catalog) if case == "D-create" else None
    before_matrix = UsdGeom.XformCache().GetLocalToWorldTransform(cp)
    session.capture_base([])
    with Usd.EditContext(session.stage, session.intent):
        if case == "D-create":
            space = by_guid(session.stage, selected["spaceGuid"])
            assert space.GetTypeName() == "AecoSpace", selected
            path = space.GetPath().AppendChild("NewCamera")
            new = UsdGeom.Xform.Define(session.stage, path).GetPrim()
            new.ApplyAPI("AecoElementAPI")
            new.ApplyAPI("AecoCctvCameraAPI")
            new.GetAttribute("aeco:phase").Set("proposed")
            new.GetInherits().SetInherits([catalog])
            UsdGeom.Xformable(new).MakeMatrixXform().Set(Gf.Matrix4d().SetTranslate(Gf.Vec3d(1., 2., 2.9)))
            set_values(new.GetChild(selected["sensor"]), {SENSOR + "pan": selected["pan"], SENSOR + "tilt": selected["tilt"], SENSOR + "focalLength": selected["focal"]})
            desired_matrix = UsdGeom.XformCache().GetLocalToWorldTransform(new)
        elif case == "D-move":
            matrix = Gf.Matrix4d(UsdGeom.Xformable(cp).GetLocalTransformation())
            matrix.SetTranslateOnly(matrix.ExtractTranslation() + Gf.Vec3d(.5, 0, .1))
            UsdGeom.Xformable(cp).MakeMatrixXform().Set(matrix)
            desired_matrix = UsdGeom.XformCache().GetLocalToWorldTransform(cp)
        elif case in ("D-pan-tilt", "D-repeat-apply"):
            sensor.GetAttribute(SENSOR + "pan").Set(selected["pan"])
            sensor.GetAttribute(SENSOR + "tilt").Set(selected["tilt"])
        elif case == "D-focal-refused":
            sensor.GetAttribute(SENSOR + "focalLength").Set(selected["refusedFocal"])
        elif case == "D-preset":
            preset = selected["preset"]
            sensor.GetAttribute(PRESET + preset + ":pan").Set(selected["presetPan"])
            sensor.GetAttribute(PRESET + preset + ":dwell").Set(selected["presetDwell"])
        elif case == "D-type-swap":
            alternate = by_guid(session.stage, selected["alternateTypeGuid"])
            cp.GetInherits().SetInherits([alternate.GetPath()])
            sensor.GetAttribute(SENSOR + "focalLength").Set(selected["swapFocal"])
        elif case == "D-delete":
            cp.SetActive(False)
        elif case == "D-converge":
            sensor.GetAttribute(SENSOR + "focalLength").Set(selected["focal"])
        else:
            raise ValueError(case)
    session.intent.Save()
    start = time.perf_counter()
    result = engine.apply(session, host)
    elapsed = time.perf_counter() - start
    codes = [d["code"] for d in result["diagnostics"]]
    if case == "D-focal-refused":
        assert codes == ["cctvOutOfEnvelope"] and result["mutations"] == 0 and result["pending"] == 1, result
        assert digest(session.document(host)) == baseline_hash
        from aeco_sync.edits import withdraw
        withdraw(session)
        assert session.status()["inSync"]
        repeat = engine.apply(session, host)
        assert repeat["inSync"] and repeat["mutations"] == 0 and repeat["publishedTypes"] == [], repeat
        return dict(id=case, host=host, passed=True, mutations=0, repeatMutations=0,
                    pendingAfterRefusal=1, pendingAfterWithdraw=0, codes=codes, seconds=elapsed,
                    expectations=evidence, census=native_census(ifcopenshell.open(session.document(host))))
    assert result["inSync"] and result["accepted"] > 0 and not any(d["blocking"] for d in result["diagnostics"]), result
    current = session.current()
    prim = current.GetPrimAtPath(path)
    f = ifcopenshell.open(session.document(host))
    census = native_census(f)
    assert census == expected, (case, census, expected)
    if case == "D-delete":
        assert not prim.IsActive()
        assert selected["cameraGuid"] not in {e.GlobalId for e in f.by_type("IfcAudioVisualAppliance")}
        entity = None
    else:
        entity = f.by_guid(prim.GetAttribute(f"aeco:host:{host}:ref").Get())
        state = native_state(entity)
        if case in ("D-create", "D-move"):
            assert np.allclose(np.array(state["matrix"]), np.array(desired_matrix).T, atol=1e-6)
        if case == "D-create":
            assert native.uel.get_container(entity, should_get_direct=True).GlobalId == selected["spaceGuid"]
            assert result["publishedTypes"] == [str(catalog)]
            assert entity.Representation
            assert current.GetPrimAtPath(catalog).GetAttribute(f"aeco:host:{host}:ref").Get() == native.uel.get_type(entity).GlobalId
        if case in ("D-pan-tilt", "D-repeat-apply"):
            assert state["sensors"][0]["drivers"][SENSOR + "pan"] == selected["pan"]
            assert state["sensors"][0]["drivers"][SENSOR + "tilt"] == selected["tilt"]
            assert np.allclose(state["matrix"], np.array(before_matrix).T, atol=1e-6)
        if case == "D-preset":
            assert state["sensors"][0]["presets"][preset]["pan"] == selected["presetPan"]
            assert state["sensors"][0]["presets"][preset]["dwell"] == selected["presetDwell"]
        if case == "D-type-swap":
            assert native.uel.get_type(entity).GlobalId == alternate.GetAttribute("aeco:host:ifc:ref").Get()
        if case == "D-converge":
            from usdaeco_cctv.importer import import_cctv
            from usdaeco_cctv.derive import derive
            fresh_path = directory / "fresh.usda"
            imported = import_cctv(seed / "core.usda", session.document(host), fresh_path)
            fresh = Usd.Stage.Open(str(fresh_path))
            layer = Sdf.Layer.CreateAnonymous()
            derived = derive(fresh, layer)
            assert_imported(imported, expected)
            assert derived["sensors"] == expected["heads"] and not derived["skipped"], derived
            assert fresh.GetPrimAtPath(sensor.GetPath()).GetAttribute(SENSOR + "focalLength").Get() == selected["focal"]
            result["convergedSensors"] = derived["sensors"]
    repeat = engine.apply(session, host)
    assert repeat["inSync"] and repeat["mutations"] == 0 and repeat["publishedTypes"] == [], repeat
    return dict(id=case, host=host, passed=True, pending=0, mutations=result["mutations"], publishedTypes=len(result["publishedTypes"]),
                repeatMutations=repeat["mutations"], nativeState=native_state(entity), nativeEvidence=result["nativeEvidence"],
                convergedSensors=result.get("convergedSensors"), seconds=elapsed, codes=codes,
                expectations=evidence, census=census)
