from usdaeco_ifc.runtime import python as run_python
"""Data-driven baseline -> intent -> host -> assertions, all through the CLI."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AECO_KIND_PLUGIN", str(ROOT / "tests/fixtures/usdAecoKindProto"))
from aeco_sync import register_plugins

register_plugins()
from pxr import Gf, Usd, UsdGeom
from aeco_sync.stack import Session, value
from aeco_sync.edits import collect
from scenarios.baseline import build
from scenarios.compare import compare, close
from scenarios.s4_intent import elements
from usdaeco_ifc.convert import convert


def cli(*args):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = run_python(
        [ "-m", "aeco_sync", *map(str, args)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    if proc.returncode:
        raise AssertionError(proc.stderr + "\n" + proc.stdout)
    return json.loads(proc.stdout)


def prepare(directory, case=None):
    directory.mkdir(parents=True, exist_ok=True)
    native = build(str(directory / "baseline.ifc"))
    if case:
        from scenarios.wp4 import configure
        configure(native, case.get("baseline", ""))
        native.write(str(directory / "baseline.ifc"))
    convert(str(directory / "baseline.ifc"), str(directory / "model.usda"))


def fresh_session(directory, host, policy="keepConnected"):
    target = directory / host
    cli(
        "init",
        directory / "model.usda",
        directory / "baseline.ifc",
        "--kind-import",
        "--directory",
        target,
        "--policy",
        policy,
    )
    return Session(target / "stage.usda")


def author_case(session, case):
    names = elements(session.stage)
    for name, control in case["intent"]:
        cli("--stage", session.path, "edit", names[name].GetPath(), control)
    session.intent.Reload()
    if case.get("raw"):
        session.capture_base()
        with Usd.EditContext(session.stage, session.intent):
            for name, prop, val in case["raw"]:
                names[name].GetAttribute(prop).Set(val)
        session.intent.Save()
    if case.get("relations") or case.get("type") or case.get("typeRaw"):
        from scenarios.wp4 import author_extra
        author_extra(session, case)
    if case.get("stale"):
        import ifcopenshell

        file = Path(session.document("ifc"))
        native = ifcopenshell.open(str(file))
        native.by_type("IfcProject")[
            0
        ].Description = "Changed independently after intent"
        native.write(str(file))


def assert_case(session, case, result, host="ifc"):
    for layer in session.stage.GetLayerStack():
        layer.Reload()
    current = session.current()
    names = elements(current)
    expect = case["expect"]
    for group in ("drivers", "derived"):
        assert expect[group], f"{case['id']} must assert {group}"
        for name, properties in expect[group].items():
            for prop, expected in properties.items():
                actual = value(names[name].GetAttribute(prop).Get())
                assert close(actual, expected), (
                    case["id"],
                    name,
                    prop,
                    expected,
                    actual,
                )
    codes = sorted(set(d["code"] for d in result["diagnostics"]))
    expected_codes = expect.get("hostCodes", {}).get(host, expect["codes"])
    assert codes == sorted(expected_codes), (case["id"], codes, expected_codes)
    residue = sorted(e.name for e in collect(session.stage, session.intent, current))
    assert residue == sorted(expect["residue"]), (case["id"], residue)
    links, gaps = 0, []
    cache = UsdGeom.XformCache()
    for prim in current.Traverse():
        if prim.GetTypeName() == "AecoPort":
            for peer in prim.GetRelationship("aeco:connectedPorts").GetTargets():
                links += 1
                gaps.append(
                    (
                        cache.GetLocalToWorldTransform(prim).ExtractTranslation()
                        - cache.GetLocalToWorldTransform(
                            current.GetPrimAtPath(peer)
                        ).ExtractTranslation()
                    ).GetLength()
                )
    assert max(gaps, default=0) < 1e-4
    assert links // 2 == expect.get("links", 1)
    if "fitting" in expect:
        fittings = [p for p in current.Traverse() if p.HasAPI("AecoPipeFittingAPI")]
        assert len(fittings) == 1, case["id"]
        fitting = fittings[0]
        assert fitting.GetAttribute("aeco:pipeFitting:origin").Get() == "generated"
        assert fitting.GetAttribute("aeco:class:ifc:code").Get() == "IfcPipeFitting." + expect["fitting"]
        assert fitting.GetAttribute("aeco:host:ifc:ref").Get()
        assert len([p for p in fitting.GetChildren() if p.GetTypeName() == "AecoPort"]) == 2
        assert len(UsdGeom.Mesh(fitting.GetChild("Geom")).GetPointsAttr().Get()) > 0
    if "joins" in expect:
        joins = sum(len(p.GetRelationship(name).GetTargets()) for p in current.Traverse()
                    if p.HasAPI("AecoWallAPI") for name in ("aeco:wall:joinAtStart", "aeco:wall:joinAtEnd", "aeco:wall:joinAlongPath"))
        assert joins // 2 == expect["joins"]
    return {
        "id": case["id"],
        "passed": True,
        "codes": codes,
        "residue": residue,
        "maxGap": max(gaps, default=0),
    }


def run_suite(directory, hosts=("ifc", "bonsai"), selected=()):
    cases = json.loads((ROOT / "scenarios/cases.json").read_text())
    report = {"hosts": {h: [] for h in hosts}, "parity": {}}
    datacentre_seed = None
    for case in cases:
        if selected and case["id"] not in selected:
            continue
        directory_case = directory / case["id"]
        if case.get("family") == "datacentre":
            from scenarios.datacentre import prepare as prepare_dc, run_case
            if datacentre_seed is None:
                datacentre_seed = prepare_dc(directory / "datacentre-seed")
                report["datacentreSeed"] = json.loads((datacentre_seed / "seed.json").read_text())
            rows = {}
            for host in hosts:
                if host in case["hosts"]:
                    rows[host] = run_case(directory_case / host, case["id"], host, seed=datacentre_seed)
                    report["hosts"][host].append(rows[host])
                    print(f"PASS {case['id']} [{host}]", flush=True)
            if set(rows) == {"ifc", "bonsai"}:
                assert close(rows["ifc"].get("nativeState"), rows["bonsai"].get("nativeState"), 1e-6), rows
                report["parity"][case["id"]] = dict(equal=True, scope="native camera drivers, type, presets, tour and placement")
            continue
        if case.get("family") == "camera":
            from scenarios.cctv import run_case
            rows = {}
            for host in hosts:
                if host in case["hosts"]:
                    rows[host] = run_case(directory_case / host, case["id"], host)
                    report["hosts"][host].append(rows[host])
                    print(f"PASS {case['id']} [{host}]", flush=True)
            if set(rows) == {"ifc", "bonsai"}:
                assert close(rows["ifc"]["nativeState"], rows["bonsai"]["nativeState"]), rows
                report["parity"][case["id"]] = dict(equal=True, scope="native camera drivers, type, presets, tour and placement")
            continue
        prepare(directory_case, case)
        sessions = {}
        for host in hosts:
            if host not in case["hosts"]:
                continue
            session = fresh_session(
                directory_case, host, case.get("policy", "keepConnected")
            )
            author_case(session, case)
            result = cli("--stage", session.path, "apply", "--host", host)
            entry = assert_case(session, case, result, host)
            report["hosts"][host].append(entry)
            sessions[host] = session
            print(f"PASS {case['id']} [{host}]", flush=True)
        if set(sessions) == {"ifc", "bonsai"}:
            for host, session in sessions.items():
                cli(
                    "--stage",
                    session.path,
                    "readback",
                    "--host",
                    host,
                    session.document(host),
                )
                session.layer(f"result.{host}.usda").Reload()
            parity = compare(
                sessions["ifc"].path.parent / "result.ifc.usda",
                sessions["bonsai"].path.parent / "result.bonsai.usda",
            )
            assert not parity["differences"], (case["id"], parity)
            report["parity"][case["id"]] = parity
            print(
                f"PASS parity {case['id']}: {parity['equal']}/{parity['compared']}, max {parity['maxNumericError']:.3g}",
                flush=True,
            )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hosts", nargs="+", choices=["ifc", "bonsai"], default=["ifc", "bonsai"]
    )
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="aeco-scenarios-") as tmp:
        report = run_suite(Path(tmp), args.hosts, args.case)
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
