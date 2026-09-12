# IFC integration acceptance

## Public re-pin (v0.2.3)

All 12 direct family inputs use the requested release tags. Their checked
source revisions are recorded beside the refs in `dependencies.json` and the
regenerated example manifest. The supported requirement ranges are unchanged.
The gate now calls the unmodified toolchain v0.3.10 structure lint.

| Acceptance item | Observed result |
|---|---|
| Direct tags and checked source revisions | 12/12 match the requested releases |
| Anonymous public tag lookup | 8/12 direct tags verified; 4 not proven |
| Recursive public tags | 2/2 verified: processing toolchain v0.4.0 and core fixture v0.9.2 |
| Python gate | 101 checks, 0 failed, 0 not run |
| Regression tests | 134 passed, 0 skipped |
| Unmodified structure lint | 29 checks, 0 failed; 14 schema rules inapplicable |
| Core validators | 8 loaded; 0 errors, 2 classification warnings |
| Native regression cases | 21 synthetic, 9 camera, 9 datacentre; all passed |
| Exact clash export | 2,980/2,980 products; 32 IFC classes; 0 failures |
| Roundtrip | 2 elements; 14 driver values; 0 convergence differences |
| Published USD | 10/10 files byte-identical; 13,404 plugin-free prims |
| Source layers, findings and converter | Unchanged; 7 converter source files byte-identical |
| Vanilla PNG | 1280 × 800; 72,634 bytes; fresh plugin-free render |
| Published result | 12 files; 6,445,651 bytes |
| Release metadata | 0.2.3 in library, Python project and source package |
| Previous-organization flake references | 0 |
| Whitespace check | `git diff --check` clean |
| Nix | 1 offline attempt; stopped before evaluation; not proven |

Regenerate with the README environment and
`env -u PYTHONPATH python examples/roundtrip/run.py --publish`, then run
`env -u PYTHONPATH PYTHONPATH=$AECO_CORE_ROOT:$PWD python check.py`.
The gate includes pytest and requires the core validators to import and load.
Source checkouts were frozen at the checked revisions; dependency checkouts
were not modified. [The re-pin receipt](public-repin.json) records before/after
hashes, public lookups and runtime provenance; [the gate receipt](acceptance.json)
records the measured integration and exact-export results.

### Deviations

- Anonymous GitHub lookups of scenarios v0.8.0, cctv v0.5.6, usdSolid v0.1.5
  and usdSolidOcct v0.1.4 requested credentials; their release pages returned
  HTTP 404. The requested tags are retained, but public access to these four
  inputs needs verification before outsider resolution can be claimed.
- The single `nix flake check --offline --no-write-lock-file` attempt used
  local direct and recursive overrides. An archive request for the recursive
  processing toolchain returned HTTP 404; the command ran before that failed
  preparation was handled and stopped at the unavailable override path.
  This local preparation failure proves neither evaluation nor a build.
  No retry was made; online flake resolution remains not proven.
