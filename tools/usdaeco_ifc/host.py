"""IFC authoring host: native transactions and closure-only geometry receipts."""

from pathlib import Path
import ifcopenshell.validate
from ifcopenshell.api import run
from ifcopenshell.util.shape_builder import ShapeBuilder
from aeco_sync.hosts.base import Host, MutationReceipt
from ._ifc_utils import *
from aeco_sync.closure import segment_near_end, JOIN_NAMES
from aeco_sync.diagnostics import Diagnostics
from aeco_sync.stack import digest
from . import _ifc_authoring as authoring
from . import _ifc_cctv as cctv
from aeco_sync.identity import guid_to_uuid, uuid_to_guid

GAP_TOL = 1e-4
STAMP = "ifcopenshell " + ifcopenshell.version


def set_pipe_depth_and_ports(f, e, new_depth, new_start_world=None):
    if new_depth <= 1e-9:
        raise ValueError("Pipe depth must be positive")
    ex = base_extrusion(e)
    old_depth = float(ex.Depth)
    ends = [
        (port, abs(local[2, 3]) < abs(local[2, 3] - old_depth))
        for port, local in ports_of(e)
    ]
    m = world(e).copy()
    if new_start_world is not None:
        m[:3, 3] = new_start_world
        run(
            "geometry.edit_object_placement",
            f,
            product=e,
            matrix=m,
            should_transform_children=False,
        )
    ex.Depth = float(new_depth)
    for port, at_start in ends:
        pm = m.copy()
        pm[:3, 3] += m[:3, 2] * (0 if at_start else new_depth)
        run("geometry.edit_object_placement", f, product=port, matrix=pm)


def realign(f, moved_port, delta, visited, log):
    """Bonsai's rule: a connected segment extends/trims its near end to the moved port (its far end stays); anything else translates by delta and its other ports propagate."""
    peer = usys.get_connected_port(moved_port)
    if peer is None:
        return
    pe = usys.get_port_element(peer)
    if pe.id() in visited:
        return
    visited.add(pe.id())
    target = world(moved_port)[:3, 3]
    if pe.is_a("IfcFlowSegment"):
        m = world(pe)
        zdir = m[:3, 2]
        ex = base_extrusion(pe)
        depth = float(ex.Depth)
        start = m[:3, 3]
        end = start + zdir * depth
        peer_local_z = (np.linalg.inv(m) @ world(peer))[2, 3]
        near_start = abs(peer_local_z) < abs(peer_local_z - depth)
        new_start, new_end = segment_near_end(start, end, target, near_start)
        set_pipe_depth_and_ports(
            f, pe, float(np.linalg.norm(new_end - new_start)), new_start_world=new_start
        )
        log.append(
            f"{pe.Name}: near end aligned, depth {depth:.3f} -> {np.linalg.norm(new_end-new_start):.3f}"
        )
        far_delta = new_end - end if near_start else new_start - start
        if np.linalg.norm(far_delta) > 1e-9:
            for other, _ in ports_of(pe):
                if other.id() != peer.id():
                    realign(f, other, far_delta, visited, log)
    else:
        m = world(pe)
        m[:3, 3] += delta
        run(
            "geometry.edit_object_placement",
            f,
            product=pe,
            matrix=m,
            should_transform_children=True,
        )
        log.append("%s: translated by %s" % (pe.Name, np.round(delta, 3).tolist()))
        for other, _ in ports_of(pe):
            if other.id() != peer.id():
                realign(f, other, delta, visited, log)


