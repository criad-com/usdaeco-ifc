"""Shared claim names; importing this list loads no native libraries."""
ORDERING_CASES = ("independent_order", "ordered_values")
ENUMERATION_CASES = ("enumerations_ifc4", "enumerations_ifc4x3", "enumerations_mixed")
CASES = ("ifc4", "ifc4x3", "ifc4_spaces", "unmapped", "phases", "unknown_status",
         "status_precedence", "headings", "extents", "extents_mm", "vanilla",
         "no_geometry", "determinism", "roles", "version") + ORDERING_CASES + ENUMERATION_CASES
