"""Synthetic federated delivery: two local ports and a remote endpoint."""
import uuid

import ifcopenshell


FOREIGN_PORT = "/Demo/Remote/Port"
FOREIGN_SPACE = "/Demo/RemoteSpace"
LOCAL_PORTS = ("/Demo/Building/Unit/In", "/Demo/Building/Unit/Out")
SYSTEM = "/Demo/Systems/Cooling"


def delivery(schema="IFC4X3"):
    model = ifcopenshell.file(schema=schema)

    def root(kind, name, **kwargs):
        uid = uuid.uuid5(uuid.NAMESPACE_URL, "usdaeco:document-fixture:" + name)
        return model.create_entity(kind, Name=name,
                                  GlobalId=ifcopenshell.guid.compress(uid.hex), **kwargs)

    root("IfcProject", "Demo")
    unit = root("IfcPump", "Unit")
    ports = [root("IfcDistributionPort", name, FlowDirection="SOURCEANDSINK")
             for name in ("In", "Out")]
    root("IfcRelNests", "Ports", RelatingObject=unit, RelatedObjects=ports)
    root("IfcRelConnectsPorts", "LocalLink", RelatingPort=ports[0], RelatedPort=ports[1])
    system = root("IfcDistributionSystem", "Cooling")
    space = root("IfcBuilding", "Building")
    root("IfcRelAggregates", "Spatial", RelatingObject=model.by_type("IfcProject")[0],
         RelatedObjects=[space])
    root("IfcRelContainedInSpatialStructure", "Contents", RelatingStructure=space,
         RelatedElements=[unit])
    root("IfcRelServicesBuildings", "Serves", RelatingSystem=system, RelatedBuildings=[space])
    document = associate(model, [ports[0]], "aeco:connectedPorts", FOREIGN_PORT)
    return model, ports, system, document


def associate(model, owners, name, description):
    document = model.create_entity("IfcDocumentReference", Name=name,
                                   Location="remote.ifc",
                                   Identification=ifcopenshell.guid.compress(
                                       uuid.uuid5(uuid.NAMESPACE_URL, "usdaeco:remote:" + name).hex),
                                   Description=description)
    uid = uuid.uuid5(uuid.NAMESPACE_URL, "usdaeco:association:" + str(document.id()))
    model.create_entity("IfcRelAssociatesDocument", GlobalId=ifcopenshell.guid.compress(uid.hex),
                        RelatedObjects=owners, RelatingDocument=document)
    return document
