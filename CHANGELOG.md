# Changelog

## 0.3.1

- Translate federated `IfcDocumentReference` associations into local
  `aeco:connectedPorts` and `aeco:serves` relationship targets in both spine
  modes. Preserve local links and foreign ownership; warn on malformed paths.
- Bump the converter version to invalidate cached IFC materializations.
- Give native file-format tests private temporary deliveries and caches;
  carry acceptance evidence through per-test JUnit records.
- Compare relationship targets as well as census and transforms, including
  every full-facility delivery when datacentre >=0.5.1 is available.

- Verify 104 checks, 0 failed, 1 not run; 175 tests passed, 1 skipped.
  Base parity includes 6,048 connected-port targets and 9 serves targets.
  All 12 published roundtrip files remain byte-identical. Record the full-gate
  timeout retry and unresolved Nix input.
- Verify the previously skipped full-facility case separately against v0.5.1:
  all nine deliveries match, including 1,008 cross-package port targets and
  9 serves targets. Compare 12,350 world transforms and 3,048 mesh point/topology
  records, excluding exactly the two disclosed controlled meshes.

## 0.3.0

- Add the read-only `usdIfc` Sdf file-format plugin, content-addressed cache,
  `spine` and `geometry` arguments, CMake build and optional Nix package.
- Keep conversion in a separate interpreter and transfer one flattened layer
  into USD; retain the IFC delivery and its USD twin as separate entry points.
- Repair overlay spatial ownership after descendant authoring and retain
  discipline types; shared packages own space extents.

- Verify 104 checks, 0 failed, 1 not run; 147 tests passed, 1 skipped.
  Preserve all 12 published result files. Record Nix dependency/network limits,
  the resource-metadata correction and unavailable connected full fixture.

## 0.2.3

- public re-pin: toolchain v0.3.10, core v0.9.5, axis v0.1.5, sync v0.5.5,
  datacentre v0.4.9, scenarios v0.8.0, cctv v0.5.6, buildup/wall/pipe v0.2.5,
  usdSolid v0.1.5 and usdSolidOcct v0.1.4.
- Record checked tag revisions and retain all supported requirement ranges.
- Use the unmodified structure lint, including release-tag and version checks.
- Republish the roundtrip: all ten USD files remain byte-identical; refresh
  source-pin provenance and two sampled PNGs.
- Verify 101 checks, 0 failed, 0 not run; 134 tests passed; unmodified
  toolchain v0.3.10 structure lint: 29 checks, 0 failed.
- Record four unproven public tag lookups, the single failed offline Nix
  attempt and the bundled native schema version separately.

## 0.2.2

- public names → github.com/criad-com.
- Pin toolchain v0.3.8; preserve all other dependency pins.
- Update the example manifest toolchain pin without republishing results.
- Verify 101 checks, 0 failed, 0 not run; 134 tests passed; unmodified
  toolchain v0.3.8 structure lint: 29 checks, 0 failed.

## 0.2.1

- Re-pin to train aeco-0.7.0; preserve requirement ranges and historical fixtures.
- Refresh roundtrip source provenance for datacentre v0.4.5; all USD files,
  renders and findings remain unchanged (13,404 prims; 2,954 elements; 33 spaces).
- Verify 100 checks, 0 failed, 0 not run; 134 tests passed; unmodified
  toolchain v0.3.5 structure lint: 28 checks, 0 failed.

## 0.2.0

- Add the optional exact export command and ABI-isolated OCCT adapter.
- Export all 2,980 clash-variant products across 32 classes as valid exact bodies,
  with proxy twins, material subsets and mapped representation prototypes.
- Keep the 94 integration checks and add six exact acceptance rows; 134 tests pass.
- Pin core v0.9.2, axis v0.1.1, toolchain v0.3.2 and datacentre v0.4.2.
- Refresh the final-reimport publication and document native runtime and lint limits.

## 0.1.2

- Publish the final reimported roundtrip as a standalone flattened crate,
  diffable review layers and a stock USD render, with S27/S28 checks.
- Preserve original intent and native results separately from final reimport
  opinions; omit live session bindings and transaction metadata from review.
- Require all eight core validators to import and load through UsdValidation.
- Pin toolchain v0.3.1 and sync v0.5.1; record the v0.3.2 licence-lint override.
- Adopt MIT and document runtime dependency licences.

## 0.1.1

- Re-pin conversion and native integration to core v0.9.1 and axis v0.1.0.
- Preserve the converter stamp and load only its core and axis schemas.
- Compare the base conversion with datacentre v0.4.1 published layers, using
  the publisher's binary semantic-layer format, and run the core validators.
- Preserve the native cases, roundtrip edits and rendered example.

## 0.1.0

- Move the core v0.8.4 IFC converter without changing its emitted layers or stamp.
- Move the IFC native host and operation helpers, register the `ifc` entry point,
  and preserve the existing synthetic and data-centre cases.
- Support bounded edits on direct exported sweeps used by the demo facility.
- Add an IFC roundtrip, USD/native convergence report and integration gate.
- Render the edited bodies with toolchain v0.2.1, a camera fitted to their bounds,
  and a normalized foreground check requiring at least 2% image coverage.
- Document the future OCCT exact pass without installing an unimplemented command.
