# Edited wall and pipe

Open `result/example.usdc` with `usdview result/example.usdc` from this directory.
The flattened final reimport is self-contained and needs no family plugins or
sibling checkouts. `result/vanilla.png` was rendered by stock USD in a fresh,
isolated process. The manifest records every file's hash and byte size.

To regenerate from the repository root, follow the main README environment and run:

```sh
env -u PYTHONPATH python examples/roundtrip/run.py --publish
```

Inputs are the published datacentre v0.4.5 `base` USD, the corresponding IFC
generated in scratch space, and `inputs/cameras.usda`. Core v0.9.2, axis v0.1.2
and sync v0.5.2 supply semantics and the transaction engine. The toolchain
v0.3.8 publication contract includes the native-kit name compatibility check. Ordinary runs refresh `out/` and compare findings; only `--publish`
replaces committed outputs.

The wall height increases by 0.15 m and the pipe length by 0.20 m. The pipe
endpoint is explicitly disconnected before extension. The convergence report
compares 14 driver coordinates/values and two body bounds and volumes by
identity. Missing bodies or changed findings fail. The published stage uses
the reimported meshes and drivers after convergence, with a display overlay
that hides surrounding elements and colours the two edited bodies.

`result/layers/out/session/` preserves the intent before application, solved
result, diagnostics, kind and derived layers. `result/layers/out/reimport/`
preserves the final imported kinds and guides. These reusable review opinions
omit live `aeco:host:*` bindings, their applied API and transaction layer metadata
(paths, times and optimistic concurrency tokens). The complete operational
sessions and IFC files remain transient under `.work/`; the review artifact is
not a resumable sync session. Drivers, geometry, identities and derivation
stamps are preserved. Source model layers are represented by the flattened
crate and are not duplicated as multi-megabyte text files in the layer archive.

The camera fits the combined body bounds with a 15% margin and renders
`proxy,render`. Room volumes retain purpose `guide`. `out/render-frame.json`
records the framing proof; every bounding-box corner must fit inside the
frustum. At least 2% of pixels must differ from their row's background by more
than `20/255` in an RGB channel. See [acceptance](../../docs/acceptance.md).

S27 relocates the crate alone, checks stock fallbacks and external dependencies,
and compares a fresh result using exact USDA/README bytes and `sdf-usda-v1`
crate normalization. S28 independently re-renders the committed crate with no
family plugins or `PYTHONPATH`. The caps are 10,000,000 bytes for all of `result/`,
2,000,000 per USDA layer, and 400,000 bytes/1,600 pixels per PNG dimension.

`--source-ifc model.ifc` shares a scratch IFC with the gate while retaining the
pinned USD source. An additional `--source-stage model.usda` selects an explicit
USD override, which the manifest records without claiming pinned composition.
Live integration rows remain NOT RUN.