def recalculate_walls(f, walls, log):
    """Port of Bonsai's tool.Model.recalculate_walls: the wall plus everything it is joined to/from, each regenerated."""
    queue = {}
    todo = list(walls)
    while todo:
        w = todo.pop()
        if w.id() in queue:
            continue
        queue[w.id()] = w
        for rel in w.ConnectedTo:
            if rel.is_a("IfcRelConnectsPathElements"):
                todo.append(rel.RelatedElement)
        for rel in w.ConnectedFrom:
            if rel.is_a("IfcRelConnectsPathElements"):
                todo.append(rel.RelatingElement)
    for w in queue.values():
        usage = uel.get_material(w)
        if not usage or not usage.is_a("IfcMaterialLayerSetUsage"):
            # Simple exported sweeps already carry their edited depth/placement.
            # No layer section exists for the layered-wall regeneration API.
            continue
        run("geometry.regenerate_wall_representation", f, wall=w)
        log.append(
            "%s: regenerated (axis now %s)"
            % (w.Name, [np.round(p, 3).tolist() for p in urep.get_reference_line(w)])
        )
    return list(queue.values())


class IfcHost(Host):
    name = "ifc"
    file_backed = True
    file_suffix = ".ifc"

    def __init__(self, session=None, document=None, *, validate_all=False, ids=None):
        self._options = (document, validate_all, ids)
        self.f = None
        if session is not None:
            self.open(session)

    @classmethod
    def initialize(cls, model, document, directory=None, policy="keepConnected", *, kind_import=False):
        from aeco_sync.stack import create, PREFIX
        if kind_import:
            from .kind_import import main
            session = main(str(document), str(model), directory)
        else:
            from aeco_sync.readback import bind
            session = create(model, document, directory, policy)
            native = ifcopenshell.open(str(document))
            idx = index_ids(session.stage)
            with Usd.EditContext(session.stage, session.layer("kind.usda")):
                for entity in native.by_type("IfcRoot"):
                    prim = prim_for(idx, entity)
                    if prim:
                        bind(prim, "ifc", entity.GlobalId, "#" + str(entity.id()),
                             session.version("ifc"), str(Path(document).resolve()))
            session.layer("kind.usda").Save()
        session.root.customLayerData = {**session.root.customLayerData, PREFIX + "gapPolicy": policy}
        session.root.Save()
        return session

    def capabilities(self):
        return frozenset({"attribute", "relationship", "activation", "primMetadata", "create"})

    def open(self, session):
        document, validate_all, ids = self._options
        self.session = session
        self.document = Path(document or session.document(self.name)).resolve()
        self.f = ifcopenshell.open(str(self.document))
        self.current = session.current()
        self.idx = index_ids(self.current)
        self._diagnostics = Diagnostics()
        self.log = []
        self.deleted = []
        self.camera_operations = []
        self._unsupported_reason = None
        self.paths = {uuid_to_guid(key): prim.GetPath() for key, prim in self.idx.items()}
        for prim in self.current.TraverseAll():
            from aeco_sync.cctv import is_sensor
            if is_sensor(prim):
                continue
            binding = prim.GetAttribute(f"aeco:host:{self.name}:ref")
            if not binding or not binding.Get():
                binding = prim.GetAttribute("aeco:host:ifc:ref")
            if binding and binding.Get():
                self.paths[binding.Get()] = prim.GetPath()
        previous = find_layer(self.current, f"result.{self.name}.usda")
        self.validation_refs = set(previous.customLayerData.get("aeco:sync:closure", [])) if previous else set()
        self.validate_all = validate_all
        self.ids = ids
        self.stamp = STAMP if self.name == "ifc" else "Bonsai 0.8.5 / " + STAMP
        self._version = digest(self.document)
        from ifcopenshell.util import unit
        if abs(unit.calculate_unit_scale(self.f) - 1.) > 1e-12:
            raise ValueError("IFC host currently requires length units in metres")
        if ids:
            from ._ifc_validation import load_ids
            self.ids = load_ids(ids)
        return self

    def diagnostics(self):
        return list(self._diagnostics.items)

    def close(self):
        self.f = None


    def version(self):
        return self._version

    def entity_for_path(self, path):
        known = next((ref for ref, bound in self.paths.items() if str(bound) == str(path)), None)
        if known:
            return self.f.by_guid(known)
        prim = self.current.GetPrimAtPath(path)
        ref = prim.GetAttribute("aeco:host:ifc:ref").Get() if prim else None
        if not ref:
            raise ValueError(f"No IFC binding for target {path}")
        return self.f.by_guid(ref)

    def register_new(self, entity, path):
        self.paths[entity.GlobalId] = Sdf.Path(path)
        for port in usys.get_ports(entity):
            self.paths[port.GlobalId] = Sdf.Path(path).AppendChild("Port_" + guid_to_uuid(port.GlobalId).replace("-", ""))

    def path_for(self, entity):
        if entity is None:
            return None
        ref = getattr(entity, "GlobalId", None)
        if ref in self.paths:
            return self.paths[ref]
        if entity.is_a("IfcDistributionPort"):
            owner = usys.get_port_element(entity)
            path = self.path_for(owner)
            if path:
                self.paths[ref] = path.AppendChild("Port_" + guid_to_uuid(ref).replace("-", ""))
        elif entity.is_a("IfcAudioVisualApplianceType"):
            self.paths[ref] = Sdf.Path("/_Types/Camera_" + guid_to_uuid(ref).replace("-", "_"))
        elif entity.is_a("IfcPipeFitting") or entity.is_a("IfcPipeSegment") or cctv.is_camera(entity):
            container = uel.get_container(entity)
            path = self.path_for(container)
            if path:
                self.paths[ref] = path.AppendChild("Element_" + guid_to_uuid(ref).replace("-", ""))
        return self.paths.get(ref)

    def wall_location_token(self, entity):
        stored = authoring.remembered(entity)
        if "LocationLine" in stored:
            return stored["LocationLine"]
        usage = uel.get_material(entity)
        if not usage or not usage.is_a("IfcMaterialLayerSetUsage"):
            return "finishFaceExterior"
        sign = -1 if usage.DirectionSense == "NEGATIVE" else 1
        return "centerline" if abs(usage.OffsetFromReferenceLine + sign * wall_thickness(entity) / 2) < 1e-7 else "finishFaceExterior"

    def creation_section(self, edit):
        data = edit.value
        if len(data["inherits"]) != 1:
            raise ValueError("A new pipe requires one bound catalog type")
        typ = self.entity_for_path(data["inherits"][0])
        if not typ.is_a("IfcPipeSegmentType"):
            raise ValueError("New pipe type must be IfcPipeSegmentType")
        prim = self.current.GetPrimAtPath(data["inherits"][0])
        arrays = [list(prim.GetAttribute("aeco:pipeType:" + n).Get() or [])
                  for n in ("nominalDiameters", "outerDiameters", "innerDiameters")]
        nominal = float(data["attributes"]["aeco:pipe:nominalDiameter"])
        if not arrays[0] or len({len(a) for a in arrays}) != 1:
            raise ValueError("Invalid pipe type size table")
        i = next((i for i, n in enumerate(arrays[0]) if abs(n - nominal) < 1e-9), None)
        if i is None or not 0 < arrays[2][i] < arrays[1][i]:
            raise ValueError("Pipe size is absent from the type table or invalid")
        return dict(nominal=nominal, outer=arrays[1][i], inner=arrays[2][i], label=f"DN{round(nominal*1000)}")

    def supports(self, edit):
        self._unsupported_reason = None
        if edit.operation == "create" and edit.value.get("kind") == "camera":
            return cctv.supports(self, edit)
        if edit.operation == "create":
            data, attrs = edit.value, edit.value["attributes"]
            allowed = {"aeco:id", "aeco:axis:start", "aeco:axis:end", "aeco:axis:curve",
                       "aeco:pipe:nominalDiameter", "xformOp:transform", "xformOpOrder"}
            try:
                self.creation_section(edit)
                start, end = np.array(attrs["aeco:axis:start"]), np.array(attrs["aeco:axis:end"])
                if attrs.get("aeco:id"):
                    ref = uuid_to_guid(attrs["aeco:id"])
                    try:
                        self.f.by_guid(ref)
                        return False
                    except RuntimeError:
                        pass
                return (set(attrs) <= allowed and attrs.get("aeco:axis:curve", "line") == "line"
                        and np.isfinite(start).all() and np.isfinite(end).all() and np.linalg.norm(end-start) > 1e-9
                        and not data["relationships"] and set(data["metadata"]) <= {"specifier", "typeName", "apiSchemas", "inheritPaths", "displayName"})
            except (ValueError, KeyError, RuntimeError, TypeError):
                return False
        try:
            entity = self.f.by_guid(edit.ref)
        except RuntimeError:
            return False
        if cctv.is_camera(entity):
            return cctv.supports(self, edit)
        if edit.operation == "primMetadata":
            if edit.name != "inheritPaths" or len(edit.value) != 1:
                return False
            try:
                typ = self.entity_for_path(edit.value[0])
                return (entity.is_a("IfcWall") and typ.is_a("IfcWallType")) or (entity.is_a("IfcPipeSegment") and typ.is_a("IfcPipeSegmentType"))
            except (ValueError, RuntimeError):
                return False
        if entity.is_a("IfcWall"):
            usage = uel.get_material(entity)
            if not usage or not usage.is_a("IfcMaterialLayerSetUsage"):
                if edit.name not in {"aeco:wall:height", "aeco:wall:baseOffset", "aeco:wall:topOffset", "xformOp:transform", "xformOpOrder"} and edit.operation != "activation":
                    return False
        if entity.is_a("IfcPipeSegment") and edit.name == "aeco:pipe:nominalDiameter":
            material = uel.get_material(entity, should_skip_usage=True)
            if not material or not material.is_a("IfcMaterialProfileSet"):
                return False
        if edit.name == "xformOpOrder":
            return list(edit.value) == ["xformOp:transform"]
        if edit.operation == "activation":
            return edit.value is False and entity.is_a("IfcElement")
        if edit.operation == "relationship":
            valid = ((edit.name in JOIN_NAMES and entity.is_a("IfcWall")) or
                     (edit.name == "aeco:connectedPorts" and entity.is_a("IfcDistributionPort") and len(edit.value) <= 1))
            if not valid:
                return False
            try:
                return all(self.entity_for_path(p).is_a("IfcWall" if edit.name in JOIN_NAMES else "IfcDistributionPort") for p in edit.value)
            except (ValueError, RuntimeError):
                return False
        if edit.kind == "axis":
            if entity.is_a("IfcPipeSegment"):
                if edit.name not in ("aeco:axis:start", "aeco:axis:end"):
                    return False
                start, end, _ = pipe_axis_local(entity)
                desired = np.array(edit.value) - start if edit.name.endswith(":end") else end - np.array(edit.value)
                return np.isfinite(desired).all() and np.linalg.norm(np.cross(desired, end-start)) < 1e-8 and np.dot(desired, end-start) > 0
            return entity.is_a("IfcWall") and edit.name in ("aeco:axis:start", "aeco:axis:end")
        if edit.kind == "transform":
            if edit.name != "xformOp:transform" or not (entity.is_a("IfcPipeSegment") or entity.is_a("IfcWall")):
                return False
            before, after = np.array(edit.current), np.array(edit.value)
            return np.isfinite(after).all() and np.allclose(before[:3, :3], after[:3, :3], atol=1e-10) and np.allclose(after[:, 3], [0, 0, 0, 1])
        if entity.is_a("IfcWall") and edit.name in authoring.WALL_FIELDS:
            return edit.name != "aeco:wall:locationLine" or edit.value in ("centerline", "coreCenterline", "finishFaceExterior", "finishFaceInterior", "coreFaceExterior", "coreFaceInterior")
        if entity.is_a("IfcWallType") and edit.name in authoring.LAYER_FIELDS:
            return True
        return edit.name == "aeco:pipe:nominalDiameter" and entity.is_a("IfcPipeSegment")

    def apply(self, edits):
        closure = edits.closure
        touched = {}
        self.f.begin_transaction()
        removed_walls = {}
        joined_pairs = set()
        resized_fittings = {}
        try:
            for path in closure.disconnect:
                port = self.current.GetPrimAtPath(path)
                ref = port.GetAttribute("aeco:host:ifc:ref").Get()
                peer = usys.get_connected_port(self.f.by_guid(ref))
                if peer:
                    owner = usys.get_port_element(peer)
                    touched[owner.id()] = owner
                run("system.disconnect_port", self.f, port=self.f.by_guid(ref))
            # Remove relations before constrained endpoint operations.
            ordered = sorted(edits, key=lambda e: (
                0 if e.operation == "create" else
                1 if e.name == "inheritPaths" else
                1 if (e.operation == "relationship" and not e.value) or (e.name.startswith("aeco:wall:allowJoin") and not e.value) else
                3 if e.operation == "relationship" else 2))
            for edit in ordered:
                if edit.operation == "create":
                    entity = cctv.create_camera(self, edit) if edit.value.get("kind") == "camera" else authoring.create_pipe(self, edit)
                    touched[entity.id()] = entity
                    continue
                entity = self.f.by_guid(edit.ref)
                prim = self.current.GetPrimAtPath(edit.path)
                if edit.name == "xformOpOrder":
                    continue
                if edit.operation == "activation":
                    self.deleted.append(
                        {"path": edit.path, "ref": edit.ref, "active": False}
                    )
                    from aeco_sync.closure import neighbours

                    adjacent = []
                    for path in neighbours(self.current, edit.path):
                        neighbour = self.current.GetPrimAtPath(path)
                        ref = neighbour.GetAttribute("aeco:host:ifc:ref").Get()
                        if ref:
                            adjacent.append(self.f.by_guid(ref))
                    if cctv.is_camera(entity):
                        cctv.execute(self, dict(operation="delete", ref=edit.ref, path=edit.path))
                    else:
                        run("root.remove_product", self.f, product=entity)
                    for neighbour in adjacent:
                        touched[neighbour.id()] = neighbour
                    walls = [e for e in adjacent if e.is_a("IfcWall")]
                    if walls:
                        touched.update(
                            {
                                w.id(): w
                                for w in recalculate_walls(self.f, walls, self.log)
                            }
                        )
                    continue
                if cctv.is_camera(entity):
                    cctv.apply_edit(self, entity, edit)
                    touched[entity.id()] = entity
                    continue
                if edit.operation == "relationship":
                    if edit.name == "aeco:connectedPorts":
                        if edit.value:
                            peer = self.entity_for_path(edit.value[0])
                            pair = frozenset((entity.GlobalId, peer.GlobalId))
                            if pair not in joined_pairs:
                                touched.update({e.id(): e for e in authoring.join_ports(self, entity, peer)})
                                joined_pairs.add(pair)
                        else:
                            peer = usys.get_connected_port(entity)
                            for port in (entity, peer):
                                if port:
                                    owner = usys.get_port_element(port)
                                    touched[owner.id()] = owner
                            run("system.disconnect_port", self.f, port=entity)
                    else:
                        connection = dict(zip(JOIN_NAMES, ("ATSTART", "ATEND", "ATPATH")))[edit.name]
                        desired = {self.entity_for_path(p).GlobalId for p in edit.value}
                        existing = set()
                        for rel in list(entity.ConnectedTo) + list(entity.ConnectedFrom):
                            if not rel.is_a("IfcRelConnectsPathElements"):
                                continue
                            first = rel.RelatingElement == entity
                            end = rel.RelatingConnectionType if first else rel.RelatedConnectionType
                            other = rel.RelatedElement if first else rel.RelatingElement
                            if end != connection:
                                continue
                            existing.add(other.GlobalId)
                            if other.GlobalId not in desired:
                                removed_walls[other.id()] = other
                                run("geometry.disconnect_path", self.f, relating_element=rel.RelatingElement, related_element=rel.RelatedElement)
                        for ref in desired - existing:
                            other = self.f.by_guid(ref)
                            rel = run("geometry.connect_wall", self.f,
                                wall1=other if connection == "ATPATH" else entity,
                                wall2=entity if connection == "ATPATH" else other,
                                is_atpath=connection == "ATPATH")
                            if rel is None:
                                raise ValueError("Wall axes do not intersect")
                            actual = rel.RelatingConnectionType if rel.RelatingElement == entity else rel.RelatedConnectionType
                            if actual != connection:
                                raise ValueError("Requested join does not match the intersecting wall end")
                            removed_walls[other.id()] = other
                        removed_walls[entity.id()] = entity
                    continue
                if edit.operation == "primMetadata" and edit.name == "inheritPaths":
                    typ = self.entity_for_path(edit.value[0])
                    usage = uel.get_material(entity)
                    location = self.wall_location_token(entity) if entity.is_a("IfcWall") else None
                    direction = usage.DirectionSense if location else None
                    run("type.assign_type", self.f, related_objects=[entity], relating_type=typ, should_map_representations=False)
                    material = uel.get_material(typ, should_skip_usage=True)
                    run("material.assign_material", self.f, products=[entity],
                        type="IfcMaterialLayerSetUsage" if location else "IfcMaterialProfileSetUsage", material=material)
                    if location:
                        usage = uel.get_material(entity)
                        usage.DirectionSense = direction
                        authoring.wall_location(usage, location)
                        removed_walls[entity.id()] = entity
                    else:
                        base_extrusion(entity).SweptArea = pipe_profile(entity)
                        authoring.remember(self.f, entity, NominalDiameter=None)
                    touched[entity.id()] = entity
                    touched[typ.id()] = typ
                    continue
                if entity.is_a("IfcWallType") and edit.name in authoring.LAYER_FIELDS:
                    occurrences = list(uel.get_types(entity))
                    locations = {e.id(): self.wall_location_token(e) for e in occurrences}
                    authoring.edit_layers(self, entity, edit)
                    for wall in occurrences:
                        authoring.wall_location(uel.get_material(wall), locations[wall.id()])
                        removed_walls[wall.id()] = wall
                    touched[entity.id()] = entity
                    continue
                if entity.is_a("IfcWall") and edit.name in authoring.WALL_FIELDS:
                    for rel in list(entity.ConnectedTo) + list(entity.ConnectedFrom):
                        if rel.is_a("IfcRelConnectsPathElements"):
                            for w in (rel.RelatingElement, rel.RelatedElement):
                                removed_walls[w.id()] = w
                    authoring.wall_edit(self, entity, edit)
                    removed_walls[entity.id()] = entity
                    continue
                if edit.kind == "axis" and entity.is_a("IfcPipeSegment"):
                    start, end, depth = pipe_axis_local(entity)
                    at_start = edit.name.endswith(":start")
                    point = np.asarray(edit.value, float)
                    new_depth = float(np.linalg.norm(end-point if at_start else point-start))
                    matrix = world(entity)
                    delta = matrix[:3, :3] @ (point - (start if at_start else end))
                    ends = [p for p, local in ports_of(entity)
                            if (abs(local[2, 3]) < abs(local[2, 3] - depth)) == at_start]
                    new_start = (matrix @ np.append(point, 1))[:3] if at_start else None
                    set_pipe_depth_and_ports(self.f, entity, new_depth, new_start)
                    touched[entity.id()] = entity
                    if ends and closure.policy == "keepConnected":
                        visited = {entity.id()}
                        before_log = len(self.log)
                        realign(self.f, ends[0], delta, visited, self.log)
                        touched.update({i: self.f.by_id(i) for i in visited})
                        if len(visited) > 1:
                            self._diagnostics.add(
                                "info",
                                "sync:neighbourMoved",
                                "; ".join(self.log[before_log:]),
                                [
                                    self.path_for(self.f.by_id(i))
                                    for i in visited
                                    if i != entity.id()
                                ],
                            )
                elif edit.kind == "axis" and entity.is_a("IfcWall"):
                    start, end = wall_axis_local(entity)
                    if edit.name.endswith(":start"):
                        start = np.array(edit.value)
                    else:
                        end = np.array(edit.value)
                    if np.linalg.norm(end - start) < 1e-9:
                        raise ValueError("Degenerate wall axis")
                    ShapeBuilder(self.f).set_polyline_coords(
                        urep.get_representation(
                            entity, "Plan", "Axis", "GRAPH_VIEW"
                        ).Items[0],
                        [
                            (float(start[0]), float(start[1])),
                            (float(end[0]), float(end[1])),
                        ],
                    )
                    touched.update(
                        {
                            w.id(): w
                            for w in recalculate_walls(self.f, [entity], self.log)
                        }
                    )
                elif edit.kind == "transform":
                    old = world(entity).copy()
                    parent_world = UsdGeom.XformCache().GetLocalToWorldTransform(
                        prim.GetParent()
                    )
                    matrix = gf_to_np(Gf.Matrix4d(edit.value) * parent_world)
                    run(
                        "geometry.edit_object_placement",
                        self.f,
                        product=entity,
                        matrix=matrix,
                        should_transform_children=True,
                    )
                    touched[entity.id()] = entity
                    if entity.is_a("IfcWall"):
                        walls = recalculate_walls(self.f, [entity], self.log)
                        touched.update({w.id(): w for w in walls})
                        others = [w for w in walls if w != entity]
                        if others:
                            self._diagnostics.add(
                                "info",
                                "sync:neighbourMoved",
                                "Joined wall set regenerated",
                                [self.path_for(w) for w in others],
                            )
                    elif closure.policy == "keepConnected":
                        visited = {entity.id()}
                        for port, _ in ports_of(entity):
                            realign(
                                self.f,
                                port,
                                matrix[:3, 3] - old[:3, 3],
                                visited,
                                self.log,
                            )
                        touched.update({i: self.f.by_id(i) for i in visited})
                elif edit.kind == "section":
                    section = edit.section
                    profile = pipe_profile(entity)
                    run("material.edit_profile", self.f, profile=profile, attributes={
                        "Radius": section["outer"] / 2,
                        "WallThickness": (section["outer"] - section["inner"]) / 2,
                        "ProfileName": section["label"]})
                    # Profile definitions may be shared: read every affected occurrence.
                    for occurrence in uel.get_elements_by_profile(profile):
                        if not occurrence.is_a("IfcPipeSegment"):
                            continue
                        authoring.remember(self.f, occurrence, NominalDiameter=float(section["nominal"]))
                        touched[occurrence.id()] = occurrence
                        for port in usys.get_ports(occurrence):
                            peer = usys.get_connected_port(port)
                            fitting = usys.get_port_element(peer) if peer else None
                            if fitting and fitting.is_a("IfcPipeFitting") and authoring.remembered(fitting).get("Origin") == "generated":
                                resized_fittings[fitting.GlobalId] = fitting
            for fitting in resized_fittings.values():
                touched.pop(fitting.id(), None)
                touched.update({e.id(): e for e in authoring.rebuild_fitting(self, fitting)})
            if removed_walls:
                touched.update({w.id(): w for w in recalculate_walls(self.f, list(removed_walls.values()), self.log)})
            self.validation_refs = {e.GlobalId for e in touched.values()}
            self.f.end_transaction()
            return MutationReceipt(tuple(sorted(self.validation_refs)), self.version())
        except Exception:
            self.f.discard_transaction()
            self.deleted = []
            self.camera_operations = []
            self._unsupported_reason = None
            raise

    def save(self, path):
        self.f.write(str(path))
        self.document = Path(path).resolve()
        self._version = digest(path)

    def readback(self, touched):
        if touched is None:
            touched = [e.GlobalId for e in self.f.by_type("IfcElement")]
        from ._ifc_receipt import readback
        return readback(self, touched)

    def geometry_log(self, entity):
        from ._ifc_validation import geometry_log
        geometry_log(self, entity)

    def validate(self):
        from ._ifc_validation import validate
        return validate(self)
