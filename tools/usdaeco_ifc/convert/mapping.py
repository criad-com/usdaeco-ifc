"""IFC -> usdAeco lookup tables (the 'lookups' half of the converter).

Deliberately tiny. Kind is never translated: an IFC entity name (plus its
PredefinedType) IS the classification code, so the only tables here are
the closed core vocabularies IFC also has as enums — port medium,
flow direction and asset phase.
"""

# IfcDistributionPortTypeEnum -> aeco:medium
MEDIUM = {
    "CABLE": "cable",
    "CABLECARRIER": "cableCarrier",
    "DUCT": "duct",
    "PIPE": "pipe",
    "WIRELESS": "wireless",
}

# IfcFlowDirectionEnum -> aeco:flowDirection
FLOW = {
    "SOURCE": "source",
    "SINK": "sink",
    "SOURCEANDSINK": "bidirectional",
}

# Pset_<Class>Common.Status -> asset phase. Unknown means no opinion.
PHASE = {"NEW": "proposed", "EXISTING": "existing",
         "DEMOLISH": "demolished", "TEMPORARY": "temporary"}


def phase(entity):
    """Read occurrence Status first, then type only if absent. A present
    unknown, empty or conflicting Status never borrows a type's phase.
    IfcPropertyEnumeratedValue arrives from get_psets as a list.
    """
    import ifcopenshell.util.element as ue

    def statuses(obj):
        values = []
        for name, props in ue.get_psets(obj, psets_only=True, should_inherit=False).items():
            if name.startswith("Pset_") and name.endswith("Common") and "Status" in props:
                value = props["Status"]
                if isinstance(value, (list, tuple)):
                    value = value[0] if len(value) == 1 else None
                values.append(value.strip().upper() if isinstance(value, str) else None)
        return values

    values = statuses(entity)
    if not values:
        typ = ue.get_type(entity)
        if typ is not None:
            values = statuses(typ)
    return PHASE.get(values[0]) if values and len(set(values)) == 1 else None


def predefined_type(entity):
    """The effective PredefinedType of an entity (falling back through
    its type object as ifcopenshell does), or None when unset/NOTDEFINED."""
    try:
        import ifcopenshell.util.element as _ue
        p = _ue.get_predefined_type(entity)
    except Exception:
        p = getattr(entity, "PredefinedType", None)
        p = str(p) if p else None
    if p in (None, "", "NOTDEFINED"):
        return None
    return str(p)


def ifc_code(entity):
    """The classification code for aeco:class:ifc:code — '<Entity>' or
    '<Entity>.<PREDEFINEDTYPE>' (registries/classification_systems.json)."""
    ptype = predefined_type(entity)
    return entity.is_a() + ("." + ptype if ptype else "")
