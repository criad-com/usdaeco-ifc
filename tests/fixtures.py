"""Small, generated IFC exchange fixtures; no external model is required."""
import math
import random
import uuid

import ifcopenshell
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.unit


def pset(model, entity, name, values):
    """Assign scalar or enumerated properties, including exporter UI headings."""
    props = []
    for key, value in values.items():
        if isinstance(value, list):
            prop = model.create_entity("IfcPropertyEnumeratedValue", Name=key,
                    EnumerationValues=[model.create_entity("IfcLabel", v) for v in value])
        else:
            kind = {bool: "IfcBoolean", int: "IfcInteger", float: "IfcReal", str: "IfcLabel"}
            prop = model.create_entity("IfcPropertySingleValue", Name=key,
                    NominalValue=model.create_entity(kind[type(value)], value) if value is not None else None)
        props.append(prop)
    uid = uuid.uuid5(uuid.NAMESPACE_URL, "usdaeco:fixture-pset:%s:%s" % (entity.GlobalId, name))
    item = model.create_entity("IfcPropertySet", GlobalId=ifcopenshell.guid.compress(uid.hex),
                               Name=name, HasProperties=props)
    if entity.is_a("IfcTypeObject"):
        entity.HasPropertySets = tuple(entity.HasPropertySets or ()) + (item,)
    else:
        model.create_entity("IfcRelDefinesByProperties",
                GlobalId=ifcopenshell.guid.compress(uuid.uuid5(uid, "rel").hex),
                RelatedObjects=[entity], RelatingPropertyDefinition=item)
    return item


def assign_type(model, entities, status="EXISTING"):
    uid = uuid.uuid5(uuid.NAMESPACE_URL, "usdaeco:fixture-type:" + entities[0].GlobalId)
    typ = model.create_entity("IfcFurnishingElementType", Name="FurnishingType",
                              GlobalId=ifcopenshell.guid.compress(uid.hex))
    model.create_entity("IfcRelDefinesByType",
            GlobalId=ifcopenshell.guid.compress(uuid.uuid5(uid, "rel").hex),
            RelatedObjects=entities, RelatingType=typ)
    pset(model, typ, "Pset_FurnishingElementTypeCommon", {"Status": status})
    return typ


def enumeration_fixture(schema="IFC4", mixed=False):
    """Common Status with scalar/enum opinions in both inheritance directions."""
    model, elements, _ = building(schema=schema)
    first = assign_type(model, elements[:3], "EXISTING")
    assign_type(model, elements[3:],
                ["EXISTING", "TEMPORARY"] if mixed else ["EXISTING"])
    common = "Pset_FurnishingElementTypeCommon"
    for entity, status in ((elements[0], ["NEW"]),
                           (elements[1], ["NEW", "TEMPORARY"] if mixed else "TEMPORARY"),
                           (elements[3], "DEMOLISH"), (elements[4], ["NEW"])):
        pset(model, entity, common, {"Status": status})
    pset(model, first, "Pset_Selections", {"Modes": ["Zulu", "Alpha"]})
    pset(model, elements[0], "Pset_Selections", {"Modes": ["Middle", "Zulu"]})
    item = pset(model, elements[0], "Pset_Lists", {"Enabled": False, "Zero": 0})
    item.HasProperties += (
        model.create_entity("IfcPropertyListValue", Name="Labels",
                            ListValues=[model.create_entity("IfcLabel", "Keep")]),
        model.create_entity("IfcPropertyListValue", Name="Samples",
                            ListValues=[model.create_entity("IfcReal", 42.)]),
        model.create_entity("IfcPropertyEnumeratedValue", Name="Empty"),
    )
    return model


