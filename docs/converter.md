# IFC converter

## 5.3 The converter, pass by pass

`tools/usdaeco_ifc/convert/` is small because the mapping is bijective. Its
structure is the mapping:

```
geometry.py   pure ifcopenshell, runs BEFORE pxr is imported:
              iterator over all products (threaded, booleans applied),
              create_shape fallback; local verts + 16-float placement
mapping.py    port type -> aeco:medium; flow enum -> aeco:flowDirection;
              Common Status -> phase; ifc_code(): '<Entity>[.<TYPE>]'
naming.py     deterministic prim names: sanitize(source name, else class);
              on sibling collision append '_' + first 6 hex of the id
author.py     the passes: spatial structure -> types -> elements
              (+ the axis of every path-based element, v0.7) -> groups
              -> ports -> geometry (each mesh marked as derived, v0.7)
cli.py        aeco-ifc convert <file.ifc> [-o out.usda] [--overlay-spine]
```

Output is three files: `<out>.usda` (root, sublayers the other two),
`<out>.semantics.usda` (spatial structure, elements, applied schemas,
groups, ports — small and diffable) and `<out>.geometry.usdc` (meshes —
binary). `stage.WriteFallbackPrimTypes()` runs last so the stage stays
legible with no schema loaded (B7).

**Declaration order (v0.8.1).** Spatial children across all aggregate
relationships, elements, used catalog types, systems and ports are emitted
in decoded `GlobalId` UUID order, then source name and IFC class. Meshes
are emitted in their source UUID order, independent of tessellation
completion or dictionary insertion order. STEP entity numbers remain
lookup keys only; they never choose declaration order or collision
suffixes. Catalog collisions use the type's decoded UUID too, without
authoring an occurrence identity on a class prim. All group prims exist
before membership targets are resolved, so references to other groups
survive the declaration order change.

This sorts declarations before authoring; relationship target sequences,
property arrays (including tours), point/face arrays, transform operations
and downstream explicit child/property reorder opinions retain their
order. Equivalent inputs with the same GlobalIds and ordered values
produce byte-identical root, semantics and geometry files with the same
output basename and tool versions. No post-export declaration sorting is
needed. The regression independently builds two IFC files with shuffled
creation order and STEP numbering, uses separate processes and hash seeds
with one/two tessellation threads, and compares raw files and layer texts.
Flattened text is compared with USD's source-filename comment disabled.
Each converter claim and pytest case uses a fresh temporary directory and native
process; an additional test seeds stale artifacts and verifies they are
neither consumed nor overwritten.

**Spatial dispatch.** `IfcBuilding` maps to `AecoFacility` in both IFC4
(where it has no `IfcFacility` superclass) and IFC4X3. Unmapped aggregate
nodes contribute no prim; traversal continues through their children.
Every aggregated `IfcBuildingStorey` becomes an `AecoLevel`, including
empty roof levels, whether or not it contains spaces or elements. The
converter preserves the source's declared levels; it does not infer
occupancy from geometry. Spatial transforms remain identity and elements
carry world placements in metres, so adding containment never moves them.

**Phase (v0.8.0).** Read `Status` from any occurrence `Pset_*Common`,
then from its type only when no occurrence Common set contains a Status
property. Single values and single-item enumerations are accepted, with
surrounding whitespace and letter case normalized. `OTHER`, `NOTKNOWN`,
`UNSET`, null and unsupported values author nothing. Conflicting Common
statuses and multi-value enumerations also author nothing. An explicit
unknown occurrence value does not borrow the type's phase. The converter
never interprets the schema fallback `proposed` as source evidence;
consumers distinguish it with `HasAuthoredValueOpinion()`. Catalog prims
carry the source Psets; phase is authored only on occurrences.

**Space extents (v0.8.0).** Tessellated `IfcSpace` bodies are local meshes
named `Extent`, with their world placement on the mesh, in the geometry
sublayer. The `AecoSpace` transform stays identity, so its contained
elements keep their world placement. The derived mark names the space's
`aeco:id`, role `extent`, approximation `tessellated`, and the usual
converter/IfcOpenShell stamp. `purpose = guide` is explicitly authored
so it survives plugin removal. Ordinary imaging excludes it; analytical
consumers may sample it as a target volume but must never use it as an
obstacle. Unrepresented spaces and `--no-geometry` produce no extent.

