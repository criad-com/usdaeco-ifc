# usdaeco-ifc — IFC in, IFC out

## Use case

Convert IFC4/IFC4X3 into core USD. Edit supported drivers in a sync intent layer,
let the IFC host solve them, then export and reimport for convergence. See the
[use case](docs/usecase.md) and [converter passes](docs/converter.md).

## The schema on an index card

| Component | Contract |
|---|---|
| Converter | spatial structure, identity, classification, types, groups, ports and tessellated geometry |
| Optional `usdIfc` format | read-only IFC layers, cached conversion and flattened materialization |
| IFC host | native transactions, closure readback, validation and export |
| Optional exact export | evaluated OCCT BReps, material subsets, mapped prototypes and Mesh twins in separate layers |
| Integration | no new schema; core and sync retain ownership of their namespaces |
| `aeco_sync.hosts` entry point | `ifc = usdaeco_ifc.host:IfcHost` |

## The example

Open [result/example.usdc](examples/roundtrip/result/example.usdc) in stock USD,
with no sibling checkouts or family plugins. It contains the final composed
stage after IFC export, reimport and convergence. The diffable intent, native
result, diagnostics, derived and presentation layers are in
[result/layers](examples/roundtrip/result/layers).

To regenerate, run `env -u PYTHONPATH python examples/roundtrip/run.py --publish`
in the environment below. The runner composes the pinned base USD of
`demo-datacentre-01`, generates its corresponding IFC in scratch space, increases
one wall height and one pipe length, then exports and reimports. The pipe uses
the explicit `disconnect` policy. See [the example](examples/roundtrip/README.md).

The stock USD render includes `proxy,render`. The camera fits the edited bodies;
the gate verifies convergence, framing, foreground coverage, relocation and
fresh-result equality through S27/S28. See [acceptance](docs/acceptance.md).

![Final reimport rendered with stock USD](examples/roundtrip/result/vanilla.png)

## Build and check

### Optional IFC file format

`usdIfc` opens `.ifc` directly through `Usd.Stage.Open` or a USD sublayer.
It is a read-only native plugin; build it against the OpenUSD used by the
consumer. The converter, host and roundtrip example need no file-format plugin.
See [file-format setup, cache and arguments](docs/file-format.md).

```sh
export USD_DEV="$USD_DEV_OUTPUT"
./build.sh
export USDAECO_IFC_SOURCE_PYTHON="$PYTHON"
export USDAECO_IFC_PYTHON="$PWD/tools/ifc-python"
export CORE_PLUGIN_DIR="$AECO_CORE_ROOT/usdAeco"
export AXIS_PLUGIN_DIR="$AECO_AXIS_ROOT/usdAecoAxis"
export PXR_PLUGINPATH_NAME="$CORE_PLUGIN_DIR:$PWD/out/plugins/usdIfc/resources"
env -u PYTHONPATH "$PYTHON" -m pytest -q tests/test_file_format.py
```

`USD_DEV_OUTPUT` is an existing toolchain `usd-dev` output and `PYTHON` is
the converter environment described below. The source launcher adds explicit
paths without installing packages. Tests launch the matching native USD Python
for plugin reads. `nix build .#usdIfc --no-write-lock-file` is the packaged route;
its converter dependency availability is platform-dependent.

### Converter and host

Use a Python environment with usd-core 26.8, IfcOpenShell 0.8.5, numpy, pytest,
pydantic 2, pyyaml and Pillow, plus the toolchain's usdrecord/Embree renderer. Put the
pinned core, axis, sync, toolchain, cctv, buildup, wall, pipe, datacentre and scenarios checkouts beside
this repo. Alternate source locations can be set explicitly:

```sh
export AECO_SYNC_ROOT=../usdaeco-sync
export LC_ALL=C
export AECO_CORE_ROOT=../usdaeco-core
export AECO_AXIS_ROOT=../usdaeco-axis
export AECO_CORE=$AECO_CORE_ROOT
export CORE_PLUGIN_DIR=$AECO_CORE_ROOT/usdAeco
export AXIS_PLUGIN_DIR=$AECO_AXIS_ROOT/usdAecoAxis
export PXR_PLUGINPATH_NAME=$CORE_PLUGIN_DIR:$AXIS_PLUGIN_DIR
export USD_SOLID_OCCT_RUNTIME=../usdSolidOcct/result-runtime
export TOOLCHAIN_DIR=../usdaeco-toolchain
export AECO_DATACENTRE_ROOT=../usdaeco-datacentre
export AECO_SCENARIOS_ROOT=../usdaeco-scenarios
export AECO_CCTV_ROOT=../usdaeco-cctv
export AECO_BUILDUP_ROOT=../usdaeco-buildup
export AECO_WALL_ROOT=../usdaeco-wall
export AECO_PIPE_ROOT=../usdaeco-pipe
env -u PYTHONPATH python check.py
env -u PYTHONPATH python -m pytest -q
env -u PYTHONPATH python examples/roundtrip/run.py --publish
nix flake check --no-write-lock-file
```

