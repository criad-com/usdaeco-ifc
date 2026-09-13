"""Authoring passes: spatial structure -> types -> elements -> groups ->
ports -> geometry. Imports pxr, so this module loads only after the
geometry pass has run (see package docstring).

Core output only: every prim, property and relationship written here is
defined by usdAeco itself. A downstream element-kind or record library
would add its applied schemas in a further pass (or a further layer)
without touching anything below.

Layer layout: <out>.usda roots the stage and sublayers
<out>.semantics.usda (spatial structure, elements, applied schemas,
groups, ports — small, diffable) and <out>.geometry.usdc (meshes —
binary).
"""
import logging
import os
import uuid as _uuid

import ifcopenshell.guid
import ifcopenshell.util.element as _ue
import ifcopenshell.util.placement as _up

from . import mapping
from .naming import Namer, sanitize


def guid_to_uuid(globalid):
    """IFC GlobalId (22-char base64) -> lowercase hyphenated UUID: the
    lossless decode that becomes aeco:id (docs/05-ifc-mapping.md)."""
    return str(_uuid.UUID(hex=ifcopenshell.guid.expand(globalid)))


def _entity_key(entity):
    """Declaration order follows identity, never STEP ids or inverse sets."""
    return (guid_to_uuid(entity.GlobalId), entity.Name or "", entity.is_a())


def _gf_from_np(m):
    from pxr import Gf
    # numpy placement is column-vector convention; USD rows are its transpose
    return Gf.Matrix4d(*(float(m[c][r]) for r in range(4) for c in range(4)))


def _gf_from_flat16(m):
    from pxr import Gf
    return Gf.Matrix4d(*(float(x) for x in m))


_IDENTITY16 = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
               0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def _set_transform(prim, gf_matrix):
    from pxr import UsdGeom
    if gf_matrix == _gf_from_flat16(_IDENTITY16):
        return
    UsdGeom.Xformable(prim).AddTransformOp().Set(gf_matrix)


def _enumeration_array_properties(ifc):
    """Choose array cardinality once for each type and its occurrences.

    A singleton selection normally becomes a scalar. If any member of
    an inheritance family selects several values for the same property,
    all its opinions must instead use arrays to remain composable.
    Keys are original Pset/property names, before USD name sanitization.
    """
    if not any(len(p.EnumerationValues or ()) > 1
               for p in ifc.by_type("IfcPropertyEnumeratedValue")):
        return {}
    families = {}
    for rel in ifc.by_type("IfcRelDefinesByType"):
        family = families.setdefault(rel.RelatingType.id(), {rel.RelatingType})
        family.update(rel.RelatedObjects)
    result = {}
    for family in families.values():
        arrays = set()
        for entity in family:
            for pset_name, props in _ue.get_psets(
                    entity, should_inherit=False, verbose=True).items():
                for key, prop in props.items():
                    if (isinstance(prop, dict)
                            and prop.get("class") == "IfcPropertyEnumeratedValue"
                            and len(prop.get("value") or ()) > 1):
                        arrays.add((pset_name, key))
        if arrays:
            for entity in family:
                result[entity.id()] = arrays
    return result