**Quarantined enumerations (v0.8.3).** Use the IFC property class to distinguish
enumerated selections from ordinary lists. A one-item enumeration becomes
its scalar value on both catalog types and occurrences: `Status = ["NEW"]`
and scalar `Status = "NEW"` both author a USD `string`. Multiple selections
remain arrays in their original order. If any member of a type/occurrence
family has several selections for the same Pset/property, use arrays for
that property throughout the family, wrapping scalar opinions in one-item
arrays. This preserves every selection and prevents inherited opinion type
mismatches even when occurrences choose different numbers of values. The
decision uses original property names and does not widen unrelated types.
Empty enumerations author nothing; ordinary list properties keep their array
shape even with one item. Numeric and boolean scalar values retain their
existing value mapping. This changes only the quarantined mirror; Common
Status precedence and the phase mapping above are unchanged. Generated IFC4
and IFC4X3 regressions check both inheritance directions, mixed cardinality,
ordered selections and ordinary lists with the core and built-in validators.

**Property headings.** Empty/whitespace property names and empty string
values (exporter UI headings) are omitted before name sanitization. This
prevents a heading from overwriting a real numeric or string property.
Zero and false are retained. Genuine names that sanitize to the same
attribute, including across Psets, are retained with deterministic
UUID-derived suffixes after the first value. The summary's
`blankHeadingsOmitted` counts omissions over all prim property passes;
inherited Psets may therefore be counted for both type and occurrence.
Summary `meshes` includes space extents; `extents` counts that subset and
`phases` counts elements with an authored phase.

**The axis pass (v0.7).** Walls get `AecoAxisAPI` from their `Axis`
representation's reference line; flow segments, beams, columns and
members from the base extrusion (start, direction × depth), all scaled
by the file's length unit into the prim's local space; `aeco:axis:length`
is written as the file's report of the length. The body mesh carries
`AecoDerivedGeometryAPI` (role body, approximation tessellated, stamp
`ifc2usdaeco (ifcopenshell <version>)`) so a consumer can tell the host's
picture from the drivers it was made from (E13/E14).

**`--overlay-spine`** authors the spatial structure as `over`s instead of
`def`s: a discipline IFC becomes a **federated layer over a shared spatial
structure** rather than a stand-alone stage, which is how several
per-discipline IFC exports compose into one twin (D9). Because naming is
deterministic, the same spatial prim from two exports lands on the same
path; because ids are lossless, `repath` repairs anything that does not.

**Kind health.** The converter never invents or translates a kind: the
IFC class is the classification code. What it reports is the number of
`IfcBuildingElementProxy` elements — IFC's own "kind unknown" — and the
validator repeats the count as `proxyClassified` warnings.

**Nothing downstream.** Where an earlier design decorated trays and
systems with discipline APIs inside the converter, the core converter
stops at core output. An element-kind library's importer is a further
pass over the same stage (or a further layer), reading `aeco:class:ifc:
code` and the `aeco:props:` quarantine and authoring its typed API — the
seam where a converter earns *native* import depth in the other direction
([06 §6.2](https://github.com/criad-com/usdaeco-core/blob/v0.8.4/docs/06-interfacing-other-formats.md)).

## 5.4 What is deliberately lossy, and where it lives

| IFC feature | Treatment |
|---|---|
| Objectified relationship entities (`IfcRel*`) | dissolve into namespace, relationships and collections — the *facts* survive, the bookkeeping does not |
| `CompositionType` / partial storeys | recursion |
| Deprecated storey `Elevation` | ignored; elevation read from placement |
| Property-set schemas | not carried as schema; values land in `aeco:props:` — governance happens by promoting a property into an element-kind API when it earns typed treatment |
| Material layer sets | `aeco:props:` in the core; an element-kind library's build-up API where one exists |
| Positioning elements, alignment business logic | the datum library |
| Provision-for-void, interference | coordination data, not element data |
| Tasks, schedules, actors, documents, cost items | the record tier |

## 5.5 The reverse direction

A usdAeco stage — or a **selected sub-part** of one — can be authored back
into native IFC entities. With the core alone the round trip is
*structural*: identity (`aeco:id` → `GlobalId` by compression), the
spatial structure, classification (the IFC class *is* the code), groups
and membership, ports and their links, and the Pset quarantine back into
Psets. Reconstruction-grade geometry (a swept profile for a tray) needs an
element-kind library's data; identity is what makes an element edited in
the IFC tool and re-exported return as the *same* element. Everything
else is an opinion in a stronger layer.


## Future exact pass

`aeco-ifc-exact` is a future implementation, requiring OCCT and the exact solid
integration. It will emit an exact body and a tessellated proxy twin, with
source correlation and tolerance metadata. This release does not install that
command, author exact bodies, or claim exact geometric convergence. The current
converter intentionally preserves the v0.8.4 tessellated output and tool stamp.