def building(schema="IFC4", spaces=False, wrapped=False, millimetres=False):
    model = ifcopenshell.file(schema=schema)
    serial = 0

    def root(cls, name=None, **kwargs):
        nonlocal serial
        serial += 1
        uid = uuid.uuid5(uuid.NAMESPACE_URL, "usdaeco:converter-fixture:%d" % serial)
        return model.create_entity(cls, GlobalId=ifcopenshell.guid.compress(uid.hex),
                                   Name=name, **kwargs)

    scale = 1000.0 if millimetres else 1.0

    def placement(parent=None, xyz=(0., 0., 0.), angle=0.):
        angle = math.radians(angle)
        position = model.create_entity(
            "IfcAxis2Placement3D",
            Location=model.create_entity("IfcCartesianPoint", Coordinates=tuple(v * scale for v in xyz)),
            Axis=model.create_entity("IfcDirection", DirectionRatios=(0., 0., 1.)),
            RefDirection=model.create_entity("IfcDirection", DirectionRatios=(math.cos(angle), math.sin(angle), 0.)))
        return model.create_entity("IfcLocalPlacement", PlacementRelTo=parent,
                                   RelativePlacement=position)

    def aggregate(parent, children):
        root("IfcRelAggregates", RelatingObject=parent, RelatedObjects=children)

    project = root("IfcProject", "DemoProject")
    unit = ifcopenshell.api.unit.add_si_unit(model, unit_type="LENGTHUNIT",
                                           prefix="MILLI" if millimetres else None)
    ifcopenshell.api.unit.assign_unit(model, units=[unit])
    context = ifcopenshell.api.context.add_context(model, context_type="Model")
    body = ifcopenshell.api.context.add_context(model, context_type="Model",
            context_identifier="Body", target_view="MODEL_VIEW", parent=context)
    site = root("IfcSite", "Site", ObjectPlacement=placement(xyz=(10., 20., 0.5), angle=23.))
    facility = root("IfcBuilding", "Building",
                    ObjectPlacement=placement(site.ObjectPlacement, (2., 3., 0.5), -11.))
    aggregate(project, [site])
    if wrapped:
        wrapper = root("IfcSpatialZone", "UnmappedAggregate")
        aggregate(site, [wrapper])
        aggregate(wrapper, [facility])
    else:
        aggregate(site, [facility])
    levels = [root("IfcBuildingStorey", name,
                   ObjectPlacement=placement(facility.ObjectPlacement, (0., 0., float(i * 4))),
                   Elevation=float(i * 4) * scale)
              for i, name in enumerate(("Ground", "Upper", "Roof"))]
    aggregate(facility, levels)
    rooms = []
    if spaces:
        for i in range(30):
            level = levels[i // 15]
            room = root("IfcSpace", "Room_%02d" % i,
                        ObjectPlacement=placement(level.ObjectPlacement, (float(i % 15 * 5), 1., 0.), 7.))
            rooms.append(room)
            aggregate(level, [room])
    elements = []
    for i in range(5):
        container = rooms[i * 6] if rooms else levels[i % 2]
        element = root("IfcFurnishingElement", "Item_%d" % i,
                       ObjectPlacement=placement(container.ObjectPlacement, (1.25, 2.5, 0.75), 31.))
        root("IfcRelContainedInSpatialStructure", RelatingStructure=container,
             RelatedElements=[element])
        elements.append(element)

    def add_body(product, length=2., height=1.):
        rep = ifcopenshell.api.geometry.add_wall_representation(
            model, context=body, length=length, height=height, thickness=1.)
        ifcopenshell.api.geometry.assign_representation(model, product=product, representation=rep)

    add_body(elements[0])
    # All remaining spaces deliberately have no representation.
    for room in rooms[:2]:
        add_body(room, length=4., height=3.)
    model.header.file_name.name = "fixture.ifc"
    model.header.file_name.time_stamp = "2026-01-01T00:00:00"
    model.header.file_name.author = ()
    model.header.file_name.organization = ()
    model.header.file_name.preprocessor_version = "fixture generator"
    model.header.file_name.originating_system = "fixture generator"
    return model, elements, rooms


def ordering_fixture(seed):
    """Independent equivalent models with shuffled creation order and STEP ids.

    GlobalIds and every ordered attribute stay fixed. Recreate *all* entities
    in two passes so references can point forward in the shuffled file.
    """
    model, elements, rooms = building(schema="IFC4X3", spaces=True)

    def root(cls, key, **kwargs):
        uid = uuid.uuid5(uuid.NAMESPACE_URL, "usdaeco:ordering-fixture:" + key)
        return model.create_entity(cls, GlobalId=ifcopenshell.guid.compress(uid.hex), **kwargs)

    # Sibling name collisions exercise both ordering and UUID suffixes.
    rooms[0].Name = rooms[1].Name = "Shared Room"
    for i, element in enumerate(elements):
        if i < 3:
            element.Name = "Shared Item"
            element.ContainedInStructure[0].RelatingStructure = rooms[0]
        element.Representation = elements[0].Representation
    for i in range(3):
        typ = root("IfcFurnishingElementType", "type:%d" % i, Name="Shared Type")
        root("IfcRelDefinesByType", "typing:%d" % i,
             RelatingType=typ, RelatedObjects=elements[i::3])
        pset(model, typ, "Pset_FurnishingElementTypeCommon", {"Status": "NEW"})
    groups = [root("IfcSystem", "system:%d" % i, Name="Shared System") for i in range(3)]
    # Deliberately nonalphabetical target order, including a forward group.
    for i, group in enumerate(groups):
        members = [elements[4], elements[1], elements[0]]
        if i == 0:
            members.append(groups[2])
        root("IfcRelAssignsToGroup", "membership:%d" % i,
             RelatingGroup=group, RelatedObjects=members)
        root("IfcRelServicesBuildings", "serves:%d" % i,
             RelatingSystem=group, RelatedBuildings=[rooms[2], rooms[0]])
    root("IfcRelReferencedInSpatialStructure", "reference",
         RelatingStructure=rooms[4], RelatedElements=[elements[0], elements[2]])
    ports = [root("IfcDistributionPort", "port:%d" % i, Name="Shared Port",
                  PredefinedType="PIPE", FlowDirection="SOURCEANDSINK",
                  ObjectPlacement=elements[0].ObjectPlacement) for i in range(3)]
    root("IfcRelNests", "ports", RelatingObject=elements[0],
         RelatedObjects=[ports[2], ports[0], ports[1]])
    root("IfcRelConnectsPorts", "connection", RelatingPort=ports[2], RelatedPort=ports[0])
    pset(model, elements[0], "Pset_Ordered", {"Tour": ["Zulu", "Alpha", "Middle"],
                                            "Samples": ["third", "first", "second"]})

    entities = list(model)
    random.Random(seed).shuffle(entities)
    shuffled = ifcopenshell.file(schema=model.schema)
    for key in ("name", "time_stamp", "author", "organization", "preprocessor_version",
                "originating_system", "authorization"):
        setattr(shuffled.header.file_name, key, getattr(model.header.file_name, key))
    copies = {entity.id(): shuffled.create_entity(entity.is_a(), id=1000 + i * 3)
              for i, entity in enumerate(entities)}

    def copy_value(value):
        if isinstance(value, ifcopenshell.entity_instance):
            if value.id():
                return copies[value.id()]
            return shuffled.create_entity(value.is_a(), *[copy_value(v) for v in value])
        if isinstance(value, tuple):
            return tuple(copy_value(v) for v in value)
        return value

    for entity in entities:
        for i, value in enumerate(entity):
            copies[entity.id()][i] = copy_value(value)
    return shuffled