def _author_props(prim, entity, array_props=()):
    """Psets + quantities -> quarantine; return omitted blank headings.

    Empty string values are exporter UI headings, not data. Drop them
    before sanitization so a heading cannot overwrite a typed property.
    Nonblank sanitized-name collisions receive deterministic suffixes.
    """
    from pxr import Sdf
    try:
        psets = _ue.get_psets(entity, verbose=True)
    except Exception:
        return 0
    omitted = 0
    taken = set()
    for pset_name in sorted(psets):
        props = psets[pset_name]
        for key in sorted(props):
            if key == "id":
                continue
            prop = props[key]
            # Verbose extraction distinguishes a single enum selection
            # from an ordinary IFC list (which must remain an array).
            # Predefined property sets can still return unwrapped values.
            value = prop.get("value") if isinstance(prop, dict) else prop
            if (isinstance(prop, dict)
                    and prop.get("class") == "IfcPropertyEnumeratedValue"
                    and value is not None and len(value) == 1):
                value = value[0]
            if not key.strip() or (isinstance(value, str) and not value.strip()):
                omitted += 1
                continue
            if ((pset_name, key) in array_props
                    and isinstance(value, (bool, int, float, str))):
                value = [value]
            attr_name = "aeco:props:%s:%s" % (sanitize(pset_name),
                                              sanitize(key))
            if isinstance(value, bool):
                tn = Sdf.ValueTypeNames.Bool
            elif isinstance(value, int):
                tn = Sdf.ValueTypeNames.Int
            elif isinstance(value, float):
                tn = Sdf.ValueTypeNames.Double
            elif isinstance(value, str):
                tn = Sdf.ValueTypeNames.String
            elif (isinstance(value, (list, tuple)) and value
                  and all(isinstance(v, (int, float)) for v in value)):
                tn, value = Sdf.ValueTypeNames.DoubleArray, list(
                    float(v) for v in value)
            elif (isinstance(value, (list, tuple)) and value
                  and all(isinstance(v, str) for v in value)):
                tn, value = Sdf.ValueTypeNames.StringArray, list(value)
            else:
                continue
            # Also preserve two genuine values whose Pset/property names
            # sanitize alike, including collisions across property sets.
            base_name = attr_name
            if attr_name in taken:
                suffix = _uuid.uuid5(_uuid.NAMESPACE_URL, pset_name + ":" + key).hex[:6]
                attr_name = base_name + "_" + suffix
                n = 2
                while attr_name in taken:
                    attr_name = base_name + "_" + suffix + "_" + str(n)
                    n += 1
            taken.add(attr_name)
            prim.CreateAttribute(attr_name, tn, custom=True).Set(value)
    return omitted


def _author_axis(prim, entity, unit):
    """v0.7: the driving axis of a path-based element, in the prim's local
    space (the placement is the prim transform). Walls: the 'Axis'
    representation's reference line. Flow segments, beams, columns,
    members: the base extrusion's start and direction * depth. Length is
    derived: the host (here: the file) reports it."""
    from pxr import Gf
    start = end = None
    try:
        if entity.is_a("IfcWall"):
            import ifcopenshell.util.representation as _urep
            p1, p2 = _urep.get_reference_line(entity)
            start = (float(p1[0]) * unit, float(p1[1]) * unit, 0.0)
            end = (float(p2[0]) * unit, float(p2[1]) * unit, 0.0)
        elif entity.is_a("IfcFlowSegment") or entity.is_a("IfcBeam") \
                or entity.is_a("IfcColumn") or entity.is_a("IfcMember"):
            import ifcopenshell.util.shape as _ush
            ex = (_ush.get_base_extrusions(entity) or [None])[0]
            if ex is not None:
                pos = (0.0, 0.0, 0.0)
                if ex.Position is not None and ex.Position.Location is not None:
                    c = ex.Position.Location.Coordinates
                    pos = (float(c[0]), float(c[1]), float(c[2]) if len(c) > 2 else 0.0)
                d = ex.ExtrudedDirection.DirectionRatios
                depth = float(ex.Depth)
                start = tuple(v * unit for v in pos)
                end = tuple((pos[i] + float(d[i]) * depth) * unit for i in range(3))
    except Exception:
        return
    if start is None or end is None:
        return
    prim.ApplyAPI("AecoAxisAPI")
    prim.GetAttribute("aeco:axis:start").Set(Gf.Vec3d(*start))
    prim.GetAttribute("aeco:axis:end").Set(Gf.Vec3d(*end))
    prim.GetAttribute("aeco:axis:length").Set(
        float(sum((end[i] - start[i]) ** 2 for i in range(3)) ** 0.5))


def _classify_ifc(prim, code, name=None):
    prim.ApplyAPI("AecoClassificationAPI", "ifc")
    prim.GetAttribute("aeco:class:ifc:code").Set(code)
    if name:
        prim.GetAttribute("aeco:class:ifc:name").Set(name)


# ---------------------------------------------------------------------------

