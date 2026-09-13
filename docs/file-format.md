# IFC as a USD file format

`usdIfc` is a read-only C++ `SdfFileFormat` plugin. It lets a USD consumer open
an IFC delivery directly, or compose it through an ordinary sublayer. The
family converter remains the source of the spatial, element, type, system,
port and geometry mapping. No additional schema is introduced.

Keep **the .ifc and its .usda twin side by side, one root per form**:

```text
cooling.ifc
cooling.usda
cooling.semantics.usda
cooling.geometry.usdc
dc.connected.usda     # sublayers cooling.ifc
dc.usda               # sublayers cooling.usda
```

The connected root requires `usdIfc`. The USD-only root and converter twins
compose without it, using their `fallbackPrimTypes`. A package's producer is
provenance data, whether the delivery originated in an authoring application
or a generator. The plugin does not connect to running authoring applications.

## Build and interpreter

Use CMake 3.24+, a C++17 compiler and the released toolchain's `usd-dev` output.
The native plugin and consuming USD process must use the same USD ABI. The
converter can use a different USD/Python ABI because it runs in a subprocess.
Linux additionally needs the shared OpenSSL Crypto development package for
SHA-256; macOS uses system CommonCrypto.

```sh
export USD_DEV="$USD_DEV_OUTPUT"
./build.sh
```

`build.sh` reads propagated TBB/OpenSubdiv and Python locations from that
installation, then runs CMake with `-Dpxr_DIR`. It never builds dependencies.
Direct CMake consumers can pass the same dependency locations themselves.
Outputs are `out/plugins/usdIfc/usdIfc.dylib` (macOS) or `usdIfc.so` (Linux),
and `out/plugins/usdIfc/resources/plugInfo.json`. Set `BUILD_DIR` and `PREFIX`
to relocate build and installation outputs.

```sh
export USDAECO_IFC_SOURCE_PYTHON="$PYTHON"
export USDAECO_IFC_PYTHON="$PWD/tools/ifc-python"
export CORE_PLUGIN_DIR="$AECO_CORE_ROOT/usdAeco"
export AXIS_PLUGIN_DIR="$AECO_AXIS_ROOT/usdAecoAxis"
export PXR_PLUGINPATH_NAME="$CORE_PLUGIN_DIR:$PWD/out/plugins/usdIfc/resources"
```

Use the converter environment and sibling paths from the [README](../README.md).
The source launcher imports `bootstrap` and supplies source paths explicitly;
no package installation or setuptools is required. Paths containing spaces or
quotes are passed as subprocess arguments, without shell expansion.
Use resources from the selected release: an older `out/plugins` directory can
retain stale version metadata even when its checkout is at a newer tag.

`USDAECO_IFC_PYTHON` selects one executable, not a shell command. Without it,
a CMake build uses `python3` on PATH, which must already import `usdaeco_ifc`
and its conversion dependencies. The Nix package compiles in a launcher for
its own Python and core/axis resources; the environment variable can override
that default. Conversion tessellates before importing USD in its child process.

The optional package is `nix build .#usdIfc --no-write-lock-file`. Inputs remain
at public released tags. External source overrides follow the toolchain's
registry instructions; keep deployment configuration outside this checkout.
The package checks that its interpreter can import IfcOpenShell before building.
The measured attempt failed there because the toolchain Python environment
omitted IfcOpenShell on the tested platform. No second Nix attempt was made;
packaged execution and Linux compilation are **not proven**. The CMake path
uses an existing native USD build and the separate converter environment.

## Arguments and composition

| Argument | Default | Converter flag |
|---|---|---|
| `spine=def` | yes | ordinary conversion |
| `spine=over` | no | `--overlay-spine` |
| `geometry=1` | yes | geometry enabled |
| `geometry=0` | no | `--no-geometry` |

```usda
#usda 1.0
(
    subLayers = [
        @cooling.ifc:SDF_FORMAT_ARGS:spine=over&geometry=1@,
        @shared.ifc@
    ]
)
```

Copy stage metadata, including the full `fallbackPrimTypes` dictionary, from
the corresponding USD-only root. Root metadata does not compose from sublayers.
`spine=over` leaves spatial ancestors as `over`s, with discipline elements,
types, systems and ports retained. Spatial definitions and space extents come
from the shared package. `geometry=0` emits no gprims. Unknown arguments and
invalid values fail with a USD runtime error.

## Cache and read behavior

Cache location, in priority order:

1. `USDAECO_IFC_CACHE` (the cache directory itself).
2. `XDG_CACHE_HOME/usdaeco-ifc`.
3. `HOME/.cache/usdaeco-ifc`.

Each `Read()` hashes the IFC bytes with SHA-256 and asks the selected converter
for `usdaeco_ifc.__version__`. The cache key is the SHA-256 of this UTF-8 record,
including its final newline:

```text
usdIfc-cache-v1
<IFC byte SHA-256>
spine=<def or over>
geometry=<0 or 1>
converter=<version>
```

Explicit defaults share a key with omitted defaults. Paths and file modification
times are not keys. Changed source bytes, arguments or converter versions create
new entries. The interpreter remains necessary for the version query on a hit.
Development edits without a version bump require cache eviction.

On a miss, `Read()` invokes `python -m usdaeco_ifc.convert <ifc> -o
<temporary-entry>/model.usda` with the selected flags. It opens the converter's
root using `UsdStage::Open`, checks composition errors, calls `Flatten(false)`,
preserves root `customLayerData`, and exports `flattened.usdc`. The complete
directory is atomically renamed to `<cache>/<key>`. Readers then open that
crate anonymously and call `SdfLayer::TransferContent`: the IFC layer contains
one complete layer and has no cache sublayer dependencies. The original
`model.usda`, semantics and geometry remain in the entry for inspection.

A per-key file lock serializes concurrent misses. Failed conversion leaves no
published entry; the temporary directory is removed. Source bytes are checked
again before publication to reject edits during conversion. Runtime errors
include the converter's bounded output/stderr tail. Metadata-only requests
currently perform the same read. `WriteToFile` always returns false; exporting
the loaded content to `.usda` is supported for inspection.

Cache entries have no automatic size limit or expiry. Remove unused entries
when no reader is active; remove a corrupt entry and reopen to regenerate it.
For the same open USD layer, use `Reload()` after source changes: ordinary USD
layer reuse does not call `Read()` again merely because `Stage.Open` is repeated.

## Verification

```sh
env -u PYTHONPATH "$PYTHON" -m pytest -q tests/test_file_format.py
env -u PYTHONPATH "$PYTHON" check.py
```

With `USD_DEV` set, tests require the build products and launch that USD's
matching Python. Their IFC is generated in `.work/` through the roundtrip's
`build_base` helper. The full gate reuses its freshly generated IFC. Native
probes register only `usdIfc`; the twin probe starts without any family plugins.
They compare census and world transforms, composition, spatial overs, no-geometry
mode, deterministic fresh-process opens, cache hits and concurrent publication,
error diagnostics and read-only behavior. Without `USD_DEV`, the optional native
tests are explicitly skipped and the existing converter/host gate remains usable.

If `AECO_DATACENTRE_ROOT/dist/full/dc.connected.usda` exists, the suite also
compares it with `dist/full/dc.usda`; otherwise that row is **not proven**.
