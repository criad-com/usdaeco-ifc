# Optional exact IFC export

`aeco-ifc-exact input.ifc --stage model.usda --out exact` selects every
meshable product represented in the existing metre stage. `--scope /Prim`,
repeatable `--class IfcWall`, and `--ids identities.json` narrow that cohort.
The identity list is a JSON array of existing `aeco:id` values. Default
linear deflection is 0.0001 m; override it with `--deflection`.

IfcOpenShell 0.8.5's `iterator-output = SERIALIZED` is the supported spelling
of the older USE_BREP_DATA route. It returns OCCT BRep text without requiring
pythonocc. The optional runtime reads that text, splits closed periodic
faces into canonical patches, writes UsdSolid BrepArrays and rebuilds them.
Every body must be a valid solid and preserve source volume within 1e-9
relative error. Unsupported representations and material correspondence
fail explicitly; `exact-report.json` includes counts and failures by class.

The ordinary converter modules are byte-identical to v0.1.2. The exact
command neither calls that converter nor changes its output files. It writes:

| File | Content |
|---|---|
| exact.usda | render-purpose BodyExact prims; core approximation, kernel tolerance and producer stamp |
| twins.usda | proxy-purpose Body meshes, guide-purpose Edges, source links and tessellation tolerances |
| exact-report.json | per-product outcomes, per-class counts, kernel and twin measures, reuse/material counts |

Both USD layers compose above the existing stage. Keep the root's metres,
up-axis, default prim and core fallbacks, and add `BrepArray = ["Xform"]`
to root `fallbackPrimTypes`. `BodyExact.proxyPrim` targets `Body`, and the
mesh's `aeco:derived:from` targets `BodyExact`. The twin contains its own
placement, so muting the exact layer does not move or remove it. Older mesh
representations are hidden only by the twin overlay.

IFC representation items' serialized styles partition canonical exact faces;
tessellation's source-face indices produce corresponding Mesh subsets. Bindings
use ordinary UsdShade materials. Mapped items share instanceable internal
representation prototypes keyed by representation map, evaluated BRep content
and styles. Identity, transforms and correlation remain on each occurrence.
A modified mapped occurrence with different evaluated content gets a separate
prototype. This is representation sharing, not a second element identity.

`USD_SOLID_OCCT_RUNTIME` names the built bridge runtime containing `paths.json`.
The family interpreter serializes IFC; a subprocess uses the bridge's native
Python and USD ABI. A small C++ adapter supplies BRep reading, surface
inspection and edge extraction omitted from the bridge's public bindings.
It dynamically links the existing OCCT libraries and caches its build;
`AECO_EXACT_CACHE` selects writable storage for immutable source packages.
No dependencies are installed. An absent runtime reports NOT RUN. A configured
but broken runtime fails. Default conversion never requires this runtime.

Exact export on arbitrary production IFC, compound layer schedules and exact
sync read-back are not established by this release. The companion solid
library demonstrates measurement, comparison, seeded validators and stock-USD
publication on the pinned office cohort.