`check.py` prints `N checks, M failed`. Source tests add explicit paths and
materialize entry-point metadata from pyproject.toml; they do not install into
the shared interpreter. Installed usage is `aeco-ifc convert model.ifc -o
model.usda` or `python -m usdaeco_ifc.convert model.ifc -o model.usda`, followed
by `aeco-ifc init model.ifc model.usda --directory session`. The source form is
`python -c 'import bootstrap; from usdaeco_ifc.cli import main; main()' convert
model.ifc -o model.usda`. Keep geometry extraction in a fresh process before
importing USD. With the exact runtime configured, source checks select its
matching stock usdrecord automatically; `USDRECORD` can override that choice.

Use the tagged sources in `dependencies.json`: core v0.9.5, axis v0.1.5,
sync v0.5.5, toolchain v0.3.10 and datacentre v0.4.9. Select a frozen release
checkout with the environment variables above if a shared checkout advances.

The optional exact command uses a separate native Python/USD ABI:

```sh
export USD_SOLID_OCCT_RUNTIME=../usdSolidOcct/result-runtime
env -u PYTHONPATH python -c 'import bootstrap; from usdaeco_ifc.exact import main; raise SystemExit(main())' input.ifc --stage model.usda --out exact
```

Installed forms are `aeco-ifc-exact` and `python -m usdaeco_ifc.exact`.
Compose `exact/exact.usda` and `exact/twins.usda` above the original model.
The default converter is unchanged. Exact export reports every selected
meshable product by IFC class, including failures, and exits nonzero if any
body fails. See [the exact route](docs/exact.md) for scope, ABI and fidelity.

Flakes name versioned GitHub inputs. Anonymous access to four tagged inputs
remains unproven in this release; see [acceptance](docs/acceptance.md). A local mirror can use
`--override-input <name> path:../<repo>` for every direct input; the toolchain
also documents nested overrides. Nix input resolution and wheel construction
remain unproven in the measured environment; see [acceptance](docs/acceptance.md).

## Family

Requires `usdAeco >=0.9,<1.0`, `usdAecoAxis >=0.1,<0.2` and
`usdAecoSync >=0.5,<0.6`. Exact tested
references and source observations are in [dependencies.json](dependencies.json).
This integration uses datacentre v0.4.9, cctv v0.5.6 and buildup v0.2.5.
Its 21 synthetic cases come from the v0.3.7 family gate. The [family board](https://github.com/criad-com/usdaeco-board)
reads these pins and the example manifest.

## Layout

`tools/usdaeco_ifc/convert/` contains the reference converter;
`tools/usdaeco_ifc/host.py` implements the host interface. `scenarios/` and
`tests/` preserve IFC-specific regression cases. `docs/` explains the mapping
and limits. `examples/roundtrip/` carries inputs, findings, render and provenance.
`usdIfc/` contains the native file-format plugin; `build.sh` installs it under `out/`.
S01–S05 and S20–S29 apply; S06–S19 are inapplicable because this package has no schema.

## Status

Version 0.3.1: **104 checks, 0 failed** against the pinned source (175 tests
passed, 1 conditional case skipped). That full-facility case then **passed
separately** against datacentre v0.5.1: all **176 cases** are verified across
the two fixture configurations.

All nine federated deliveries and the connected root match their twins' census,
relationships and transforms: **6,052 port targets**, including **1,008 cross-package
targets**, **9 serves targets**, and **12,350 world transforms**. Mesh points and
topology match for 3,048 meshes; exactly two manifest-listed controlled
meshes are excluded from that comparison. The base still matches 6,048 port
and 9 serves targets. All 12 published roundtrip result files are unchanged.
Native tests use private caches; two formerly flaky cases also pass during an
overlapping pytest run. Nix packaging remains not proven. See the
[file-format receipt](docs/file-format-acceptance.json) and
[acceptance deviations](docs/acceptance.md).
The optional exact command preserves the existing
converter, native cases and final-reimport publication. The base census is
2,954 elements, 33 spaces and zero unparented elements. Exact export also
covers the clash variant; measurements and current gate totals are in
[acceptance](docs/acceptance.md). Source tests require no installed package
or setuptools. Live integration execution and wheel construction remain
not proven.

## Licence

[MIT](LICENSE). Copyright (c) 2026 Criad.

Runtime dependencies keep their own licences; third-party code is not vendored.

| Dependency | Licence and use |
|---|---|
| OpenUSD | Apache-2.0-style TOST; USD runtime and renderer |
| numpy / jinja2 | BSD; arrays and shared toolchain templates |
| PyYAML / pydantic | MIT; demo input parsing and validation |
| Pillow | HPND; image validation |
| IfcOpenShell | LGPL-3.0; imported only |
| OCCT | LGPL-2.1; dynamically linked through IfcOpenShell and the optional bridge |
| OpenSSL | Apache-2.0; optional Linux plugin SHA-256 implementation |
