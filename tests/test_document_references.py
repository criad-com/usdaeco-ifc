"""Document targets survive federation without capturing another package."""
import logging

import pytest
from pxr import Sdf, Usd

from document_fixtures import (FOREIGN_PORT, FOREIGN_SPACE, LOCAL_PORTS, SYSTEM,
                               associate, delivery)
from usdaeco_ifc.convert import convert_file


def convert(model, tmp_path, overlay):
    output = tmp_path / "delivery.usda"
    stats = convert_file(model, str(output), geometry=False, overlay_spine=overlay)
    return Usd.Stage.Open(str(output)), stats


def targets(stage, path, name):
    return list(map(str, stage.GetPrimAtPath(path).GetRelationship(name).GetTargets()))


@pytest.mark.parametrize("schema", ["IFC4", "IFC4X3"])
@pytest.mark.parametrize("overlay", [False, True])
def test_local_and_federated_port_targets(tmp_path, schema, overlay):
    model, _, _, _ = delivery(schema)
    stage, stats = convert(model, tmp_path, overlay)
    assert stats["ports"] == 2 and stats["portLinks"] == 1
    assert targets(stage, LOCAL_PORTS[0], "aeco:connectedPorts") == [LOCAL_PORTS[1], FOREIGN_PORT]
    assert targets(stage, LOCAL_PORTS[1], "aeco:connectedPorts") == [LOCAL_PORTS[0]]
    for layer in stage.GetLayerStack():
        assert layer.GetPrimAtPath(FOREIGN_PORT) is None
        assert layer.GetPrimAtPath("/Demo/Remote") is None
    spatial = stage.GetPrimAtPath("/Demo/Building").GetPrimStack()[0]
    assert spatial.specifier == (Sdf.SpecifierOver if overlay else Sdf.SpecifierDef)


@pytest.mark.parametrize("overlay", [False, True])
def test_serves_preserves_local_targets_and_ignores_duplicate_documents(tmp_path, overlay):
    model, ports, system, _ = delivery()
    associate(model, [system], "aeco:serves", FOREIGN_SPACE)
    associate(model, [system], "aeco:serves", FOREIGN_SPACE)
    associate(model, [ports[0]], "aeco:connectedPorts", LOCAL_PORTS[1])
    stage, _ = convert(model, tmp_path, overlay)
    assert targets(stage, SYSTEM, "aeco:serves") == ["/Demo/Building", FOREIGN_SPACE]
    assert targets(stage, LOCAL_PORTS[0], "aeco:connectedPorts") == [LOCAL_PORTS[1], FOREIGN_PORT]
    assert not stage.GetPrimAtPath(FOREIGN_SPACE)


@pytest.mark.parametrize("description", [None, "", "Port", "../Port", "/",
                                          "/Demo.port", "/Demo/Port.rel[/Target]",
                                          "/Demo{choice=A}/Port", "not a path"])
@pytest.mark.parametrize("overlay", [False, True])
def test_malformed_document_is_a_warning(tmp_path, caplog, description, overlay):
    model, _, _, document = delivery()
    document.Description = description
    with caplog.at_level(logging.WARNING, logger="usdaeco_ifc.convert.author"):
        stage, _ = convert(model, tmp_path, overlay)
    assert len(caplog.records) == 1
    assert "Description must be an absolute USD prim path" in caplog.text
    assert targets(stage, LOCAL_PORTS[0], "aeco:connectedPorts") == [LOCAL_PORTS[1]]


def test_unknown_names_document_information_and_wrong_owners_are_ignored(tmp_path, caplog):
    model, ports, system, document = delivery()
    document.Name = "unrecognized"
    document.Description = "not a path"
    associate(model, [system], "aeco:connectedPorts", "not a path")
    associate(model, ports, "aeco:serves", "not a path")
    association = model.by_type("IfcRelAssociatesDocument")[0]
    association.RelatingDocument = model.create_entity("IfcDocumentInformation",
                                                       Identification="manual",
                                                       Name="aeco:connectedPorts")
    associate(model, ports, "unrecognized", "not a path")
    stage, _ = convert(model, tmp_path, False)
    assert not caplog.records
    assert targets(stage, LOCAL_PORTS[0], "aeco:connectedPorts") == [LOCAL_PORTS[1]]
    assert not stage.GetPrimAtPath(SYSTEM).GetRelationship("aeco:connectedPorts")


def test_reciprocal_document_deliveries_compose_symmetrically(tmp_path):
    model, ports, _, document = delivery()
    # Both files contain the same spatial naming contract but own one port.
    local_link = model.by_type("IfcRelConnectsPorts")[0]
    model.remove(local_link)
    document.Description = LOCAL_PORTS[1]
    nest = model.by_type("IfcRelNests")[0]
    nest.RelatedObjects = [ports[0]]
    second_id = ports[1].GlobalId
    model.remove(ports[1])
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    first, _ = convert(model, first_dir, True)
    assert not first.GetPrimAtPath(LOCAL_PORTS[1])
    document.Description = LOCAL_PORTS[0]
    ports[0].Name = "Out"
    ports[0].GlobalId = second_id
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second, _ = convert(model, second_dir, True)
    root = Sdf.Layer.CreateAnonymous()
    root.subLayerPaths = [first.GetRootLayer().identifier, second.GetRootLayer().identifier]
    stage = Usd.Stage.Open(root)
    assert targets(stage, LOCAL_PORTS[0], "aeco:connectedPorts") == [LOCAL_PORTS[1]]
    assert targets(stage, LOCAL_PORTS[1], "aeco:connectedPorts") == [LOCAL_PORTS[0]]