- Exact acceptance used the published usdSolidOcct v0.1.4 runtime, which
  contains usdSolid v0.1.4. Its upstream schema and validator revisions match
  the requested usdSolid v0.1.5 source. The
  [usdSolid v0.1.5 changelog](https://github.com/criad-com/usdSolid/blob/v0.1.5/CHANGELOG.md)
  records the packaging change and blocked native rebuild. A native v0.1.5
  package is not proven by this gate.
- Two freshly rendered PNGs have new bytes. Changed pixels are 0.496% for
  the example and 0.489% for vanilla, with mean absolute RGB differences
  below 0.025/255. All geometry and layer bytes remain identical; the result
  README changes only its data-centre source tag. The
  [data-centre v0.4.9 changelog](https://github.com/criad-com/usdaeco-datacentre/blob/v0.4.9/CHANGELOG.md)
  likewise records byte-identical published stages. Full artifact-byte
  equality is not claimed for sampled PNGs.
- Historical fixture references remain provenance records outside the flake
  inputs. The existing converter parity limit also remains: all three
  authored layer texts match, while the freshly packed semantic crate has a
  different binary encoding. The committed roundtrip crate is byte-identical.

## Public-name baseline (v0.2.2)

Public flake inputs and documentation links now use `github.com/criad-com`.
Toolchain v0.3.8 is the only changed dependency pin. Its unmodified structure
lint passes 29 checks, 0 failed, including S05, S25 and S29; 14 schema-only
rules remain inapplicable to this integration.

The patch replaces 15 public references and leaves zero references to the
previous public organization in tracked files. All 11 other direct pins,
historical fixtures, requirement ranges, seven converter source files and
12 published result files are unchanged. Only the toolchain pin changes in
the example manifest; no result was republished.

| Acceptance item | Observed result |
|---|---|
| Python gate | 101 checks, 0 failed, 0 not run |
| Regression tests | 134 passed, 0 skipped |
| Unmodified toolchain v0.3.8 structure | 29 checks, 0 failed |
| Core validators | 8 loaded; 0 errors, 2 classification warnings |
| Exact clash export | 2,980/2,980 products; 32 IFC classes; 0 failures |
| Roundtrip | 2 elements; 14 driver values; 0 convergence differences |
| Published results | All 12 files unchanged; 13,404 plugin-free prims |
| Public-reference sweep | 0 previous-organization references in tracked files |

Current gate observations are recorded in [dependencies.json](../dependencies.json).
The detailed exact-export evidence below remains the v0.2.0 baseline.

### Deviations

- One offline `nix flake check` attempt with local source overrides failed to
  resolve the uncached transitive OpenUSD input while external networking was
  blocked. Nix remains **NOT PROVEN**; no retry was made.
- The existing S04 compatibility adapter is unchanged. The 29/0 structure
  result above comes from a separate run of the unmodified v0.3.8 lint.

## Exact-export baseline (v0.2.0)

Version 0.2.0 adds an independent exact export pass. The ordinary converter
modules are byte-identical to v0.1.2. The gate retains all 94 integration
checks and adds six full-variant exact checks.

| Acceptance item | Observed result |
|---|---|
| Python gate | 100 checks, 0 failed, 0 not run |
| Regression tests | 134 passed, 0 skipped |
| Structure with native-name compatibility | 28 checks, 0 failed; 14 schema-only rules inapplicable |
| Exact clash export | 2,980/2,980 meshable products; 32 IFC classes; 0 failures |
| Exact solid validity and tolerances | 2,980/2,980 valid solids with positive kernel tolerances |
| Twin volume and area budgets | 2,980/2,980 within tolerance |
| Mapped products | 45 occurrences, 3 representation prototypes |
| Material subsets | 3,334 exact subsets plus corresponding twin subsets |
| Retained native cases | 21/21 synthetic; 9/9 camera; 9/9 datacentre |
| Core validation | 8 loaded; 0 errors, 2 source classification warnings |
| Published base census | 2,954 elements; 33 spaces; 0 unparented |
| Default converter source | All converter module bytes unchanged from v0.1.2 |
| Published converter parity | 2/3 files byte-identical; 3/3 authored texts identical |
| Roundtrip | 2 elements; 14 driver values; 0 convergence differences |
| S27 relocation | 13,404 prims; no external assets; 0 composition errors |
| S28 stock render | 1280 × 800; 72,626 bytes; no family plugins |
| Complete result | 12 files; 6,445,643 / 10,000,000 bytes |
| Crate / largest USDA | 1,738,843 / 1,263,170 bytes |
| Nix | 1 attempt; unresolved nested toolchain input; NOT PROVEN |

The current [machine-readable evidence](acceptance.json) records each native case,
converter comparison, measured roundtrip and exact per-class census. All
inputs use the stated release tags. The data-centre IFC is generated in
scratch space; no dependency checkout is modified.

The companion solid example selects all 109 office-footprint products from
the same clash variant, validates material partitions and native schemas,
measures 42 wall thicknesses and 6 pipe diameters, and publishes the three
planted clearance cases. Its separate B7 test mutes the exact layer and
renders the unchanged twins without plugins.

| IFC class | Meshable | Exact | Failed |
|---|---:|---:|---:|
| IfcAirTerminal | 4 | 4 | 0 |
| IfcAlarm | 12 | 12 | 0 |
| IfcAudioVisualAppliance | 45 | 45 | 0 |
| IfcBeam | 4 | 4 | 0 |
| IfcBuildingElementProxy | 2 | 2 | 0 |
| IfcCableCarrierFitting | 165 | 165 | 0 |
| IfcCableCarrierSegment | 339 | 339 | 0 |
| IfcCableFitting | 401 | 401 | 0 |
| IfcCableSegment | 797 | 797 | 0 |
| IfcChiller | 2 | 2 | 0 |
| IfcColumn | 80 | 80 | 0 |
| IfcCoolingTower | 4 | 4 | 0 |
| IfcCovering | 2 | 2 | 0 |
| IfcDoor | 41 | 41 | 0 |
| IfcElectricDistributionBoard | 26 | 26 | 0 |
| IfcElectricFlowStorageDevice | 16 | 16 | 0 |
| IfcElectricGenerator | 2 | 2 | 0 |
| IfcElementAssembly | 2 | 2 | 0 |
| IfcFurniture | 170 | 170 | 0 |
| IfcHeatExchanger | 1 | 1 | 0 |
| IfcLightFixture | 6 | 6 | 0 |
| IfcPipeFitting | 238 | 238 | 0 |
| IfcPipeSegment | 482 | 482 | 0 |
| IfcPump | 6 | 6 | 0 |
| IfcSanitaryTerminal | 2 | 2 | 0 |
| IfcSensor | 11 | 11 | 0 |
| IfcSlab | 9 | 9 | 0 |
| IfcStair | 1 | 1 | 0 |
| IfcTank | 3 | 3 | 0 |
| IfcTransformer | 2 | 2 | 0 |
| IfcUnitaryEquipment | 13 | 13 | 0 |
| IfcWall | 92 | 92 | 0 |

### Baseline deviations

- IfcOpenShell 0.8.5 exposes BRep text through SERIALIZED iterator output,
  the supported equivalent of USE_BREP_DATA. A small dynamically linked
  adapter supplies BRep reading, surface inspection and edge extraction;
  all kernel operations run in the bridge's separate Python/USD ABI.
- Unmodified toolchain v0.3.2 gives **27/28**: S04 accepts lowercase repository
  names only. The compatibility adapter permits `usdSolid` and `usdSolidOcct`,
  then applies every original pin/range check. Its rejection behavior has
  seeded tests. The reported 28/0 uses that adapter, not unmodified lint.
- Core, axis, sync and data-centre checkouts advanced during the work. Frozen
  tagged source archives supplied the requested versions. Source revisions
  and the native renderer version are recorded in dependency observations.
- The semantic crate's binary encoding differs from the published base;
  all authored content matches. No stamp or authored opinion is removed
  during comparison. This retained parity limitation is unchanged.
- One offline Nix attempt with direct local overrides failed resolving the
  nested aeco-toolchain revision (HTTP 404). It was not retried. Nix execution,
  Linux execution and wheel installation remain NOT PROVEN. Tests use source
  paths and do not require setuptools.

## What remains

Exact sync read-back, a driver-to-solid evaluator, direct exact-body imaging
and arbitrary production-IFC coverage remain outside this release. Compound
build-up/type-schedule equivalence is not proven by the fixture. Live
integration execution remains NOT RUN; this release adds no such claim.
