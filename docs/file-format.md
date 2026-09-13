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
The v0.3.1 attempt used an external registry override, disabled substitutes and
remote builders, and requested offline resolution. It stopped at the published
CCTV input with a GitHub lookup returning HTTP 404; the registry did not resolve
that direct input. No second attempt was made. The earlier v0.3.0 attempt reached
configuration but lacked IfcOpenShell. Packaged execution and Linux compilation
remain **not proven**. The CMake path uses an existing native USD build and the
separate converter environment.

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

## Federated document references

The converter translates `IfcDocumentReference` entities associated through
`IfcRelAssociatesDocument` with this delivery contract:

| Field | Meaning |
|---|---|
| `Name` | `aeco:connectedPorts` for a local port, or `aeco:serves` for a local system |
| `Location` | target package IFC basename, for example `cooling.ifc` |
| `Identification` | target port or spatial object's IFC GlobalId |
| `Description` | target absolute USD prim path |

Only the local owner receives the relationship target from `Description`.
There is no foreign prim definition or placeholder, no package lookup, and no
reciprocal opinion authored on another package's port. Compose both deliveries
to resolve reciprocal port links. Same-file port links remain symmetric, and
local system service targets remain intact. Unknown names are ignored;
malformed descriptions warn and are skipped by the converter. See the
[converter mapping](converter.md) for validation and ownership details.

This applies to default reads and `spine=over`, with either geometry argument.
The plugin uses the updated converter unchanged. Converter version `0.3.1`
selects new cache entries, so materializations made before document translation
are not reused.

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
matching Python. A module-scoped temporary fixture prepares immutable base
IFC and twin inputs through the roundtrip's `build_base` helper. Each test copies
those inputs into its own fresh temporary directory and sets its own
`USDAECO_IFC_CACHE`. The XDG-default test removes that override only while using
its private `XDG_CACHE_HOME`; no test reads the user cache. Writers finish and
close saved layers before native reader subprocesses start. The full gate can
reuse its freshly generated IFC as an input. Native
probes register only `usdIfc`; the twin probe starts without any family plugins.
They compare relationship targets, census and world transforms, composition, spatial overs, no-geometry
mode, deterministic fresh-process opens, cache hits and concurrent publication,
error diagnostics and read-only behavior. Without `USD_DEV`, the optional native
tests are explicitly skipped and the existing converter/host gate remains usable.
Per-test JUnit properties carry acceptance evidence to `check.py`; there is no
shared writable test report. Use `--junitxml=<report.xml>` to retain that evidence
when running pytest separately. With native reads enabled, the gate allows
600 seconds for regression tests because each test starts with a private cold
cache; the converter-only budget remains 240 seconds.

If `AECO_DATACENTRE_ROOT` identifies datacentre >=0.5.1 with `dist/full/`, the
suite compares all nine IFC deliveries against their committed twins: each
discipline uses `spine=over`, while shared defines the spatial spine. It compares
every relationship target, census and world transform, counts the 1,008 port
targets unresolved within their individual packages, and reports serves counts.
It also compares `dc.connected.usda` with `dc.usda`. Mesh point/topology hashes
are compared for every mesh except exactly the two `tessellationControlled`
paths declared by the full manifest (near and tangent clash pipes). Those paths
remain included in census, relationship and transform comparisons. Their reader
and twin point counts are recorded. Without that fixture the
full-facility row is **not proven**; synthetic parity does not replace it.

The v0.3.1 acceptance ran the pinned gate with the released base source, then
ran the conditional case separately with the full v0.5.1 source. To repeat that
case, point `AECO_DATACENTRE_ROOT` at the full source and run:

```sh
env -u PYTHONPATH "$PYTHON" -m pytest -q tests/test_file_format.py::test_connected_datacentre_matches_usd_only --junitxml=out/full-facility.xml
```

All nine deliveries passed, including 1,008 cross-package port targets and nine
serves targets. The connected root matches 12,350 world transforms and 3,048
mesh point/topology records. See the [acceptance table](acceptance.md) for the
per-package counts and the two controlled-tessellation exclusions.
