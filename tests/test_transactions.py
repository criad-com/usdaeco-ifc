from pathlib import Path
import pytest
from pxr import Gf, Usd
from scenarios.baseline import build
from scenarios.s4_intent import elements
from usdaeco_ifc.convert import convert
from usdaeco_ifc.kind_import import main as import_kinds
from aeco_sync import engine
from aeco_sync.edits import author
from aeco_sync.stack import digest
from usdaeco_ifc.host import IfcHost


@pytest.fixture
def native_session(tmp_path):
    build(str(tmp_path / "baseline.ifc"))
    convert(str(tmp_path / "baseline.ifc"), str(tmp_path / "model.usda"))
    return import_kinds(
        str(tmp_path / "baseline.ifc"), str(tmp_path / "model.usda"), str(tmp_path)
    )


def test_bad_size_never_calls_apply(native_session, monkeypatch):
    pipe = elements(native_session.stage)["Pipe 1"]
    author(native_session, str(pipe.GetPath()), ["diameter=.033"])

    def forbidden(*args):
        pytest.fail("Preflight refusal must not enter the host transaction")

    monkeypatch.setattr(IfcHost, "apply", forbidden)
    result = engine.apply(native_session)
    assert result["accepted"] == 0 and result["pending"] == 1
    assert [d["code"] for d in result["diagnostics"]] == ["pipeSizeNotInTable"]


def test_native_failure_restores_layers_and_retains_intent(native_session, monkeypatch):
    pipe = elements(native_session.stage)["Pipe 1"]
    author(native_session, str(pipe.GetPath()), ["length=2.5"])
    before = digest(native_session.document("ifc"))
    result_before = native_session.layer("result.ifc.usda").ExportToString()

    def fail(*args):
        raise RuntimeError("forced geometry failure")

    monkeypatch.setattr(IfcHost, "readback", fail)
    result = engine.apply(native_session)
    assert result["accepted"] == 0 and result["pending"] == 1
    assert digest(native_session.document("ifc")) == before
    assert native_session.layer("result.ifc.usda").ExportToString() == result_before
    assert not (native_session.path.parent / "host.ifc.ifc").exists()
    assert result["diagnostics"][-1]["code"] == "ifc:exception:RuntimeError"


def test_native_conflict_and_stale_apply(native_session):
    import ifcopenshell
    from usdaeco_ifc.host import set_pipe_depth_and_ports

    pipe = elements(native_session.stage)["Pipe 2"]
    path = str(pipe.GetPath())
    author(native_session, path, ["length=2"])
    document = native_session.path.parent / "external.ifc"
    host = IfcHost(native_session)
    entity = host.f.by_guid(pipe.GetAttribute("aeco:host:ifc:ref").Get())
    set_pipe_depth_and_ports(host.f, entity, 2.2)
    host.f.write(str(document))
    result = engine.readback(native_session, "ifc", document)
    assert "sync:conflict" in [d["code"] for d in result["diagnostics"]]
    current = native_session.current()
    assert current.GetAttributeAtPath(path + ".aeco:axis:end").Get()[
        2
    ] == pytest.approx(2.2)
    assert native_session.status()["pending"] == 1
    refused = engine.apply(native_session)
    assert refused["accepted"] == 0 and refused["pending"] == 1
    assert any(
        d["code"] in ("sync:staleIntent", "sync:conflict")
        for d in refused["diagnostics"]
    )


def test_disconnect_relationship_reaches_native_and_readback(native_session):
    stage = native_session.stage
    ports = [
        p
        for p in stage.Traverse()
        if p.GetTypeName() == "AecoPort"
        and p.GetRelationship("aeco:connectedPorts").GetTargets()
    ]
    native_session.capture_base()
    with Usd.EditContext(stage, native_session.intent):
        ports[0].GetRelationship("aeco:connectedPorts").SetTargets([])
    result = engine.apply(native_session)
    assert result["accepted"] == 1 and result["pending"] == 0
    current = native_session.current()
    assert all(
        not current.GetPrimAtPath(p.GetPath())
        .GetRelationship("aeco:connectedPorts")
        .GetTargets()
        for p in ports
    )


def test_deactivation_deletes_native_product(native_session):
    stage = native_session.stage
    wall = elements(stage)["Wall B"]
    ref = wall.GetAttribute("aeco:host:ifc:ref").Get()
    native_session.capture_base()
    with Usd.EditContext(stage, native_session.intent):
        wall.SetActive(False)
    result = engine.apply(native_session)
    assert result["accepted"] == 1 and result["pending"] == 0, result
    native = IfcHost(native_session)
    with pytest.raises(RuntimeError):
        native.f.by_guid(ref)
    assert not stage.GetPrimAtPath(wall.GetPath()).IsActive()


def test_external_native_deletion_masks_import(native_session):
    from ifcopenshell.api import run

    pipe = elements(native_session.stage)["Pipe 2"]
    path = pipe.GetPath()
    host = IfcHost(native_session)
    run(
        "root.remove_product",
        host.f,
        product=host.f.by_guid(pipe.GetAttribute("aeco:host:ifc:ref").Get()),
    )
    document = native_session.path.parent / "external-deleted.ifc"
    host.f.write(str(document))
    engine.readback(native_session, "ifc", document)
    assert not native_session.stage.GetPrimAtPath(path).IsActive()


def test_switching_new_host_requires_readback(native_session):
    pipe = elements(native_session.stage)["Pipe 1"]
    author(native_session, str(pipe.GetPath()), ["length=2.5"])
    assert engine.apply(native_session)["accepted"] == 1
    with pytest.raises(ValueError, match="Read back"):
        engine.apply(native_session, "bonsai")