def author(ifc, geo, out_path, overlay_spine=False):
    from usdaeco_ifc import register_plugins
    register_plugins()
    from pxr import Usd, UsdGeom, Sdf, Gf, Kind, Vt

    base, _ = os.path.splitext(out_path)
    sem_path = base + ".semantics.usda"
    geo_path = base + ".geometry.usdc"

    # Re-export safe: in a long-running process the output identifiers
    # stay REGISTERED in the Sdf layer registry across exports, so
    # CreateNew would fail ("a layer already exists"). Reuse and Clear the
    # registered layer if present; else make a fresh one.
    def _fresh(path):
        lyr = Sdf.Layer.Find(path)
        if lyr is not None:
            lyr.Clear()
            return lyr
        if os.path.exists(path):
            os.remove(path)
        return Sdf.Layer.CreateNew(path)

    root_layer = _fresh(out_path)
    sem_layer = _fresh(sem_path)
    geo_layer = _fresh(geo_path)
    root_layer.subLayerPaths.clear()
    root_layer.subLayerPaths.append(os.path.basename(sem_path))
    root_layer.subLayerPaths.append(os.path.basename(geo_path))

    stage = Usd.Stage.Open(root_layer)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    stage.SetEditTarget(Usd.EditTarget(sem_layer))

    # Raw IFC placements are in the project's length unit; the geometry
    # pass already returns metres, so only values read straight from
    # ObjectPlacement (level elevation, placement fallbacks, port offsets)
    # need scaling.
    try:
        import ifcopenshell.util.unit as _uun
        unit = float(_uun.calculate_unit_scale(ifc))
    except Exception:
        unit = 1.0

    def _scaled(m):
        m = m.copy()
        for r in range(3):
            m[r][3] *= unit
        return m

    namer = Namer()
    stats = {"spatial": 0, "elements": 0, "types": 0, "systems": 0,
             "zones": 0, "ports": 0, "portLinks": 0, "meshes": 0,
             "unparented": 0, "unclassified": 0, "phases": 0,
             "extents": 0, "blankHeadingsOmitted": 0}
    array_props = _enumeration_array_properties(ifc)

    def define(path, type_name):
        if overlay_spine:
            prim = stage.OverridePrim(path)
        else:
            prim = stage.DefinePrim(path, type_name)
        return prim

    # ---- spatial structure ----------------------------------------------
    project = ifc.by_type("IfcProject")[0]
    root_name = sanitize(project.Name, "Project")
    root_path = Sdf.Path("/" + root_name)
    root = define(root_path, "Xform")
    if not overlay_spine:
        Usd.ModelAPI(root).SetKind(Kind.Tokens.assembly)
        root.ApplyAPI("AecoProjectAPI")
        root.GetAttribute("aeco:project:name").Set(
            project.LongName or project.Name or "")
        root.GetAttribute("aeco:project:id").Set(
            guid_to_uuid(project.GlobalId))
    stage.SetDefaultPrim(root)

    spatial_paths = {root_path}
    path_by_entity = {}     # ifc entity id -> Sdf.Path

    def spatial_type(entity):
        if entity.is_a("IfcSite"):
            return "AecoSite"
        if entity.is_a("IfcBuildingStorey"):
            return "AecoLevel"
        if entity.is_a("IfcFacilityPart"):
            return "AecoFacilityPart"
        # IFC4 buildings predate the IfcFacility superclass in IFC4X3.
        if entity.is_a("IfcBuilding") or entity.is_a("IfcFacility"):
            return "AecoFacility"
        if entity.is_a("IfcSpace"):
            return "AecoSpace"
        return None

    def walk_spatial(entity, parent_path):
        stype = spatial_type(entity)
        if stype:
            uid = guid_to_uuid(entity.GlobalId)
            name = namer.child(parent_path, entity.Name, uid,
                               entity.is_a())
            path = parent_path.AppendChild(name)
            prim = define(path, stype)
            path_by_entity[entity.id()] = path
            spatial_paths.add(path)
            stats["spatial"] += 1
            if not overlay_spine:
                prim.GetAttribute("aeco:id").Set(uid)
                _classify_ifc(prim, mapping.ifc_code(entity),
                              getattr(entity, "LongName", None)
                              or entity.Name)
                if stype == "AecoLevel":
                    world = _up.get_local_placement(entity.ObjectPlacement)
                    prim.GetAttribute("aeco:elevation").Set(
                        float(world[2][3]) * unit)
                stats["blankHeadingsOmitted"] += _author_props(
                    prim, entity, array_props.get(entity.id(), ()))
        else:
            path = parent_path
        # An unmapped aggregate contributes no prim, but must not hide
        # mapped descendants. Empty storeys are levels too, including roofs.
        children = [child for rel in getattr(entity, "IsDecomposedBy", ()) or ()
                    for child in rel.RelatedObjects]
        for child in sorted(children, key=_entity_key):
            walk_spatial(child, path)

    walk_spatial(project, root_path)

    # ---- catalog types ----------------------------------------------------
    type_path_by_id = {}
    catalog_root = None
    types = {rel.RelatingType.id(): rel.RelatingType
             for rel in ifc.by_type("IfcRelDefinesByType")}
    for t in sorted(types.values(), key=_entity_key):
        if catalog_root is None:
            catalog_root = stage.CreateClassPrim(
                root_path.AppendChild("_TypeCatalog"))
        name = namer.child(catalog_root.GetPath(), t.Name, guid_to_uuid(t.GlobalId),
                           t.is_a())
        tprim = stage.CreateClassPrim(
            catalog_root.GetPath().AppendChild(name))
        tprim.ApplyAPI("AecoTypeAPI")
        tprim.GetAttribute("aeco:type:model").Set(t.Name or "")
        _classify_ifc(tprim, mapping.ifc_code(t))
        stats["blankHeadingsOmitted"] += _author_props(
            tprim, t, array_props.get(t.id(), ()))
        man = None
        try:
            psets = _ue.get_psets(t)
            man = (psets.get("Pset_ManufacturerTypeInformation") or
                   {}).get("Manufacturer")
        except Exception:
            pass
        if man:
            tprim.GetAttribute("aeco:type:manufacturer").Set(str(man))
        type_path_by_id[t.id()] = tprim.GetPath()
        stats["types"] += 1

    occurrence_type = {}    # element entity id -> type path
    for rel in ifc.by_type("IfcRelDefinesByType"):
        tpath = type_path_by_id.get(rel.RelatingType.id())
        if tpath:
            for obj in rel.RelatedObjects:
                occurrence_type[obj.id()] = tpath

    # ---- elements ---------------------------------------------------------
    container_of = {}       # element entity id -> spatial path
    for rel in ifc.by_type("IfcRelContainedInSpatialStructure"):
        spath = path_by_entity.get(rel.RelatingStructure.id())
        if spath is None:
            continue
        for e in rel.RelatedElements:
            container_of[e.id()] = spath

    referenced = {}         # element entity id -> [spatial paths]
    for rel in ifc.by_type("IfcRelReferencedInSpatialStructure"):
        spath = path_by_entity.get(rel.RelatingStructure.id())
        if spath is None:
            continue
        for e in rel.RelatedElements:
            referenced.setdefault(e.id(), []).append(spath)

    elem_world = {}         # element entity id -> numpy world matrix
    for entity in sorted(ifc.by_type("IfcElement"), key=_entity_key):
        if entity.is_a("IfcOpeningElement") or entity.is_a("IfcVirtualElement"):
            continue
        uid = guid_to_uuid(entity.GlobalId)
        parent = container_of.get(entity.id())
        if parent is None:
            parent = root_path
            stats["unparented"] += 1
        name = namer.child(parent, entity.Name, uid, entity.is_a())
        path = parent.AppendChild(name)
        prim = stage.DefinePrim(path, "Xform")
        path_by_entity[entity.id()] = path
        stats["elements"] += 1

        entry = geo.get(entity.GlobalId)
        if entry is not None:
            gf = _gf_from_flat16(entry["matrix"])
        else:
            try:
                world = _up.get_local_placement(entity.ObjectPlacement)
                gf = _gf_from_np(_scaled(world))
            except Exception:
                gf = None
        if gf is not None:
            _set_transform(prim, gf)
            try:
                elem_world[entity.id()] = _up.get_local_placement(
                    entity.ObjectPlacement)
            except Exception:
                pass

        prim.ApplyAPI("AecoElementAPI")
        prim.GetAttribute("aeco:id").Set(uid)
        phase = mapping.phase(entity)
        if phase is not None:
            prim.GetAttribute("aeco:phase").Set(phase)
            stats["phases"] += 1
        # Kind IS the IFC class (+ PredefinedType). Proxies are carried
        # verbatim — the validator's proxyClassified warning, not this
        # converter, is where the health metric is reported.
        code = mapping.ifc_code(entity)
        _classify_ifc(prim, code, entity.Name)
        if code.startswith("IfcBuildingElementProxy"):
            stats["unclassified"] += 1
        stats["blankHeadingsOmitted"] += _author_props(
            prim, entity, array_props.get(entity.id(), ()))
        _author_axis(prim, entity, unit)

        tpath = occurrence_type.get(entity.id())
        if tpath:
            prim.GetInherits().AddInherit(tpath)
        for spath in referenced.get(entity.id(), []):
            prim.GetRelationship("aeco:referencedContainers").AddTarget(spath)

    # ---- groups: systems / zones ----------------------------------------
    systems_root = zones_root = None
    groups = sorted(ifc.by_type("IfcSystem"), key=_entity_key)
    for group in groups:
        if group.is_a("IfcStructuralAnalysisModel"):
            continue
        is_zone = group.is_a("IfcZone")
        if is_zone:
            if zones_root is None:
                zones_root = stage.DefinePrim(
                    root_path.AppendChild("Zones"), "Scope")
            parent = zones_root.GetPath()
            type_name = "AecoZone"
        else:
            if systems_root is None:
                systems_root = stage.DefinePrim(
                    root_path.AppendChild("Systems"), "Scope")
            parent = systems_root.GetPath()
            type_name = "AecoSystem"
        uid = guid_to_uuid(group.GlobalId)
        name = namer.child(parent, group.Name, uid, group.is_a())
        prim = stage.DefinePrim(parent.AppendChild(name), type_name)
        path_by_entity[group.id()] = prim.GetPath()
        stats["zones" if is_zone else "systems"] += 1
        prim.GetAttribute("aeco:id").Set(uid)
        if group.Name:
            prim.SetDisplayName(group.Name)
        _classify_ifc(prim, mapping.ifc_code(group),
                      getattr(group, "LongName", None) or group.Name)

    # Resolve memberships after all groups exist, including group targets.
    # Keep source target sequences; only declarations above are sorted.
    for group in groups:
        gpath = path_by_entity.get(group.id())
        if gpath is None:
            continue
        prim = stage.GetPrimAtPath(gpath)
        members = Usd.CollectionAPI(prim, "members").GetIncludesRel()
        for rel in group.IsGroupedBy or []:
            for obj in rel.RelatedObjects:
                mpath = path_by_entity.get(obj.id())
                if mpath is not None:
                    members.AddTarget(mpath)
        for rel in getattr(group, "ServicesBuildings", None) or []:
            for b in rel.RelatedBuildings:
                bpath = path_by_entity.get(b.id())
                if bpath is not None:
                    prim.GetRelationship("aeco:serves").AddTarget(bpath)

    # ---- ports ------------------------------------------------------------
    import numpy as _np
    port_path_by_id = {}
    for port in sorted(ifc.by_type("IfcDistributionPort"), key=_entity_key):
        host = None
        for rel in port.Nests or []:
            host = rel.RelatingObject
            break
        hpath = path_by_entity.get(host.id()) if host is not None else None
        if hpath is None:
            continue
        uid = guid_to_uuid(port.GlobalId)
        name = namer.child(hpath, port.Name, uid, "Port")
        prim = stage.DefinePrim(hpath.AppendChild(name), "AecoPort")
        port_path_by_id[port.id()] = prim.GetPath()
        stats["ports"] += 1
        prim.GetAttribute("aeco:id").Set(uid)
        ptype = mapping.predefined_type(port)
        medium = mapping.MEDIUM.get(ptype or "")
        if medium:
            prim.GetAttribute("aeco:medium").Set(medium)
        flow = mapping.FLOW.get(str(port.FlowDirection or ""))
        if flow:
            prim.GetAttribute("aeco:flowDirection").Set(flow)
        try:
            pworld = _up.get_local_placement(port.ObjectPlacement)
            eworld = elem_world.get(host.id())
            local = (_np.linalg.inv(eworld) @ pworld
                     if eworld is not None else pworld)
            _set_transform(prim, _gf_from_np(_scaled(local)))
        except Exception:
            pass

    for rel in ifc.by_type("IfcRelConnectsPorts"):
        a = port_path_by_id.get(rel.RelatingPort.id())
        b = port_path_by_id.get(rel.RelatedPort.id())
        if a is None or b is None:
            continue
        stage.GetPrimAtPath(a).GetRelationship(
            "aeco:connectedPorts").AddTarget(b)
        stage.GetPrimAtPath(b).GetRelationship(
            "aeco:connectedPorts").AddTarget(a)
        stats["portLinks"] += 1

    # Federated deliveries carry the other package's target path as data.
    # Only the local endpoint owns an opinion; never define the foreign prim
    # or infer its reciprocal link from a basename or GlobalId.
    relationship_types = {"aeco:connectedPorts": "AecoPort",
                          "aeco:serves": "AecoSystem"}
    for association in ifc.by_type("IfcRelAssociatesDocument"):
        document = association.RelatingDocument
        if not document.is_a("IfcDocumentReference"):
            continue
        name = document.Name
        if name not in relationship_types:
            continue
        owners = []
        for entity in association.RelatedObjects:
            path = port_path_by_id.get(entity.id()) or path_by_entity.get(entity.id())
            prim = stage.GetPrimAtPath(path) if path is not None else None
            if prim and prim.GetTypeName() == relationship_types[name]:
                owners.append(prim)
        if not owners:
            continue
        description = document.Description or ""
        target = (Sdf.Path(description) if description and
                  Sdf.Path.IsValidPathString(description) else Sdf.Path.emptyPath)
        if (not target.IsAbsolutePath() or not target.IsPrimPath()
                or target.ContainsPrimVariantSelection()):
            logging.getLogger(__name__).warning(
                "IfcDocumentReference #%s (%s): Description must be an absolute "
                "USD prim path; ignored %r", document.id(), name, description)
            continue
        for prim in owners:
            relationship = prim.GetRelationship(name)
            if target not in relationship.GetTargets():
                relationship.AddTarget(target)

    # ---- geometry ---------------------------------------------------------
    stage.SetEditTarget(Usd.EditTarget(geo_layer))
    guid_to_path = {}
    for entity in ifc.by_type("IfcElement"):
        p = path_by_entity.get(entity.id())
        if p is not None:
            guid_to_path[entity.GlobalId] = p
    space_guids = set()
    for entity in ifc.by_type("IfcSpace"):
        p = path_by_entity.get(entity.id())
        if p is not None:
            guid_to_path[entity.GlobalId] = p
            space_guids.add(entity.GlobalId)
    # The threaded tessellator's completion order is not declaration order.
    # Leave each mesh's point/face arrays and transform untouched.
    for guid in sorted(geo, key=guid_to_uuid):
        entry = geo[guid]
        epath = guid_to_path.get(guid)
        if epath is None or not entry["verts"]:
            continue
        is_extent = guid in space_guids
        if overlay_spine and is_extent:
            continue  # The shared spatial package owns space extents.
        mesh = UsdGeom.Mesh.Define(stage, epath.AppendChild("Extent" if is_extent else "Geom"))
        if is_extent:
            # Spatial prims have identity transforms. Place only the
            # extent, so contained elements retain their world placement.
            _set_transform(mesh.GetPrim(), _gf_from_flat16(entry["matrix"]))
            mesh.CreatePurposeAttr().Set(UsdGeom.Tokens.guide)
        verts = entry["verts"]
        pts = Vt.Vec3fArray(len(verts) // 3)
        for i in range(len(pts)):
            pts[i] = Gf.Vec3f(verts[3 * i], verts[3 * i + 1],
                              verts[3 * i + 2])
        mesh.GetPointsAttr().Set(pts)
        faces = entry["faces"]
        mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray(faces))
        mesh.GetFaceVertexCountsAttr().Set(
            Vt.IntArray([3] * (len(faces) // 3)))
        mesh.GetDoubleSidedAttr().Set(True)
        lo = Gf.Vec3f(min(verts[0::3]), min(verts[1::3]), min(verts[2::3]))
        hi = Gf.Vec3f(max(verts[0::3]), max(verts[1::3]), max(verts[2::3]))
        mesh.GetExtentAttr().Set(Vt.Vec3fArray([lo, hi]))
        if entry["color"]:
            mesh.GetDisplayColorAttr().Set(
                Vt.Vec3fArray([Gf.Vec3f(*entry["color"])]))
        # v0.7: the body is DERIVED geometry (ADR-0002, E14): mark it.
        mp = mesh.GetPrim()
        mp.ApplyAPI("AecoDerivedGeometryAPI")
        mp.GetAttribute("aeco:derived:source").Set(guid_to_uuid(guid))
        mp.GetAttribute("aeco:derived:role").Set("extent" if is_extent else "body")
        mp.GetAttribute("aeco:derived:approx").Set("tessellated")
        mp.GetAttribute("aeco:derived:stamp").Set(
            "ifc2usdaeco (ifcopenshell %s)" % getattr(ifcopenshell, "version", "?"))
        stats["meshes"] += 1
        if is_extent:
            stats["extents"] += 1

    if overlay_spine:
        # DefinePrim on an element or mesh promotes its ancestors to defs.
        # Restore spatial ownership only after all descendants are authored.
        for layer in (sem_layer, geo_layer):
            for path in spatial_paths:
                spec = layer.GetPrimAtPath(path)
                if spec is not None:
                    spec.specifier = Sdf.SpecifierOver

    stage.SetEditTarget(Usd.EditTarget(root_layer))
    stage.WriteFallbackPrimTypes()
    geo_layer.Save()
    sem_layer.Save()
    root_layer.Save()
    stats["out"] = out_path
    return stats
