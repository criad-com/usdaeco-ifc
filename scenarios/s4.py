"""The original five-edit S4 sequence and S5 Bonsai parity through aeco-sync."""

from pathlib import Path
from pxr import Usd
from aeco_sync.stack import Session
from .run import prepare, fresh_session, cli
from .s4_intent import elements
from .s4_verify import main as verify
from .compare import compare


def run(directory, bonsai=True):
    prepare(directory)
    sessions = {}
    for host in ("ifc", "bonsai") if bonsai else ("ifc",):
        session = fresh_session(directory, host)
        names = elements(session.stage)
        for name, controls in [
            ("Pipe 1", ["length=2.5", "diameter=.065"]),
            ("Wall A", ["length=5"]),
            ("Wall B", ["move=1,0,0"]),
        ]:
            cli("--stage", session.path, "edit", names[name].GetPath(), *controls)
        session.intent.Reload()
        with Usd.EditContext(session.stage, session.intent):
            names["Pipe 1"].GetAttribute("aeco:pipe:outerDiameter").Set(0.09)
        session.intent.Save()
        first = cli("--stage", session.path, "apply", "--host", host)
        assert first["accepted"] == 3 and first["refused"] == 2, first
        assert sorted(set(d["code"] for d in first["diagnostics"])) == [
            "ifcopenshell:geometry",
            "pipePortSizeMismatch",
            "sync:derivedAuthored",
            "sync:joinedEnd",
            "sync:neighbourMoved",
        ], first
        for layer in session.stage.GetLayerStack():
            layer.Reload()
        if host == "ifc":
            assert verify(str(session.path), "first")
        withdrawn = cli("--stage", session.path, "withdraw")
        assert withdrawn["withdrawn"] == 2 and withdrawn["inSync"]
        second = cli("--stage", session.path, "apply", "--host", host)
        assert second["edits"] == 0 and second["pending"] == 0 and not second["touched"]
        for layer in session.stage.GetLayerStack():
            layer.Reload()
        if host == "ifc":
            assert verify(str(session.path), "second")
        sessions[host] = session
    parity = (
        compare(
            sessions["ifc"].path.parent / "result.ifc.usda",
            sessions["bonsai"].path.parent / "result.bonsai.usda",
        )
        if bonsai
        else None
    )
    if parity:
        assert not parity["differences"], parity
    return {
        "first": "PASS: 3 applied, 2 retained",
        "second": "PASS: empty intent; no host mutation",
        "parity": parity,
    }
