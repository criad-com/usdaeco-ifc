"""Scoped native checks; whole-document EXPRESS and IDS are explicit options."""

import json
import re
import ifcopenshell
import ifcopenshell.validate
import numpy as np
from ._ifc_utils import world, pipe_profile, usys, uel
from aeco_sync.diagnostics import Diagnostics


class ValidationView(ifcopenshell.file):
    """Iterate a closure while inverse cardinalities still see the real file.

    This is deliberately used only for attribute/inverse checks. Global EXPRESS
    rules require the original population and run only with validate_all.
    """
    def __init__(self, source, instances):
        # Borrow the population without registering a second owner in file_dict.
        self.source = source
        self.wrapped_data = source.wrapped_data
        self.instances = instances

    def __del__(self):
        pass

    def __iter__(self):
        return iter(self.instances)


def load_ids(path):
    try:
        from ifctester import ids
    except ImportError as exc:
        raise ValueError("--ids requires the optional dependency: pip install 'usdaeco-sync[ids]'") from exc
    return ids.open(str(path))


def geometry_log(host, entity):
    previous_level = "error"
    for line in ifcopenshell.get_log().splitlines():
        refs = [entity.GlobalId]
        try:
            row = json.loads(line)
            message = row.get("message", line)
            level = str(row.get("level", "error")).lower()
            instance = re.match(r"#\d+", str(row.get("instance", "")))
            if instance:
                refs.append(instance.group())
        except (ValueError, AttributeError):
            message = line
            match = re.match(r"\[(\w+)\]", line)
            level = match.group(1).lower() if match else previous_level
        previous_level = level
        severity = "warning" if "warn" in level or "warning" in line.lower() else "info" if "notice" in level else "error"
        path = host.path_for(entity)
        host._diagnostics.add(severity, "ifcopenshell:geometry", message,
            [path] if path else [], False, "regenerate", refs)


def validation_rows(host, rows, owners=None):
    diag = Diagnostics()
    owners = owners or {}
    for row in rows:
        entity = row.get("instance")
        match = re.match(r"^#?(\d+)(?:=|$)", str(entity)) if isinstance(entity, (int, str)) else None
        if match:
            try:
                entity = host.f.by_id(int(match.group(1)))
            except RuntimeError:
                entity = None
        path = host.path_for(entity) if hasattr(entity, "is_a") else None
        paths = [str(path)] if path else sorted(owners.get(entity.id(), ())) if hasattr(entity, "id") else []
        if not paths and hasattr(entity, "id"):
            todo, seen, found = [entity], set(), set()
            while todo:
                item = todo.pop()
                if item.id() in seen:
                    continue
                seen.add(item.id())
                path = host.path_for(item)
                if path:
                    found.add(str(path))
                elif not item.is_a("IfcRoot"):
                    todo.extend(host.f.get_inverse(item))
                elif item.is_a("IfcRelationship"):
                    todo.extend(e for e in host.f.traverse(item, max_levels=1) if e != item)
            paths = sorted(found)
        ref = getattr(entity, "GlobalId", None)
        ref = ref or ("#" + str(entity.id()) if hasattr(entity, "id") else None)
        message = row.get("message", str(row))
        rule = row.get("rule") or row.get("attribute")
        if not rule:
            match = re.search(r'(?:Rule\s+|)(Ifc\w+\.[A-Za-z0-9_]+)', message)
            rule = match.group(1) if match else "rule"
        severity = str(row.get("level", "error")).lower()
        diag.add(severity if severity in ("error", "warning", "info") else "error",
                 "ifc:" + str(rule), message, paths, phase="validate", host_refs=[ref] if ref else [])
    return diag.items


def validate(host):
    diag = Diagnostics()
    roots = [host.f.by_guid(ref) for ref in host.validation_refs]
    relations = {}
    if host.validate_all:
        relations = {r.id(): r for r in host.f.by_type("IfcRelConnectsPorts")}
    else:
        for root in roots:
            for port in usys.get_ports(root) if root.is_a("IfcDistributionElement") else []:
                for rel in list(port.ConnectedTo) + list(port.ConnectedFrom):
                    relations[rel.id()] = rel
    for rel in relations.values():
        a, b = rel.RelatingPort, rel.RelatedPort
        paths = [str(p) for p in (host.path_for(a), host.path_for(b)) if p]
        refs = [a.GlobalId, b.GlobalId]
        gap = float(np.linalg.norm(world(a)[:3, 3] - world(b)[:3, 3]))
        if gap > 1e-4:
            diag.add("error", "sync:gap", f"Connected ports are {gap:.6g} m apart", paths, phase="validate", host_refs=refs)
        pa, pb = usys.get_port_element(a), usys.get_port_element(b)
        ra, rb = pipe_profile(pa), pipe_profile(pb)
        if ra and rb and abs(ra.Radius - rb.Radius) > 1e-9:
            diag.add("warning", "pipePortSizeMismatch", "Connected pipe sizes differ without a transition", paths, phase="validate", host_refs=refs)
    logger = ifcopenshell.validate.json_logger()
    owners, instances = {}, {}
    if host.validate_all:
        native = host.f
    else:
        for root in roots:
            dependencies = list(host.f.traverse(root))
            # Associations are inverse IFC attributes, so forward traversal of
            # the product alone omits the section/type we may just have edited.
            for associated in (uel.get_material(root), uel.get_type(root)):
                if associated is not None:
                    dependencies.extend(host.f.traverse(associated))
            if root.is_a("IfcDistributionElement"):
                for port in usys.get_ports(root):
                    dependencies.extend(host.f.traverse(port))
            for entity in dependencies:
                if not entity.id():
                    continue
                instances[entity.id()] = entity
                path = host.path_for(root)
                if path:
                    owners.setdefault(entity.id(), set()).add(str(path))
        native = ValidationView(host.f, list(instances.values()))
    if host.validate_all or instances:
        ifcopenshell.validate.validate(native, logger, express_rules=host.validate_all)
        diag.items.extend(validation_rows(host, logger.statements, owners))
    if host.ids:
        host.ids.validate(host.f)
        for specification in host.ids.specifications:
            for requirement in specification.requirements:
                for failure in requirement.failures:
                    entity = failure["element"]
                    path = host.path_for(entity)
                    diag.add("error", "ids:" + (specification.identifier or specification.name), failure["reason"],
                             [path] if path else [], phase="validate", host_refs=[getattr(entity, "GlobalId", "#" + str(entity.id()))])
            if specification.status is False and not any(r.failures for r in specification.requirements):
                diag.add("error", "ids:" + (specification.identifier or specification.name),
                         "IDS applicability/cardinality requirement failed", [], phase="validate")
    return diag.items
