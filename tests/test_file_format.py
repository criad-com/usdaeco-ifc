"""IFC file-format contract; native consumer and converter stay in separate ABIs."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from usdaeco_ifc.acceptance import build_base
from usdaeco_ifc.runtime import python
from file_format_support import ROOT, SNAPSHOT, environment, native, snapshot


def test_file_format_descriptor():
    descriptor = json.loads((ROOT / 'usdIfc/plugInfo.json').read_text())['Plugins'][0]
    info = descriptor['Info']['Types']['UsdIfcFileFormat']
    assert descriptor['Name'] == 'usdIfc'
    assert info['bases'] == ['SdfFileFormat']
    assert info['formatId'] == 'ifc' and info['extensions'] == ['ifc']
    assert info['primary'] and info['target'] == 'usd'
    assert info['supportsWriting'] is info['supportsEditing'] is False


@pytest.mark.parametrize('spine', ['def', 'over'])
def test_document_references_match_saved_twin(tmp_path, format_env, file_format_evidence, spine):
    from document_fixtures import (FOREIGN_PORT, FOREIGN_SPACE, LOCAL_PORTS, SYSTEM,
                                   associate, delivery)
    model, ports, system, _ = delivery()
    associate(model, [system], 'aeco:serves', FOREIGN_SPACE)
    associate(model, ports, 'ignored', 'not a path')
    associate(model, ports, 'aeco:connectedPorts', 'relative/Port')
    source = tmp_path / 'delivery.ifc'
    model.write(str(source))
    del model, ports, system
    twin = source.with_suffix('.usda')
    args = ['-m', 'usdaeco_ifc.convert', str(source), '-o', str(twin)]
    if spine == 'over':
        args.append('--overlay-spine')
    # Wait for the writer process to exit, closing all saved layer handles.
    conversion = python(args, check=True, capture_output=True, text=True, timeout=180)
    assert 'Description must be an absolute USD prim path' in conversion.stderr
    actual = snapshot(str(source) + ':SDF_FORMAT_ARGS:spine=' + spine, format_env)
    reference = snapshot(twin, environment(tmp_path / 'vanilla-cache', plugins=False))
    for key in ('census', 'transforms', 'relationships'):
        assert actual[key] == reference[key]
    assert actual['census']['ports'] == 2
    assert actual['relationships'][LOCAL_PORTS[0]]['aeco:connectedPorts'] == [LOCAL_PORTS[1], FOREIGN_PORT]
    assert actual['relationships'][LOCAL_PORTS[1]]['aeco:connectedPorts'] == [LOCAL_PORTS[0]]
    assert actual['relationships'][SYSTEM]['aeco:serves'] == ['/Demo/Building', FOREIGN_SPACE]
    assert actual['unresolvedRelationshipCounts'] == {'aeco:connectedPorts': 1, 'aeco:serves': 1}
    file_format_evidence['documentReferences' + spine.title()] = actual['relationshipCounts']


@pytest.fixture(scope='module')
def format_runtime():
    if not os.environ.get('USD_DEV'):
        pytest.skip('not proven: set USD_DEV and build usdIfc for native file-format tests')
    resources = Path(os.environ.get('USD_IFC_PLUGIN_DIR', ROOT / 'out/plugins/usdIfc/resources'))
    assert (resources / 'plugInfo.json').is_file(), 'Run build.sh before native tests'


@pytest.fixture(scope='module')
def format_inputs(tmp_path_factory, format_runtime):
    # Prepare immutable inputs once; every test copies them before opening USD.
    work = tmp_path_factory.mktemp('file-format-inputs')
    source = os.environ.get('AECO_CONTRACT_IFC')
    if not source:
        print('== stage: regenerate roundtrip IFC fixture', flush=True)
        source, _ = build_base(work / 'base')
    delivered = work / "delivery with ' spaces"
    delivered.mkdir()
    source_copy = delivered / "model ' quoted.ifc"
    shutil.copyfile(source, source_copy)
    twin = source_copy.with_suffix('.usda')
    print('== stage: convert IFC twin and open native format', flush=True)
    python(['-m', 'usdaeco_ifc.convert', str(source_copy), '-o', str(twin)],
           check=True, capture_output=True, text=True, timeout=180)
    return delivered


@pytest.fixture
def file_format_evidence(record_property):
    evidence = {}
    yield evidence
    record_property('fileFormat', json.dumps(evidence))


@pytest.fixture
def format_env(tmp_path, format_runtime):
    return environment(tmp_path / 'cache')


@pytest.fixture
def format_case(tmp_path, format_inputs, format_env, file_format_evidence):
    work = tmp_path
    delivered = work / "delivery with ' spaces"
    shutil.copytree(format_inputs, delivered)
    source_copy = delivered / "model ' quoted.ifc"
    twin = source_copy.with_suffix('.usda')
    env = format_env
    first = snapshot(source_copy, env)
    reference = snapshot(twin, env)
    file_format_evidence.update(census=first['census'], flattenedHash=first['hash'],
                                relationshipCounts=first['relationshipCounts'])
    return dict(work=work, source=source_copy, twin=twin, env=env, first=first,
                reference=reference, evidence=file_format_evidence)


def test_ifc_stage_matches_converter_census_and_world_transforms(format_case):
    case = format_case
    assert case['first']['census'] == case['reference']['census']
    assert case['first']['transforms'] == case['reference']['transforms']
    assert case['first']['relationships'] == case['reference']['relationships']
    assert all(case['first']['census'][k] > 0 for k in ('spatial','elements','types','systems','ports','meshes'))
    assert case['first']['sublayers'] == []
    case['evidence']['worldTransformsCompared'] = len(case['first']['transforms'])


def test_usda_root_sublayers_ifc(format_case):
    case = format_case
    root = case['source'].parent / 'connected.usda'
    # Keep the root header from the twin: fallbacks belong on each entry point.
    code = '''
from pxr import Sdf
root = Sdf.Layer.CreateNew(sys.argv[1])
root.TransferContent(Sdf.Layer.FindOrOpen(sys.argv[2]))
root.subLayerPaths = [sys.argv[3]]
root.Save()
'''
    native(code, root, case['twin'], case['source'].name, env=case['env'])
    actual = snapshot(root, case['env'])
    assert actual['census'] == case['reference']['census']
    assert actual['transforms'] == case['reference']['transforms']


@pytest.mark.parametrize('geometry', ['0', '1'])
def test_overlay_spine_and_geometry_arguments(format_case, geometry):
    case = format_case
    output = case['work'] / ('overlay-' + geometry + '.usda')
    args = ['-m', 'usdaeco_ifc.convert', str(case['source']), '-o', str(output), '--overlay-spine']
    if geometry == '0': args.append('--no-geometry')
    python(args, check=True, capture_output=True, text=True, timeout=180)
    identifier = str(case['source']) + ':SDF_FORMAT_ARGS:geometry=' + geometry + '&spine=over'
    code = SNAPSHOT + '''
layer = Sdf.Layer.FindOrOpen(sys.argv[1])
twin = Usd.Stage.Open(sys.argv[2])
paths = json.loads(sys.argv[3])
for path in paths:
    spec = layer.GetPrimAtPath(path)
    assert spec is not None and spec.specifier == Sdf.SpecifierOver, path
assert not layer.subLayerPaths
actual = snapshot(sys.argv[1])
reference = snapshot(sys.argv[2])
assert actual['census'] == reference['census']
assert actual['transforms'] == reference['transforms']
assert actual['relationships'] == reference['relationships']
defined = snapshot(sys.argv[4])
for key in ('elements', 'types', 'systems', 'ports'):
    assert actual['census'][key] == defined['census'][key], key
root = Sdf.Layer.CreateAnonymous('federated.usda')
root.TransferContent(Sdf.Layer.FindOrOpen(sys.argv[4]))
root.subLayerPaths = [sys.argv[1], sys.argv[4]]
stage = Usd.Stage.Open(root)
assert stage and not stage.GetCompositionErrors()
assert len(list(stage.Traverse())) > 0
if sys.argv[5] == '0':
    overlay = Usd.Stage.Open(layer)
    assert not any(p.IsA(UsdGeom.Gprim) for p in overlay.TraverseAll())
print(json.dumps(dict(spatialOvers=len(paths), geometry=sys.argv[5], census=actual['census'])))
'''
    paths = [*case['reference']['spatialPaths'], '/demo_datacentre_01']
    result = native(code, identifier, output, json.dumps(paths), case['twin'], geometry, env=case['env'])
    case['evidence']['overlay' + geometry] = json.loads(result.stdout)


def test_geometry_zero_and_default_arguments(format_case):
    case = format_case
    actual = snapshot(str(case['source']) + ':SDF_FORMAT_ARGS:geometry=0', case['env'])
    assert actual['census']['gprims'] == 0
    for key in ('spatial', 'elements', 'types', 'systems', 'ports'):
        assert actual['census'][key] == case['first']['census'][key]
    assert snapshot(str(case['source']) + ':SDF_FORMAT_ARGS:geometry=1&spine=def', case['env']) == case['first']


def test_fresh_open_cache_hit_is_deterministic(format_case):
    case = format_case
    cache = Path(case['env']['USDAECO_IFC_CACHE'])
    before = {str(p.relative_to(cache)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
              for p in cache.rglob('*') if p.is_file() and p.suffix != '.lock'}
    second = snapshot(case['source'], case['env'])
    after = {str(p.relative_to(cache)): (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
             for p in cache.rglob('*') if p.is_file() and p.suffix != '.lock'}
    assert before and before == after
    assert second['hash'] == case['first']['hash']
    # Fail any converter invocation while allowing its version query. The
    # second process must really use disk cache, not the Sdf layer registry.
    launcher = case['work'] / 'cached-python'
    import shlex
    launcher.write_text('#!/bin/sh\nif [ "$1" = "-m" ]; then exit 99; fi\nexec ' +
                        shlex.quote(case['env']['USDAECO_IFC_PYTHON']) + ' "$@"\n')
    launcher.chmod(0o755)
    env = dict(case['env'], USDAECO_IFC_PYTHON=str(launcher))
    assert snapshot(case['source'], env)['hash'] == second['hash']
    case['evidence']['cacheHit'] = True


def test_cache_key_hashes_bytes_arguments_and_converter_version(format_case):
    from usdaeco_ifc import __version__
    case = format_case
    source_hash = hashlib.sha256(case['source'].read_bytes()).hexdigest()
    cache = Path(case['env']['USDAECO_IFC_CACHE'])
    for spine, geometry in [('def','1'), ('def','0'), ('over','0'), ('over','1')]:
        snapshot(str(case['source']) + ':SDF_FORMAT_ARGS:spine=' + spine + '&geometry=' + geometry, case['env'])
        key = hashlib.sha256(('usdIfc-cache-v1\n' + source_hash + '\nspine=' + spine +
                              '\ngeometry=' + geometry + '\nconverter=' + __version__ + '\n').encode()).hexdigest()
        assert (cache / key / 'flattened.usdc').is_file()
    changed = case['work'] / 'changed.ifc'
    changed.write_bytes(case['source'].read_bytes() + b'\n')
    assert snapshot(str(changed) + ':SDF_FORMAT_ARGS:geometry=0', case['env'])['census']['elements'] > 0
    assert len(list(cache.glob('*/flattened.usdc'))) == 5
    import shlex
    launcher = case['work'] / 'versioned-python'
    launcher.write_text('#!/bin/sh\nif [ "$1" = "-c" ]; then echo 9.0.0; exit 0; fi\nexec ' +
                        shlex.quote(case['env']['USDAECO_IFC_PYTHON']) + ' "$@"\n')
    launcher.chmod(0o755)
    env = dict(case['env'], USDAECO_IFC_PYTHON=str(launcher))
    actual = snapshot(str(case['source']) + ':SDF_FORMAT_ARGS:geometry=0', env)
    assert actual['census']['elements'] == case['first']['census']['elements']
    key = hashlib.sha256(('usdIfc-cache-v1\n' + source_hash +
                          '\nspine=def\ngeometry=0\nconverter=9.0.0\n').encode()).hexdigest()
    assert (cache / key / 'flattened.usdc').is_file()


def test_concurrent_cache_publication(format_case):
    case = format_case
    env = dict(case['env'], USDAECO_IFC_CACHE=str(case['work'] / 'concurrent-cache'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(snapshot, str(case['source']) + ':SDF_FORMAT_ARGS:geometry=0', env) for _ in range(2)]
        results = [f.result() for f in futures]
    assert results[0] == results[1]
    assert len(list(Path(env['USDAECO_IFC_CACHE']).glob('*/flattened.usdc'))) == 1
    assert not list(Path(env['USDAECO_IFC_CACHE']).glob('*.tmp-*'))


def test_xdg_cache_default_and_explicit_override(format_case):
    case = format_case
    xdg = case['work'] / 'xdg'
    shutil.copytree(case['env']['USDAECO_IFC_CACHE'], xdg / 'usdaeco-ifc')
    env = dict(case['env'], XDG_CACHE_HOME=str(xdg))
    env.pop('USDAECO_IFC_CACHE')
    assert snapshot(case['source'], env)['hash'] == case['first']['hash']
    unused = case['work'] / 'unused-xdg'
    env = dict(case['env'], XDG_CACHE_HOME=str(unused))
    assert snapshot(case['source'], env)['hash'] == case['first']['hash']
    assert not unused.exists()


def test_errors_include_stderr_and_reject_invalid_args(format_case):
    case = format_case
    for args in ('spine=bad', 'geometry=2', 'unknown=1'):
        result = native('from pxr import Usd;Usd.Stage.Open(sys.argv[1])',
                        str(case['source']) + ':SDF_FORMAT_ARGS:' + args, env=case['env'], check=False)
        assert result.returncode != 0 and 'usdIfc:' in result.stderr
    bad = case['work'] / 'bad.ifc'
    bad.write_text('ISO-10303-21;\ninvalid\n')
    result = native('from pxr import Usd;Usd.Stage.Open(sys.argv[1])', bad, env=case['env'], check=False)
    assert result.returncode != 0
    assert 'converter failed' in result.stderr and 'Traceback' in result.stderr
    assert not list(Path(case['env']['USDAECO_IFC_CACHE']).glob('*.tmp-*'))


def test_ifc_is_read_only(format_case):
    case = format_case
    before = case['source'].read_bytes()
    code = '''
from pxr import Sdf, Tf
layer = Sdf.Layer.FindOrOpen(sys.argv[1])
assert not Sdf.FileFormat.FormatSupportsWriting('ifc')
assert not Sdf.FileFormat.FormatSupportsEditing('ifc')
try:
    result = layer.Export(sys.argv[1])
except Tf.ErrorException:
    result = False
assert not result
assert layer.ExportToString().startswith('#usda 1.0')
'''
    native(code, case['source'], env=case['env'])
    assert case['source'].read_bytes() == before


def test_usda_twin_opens_without_any_plugins(format_case):
    case = format_case
    env = environment(case['work'] / 'vanilla-cache', plugins=False)
    native('from pxr import Sdf,Usd;assert not Sdf.FileFormat.FindByExtension("ifc");'
           'assert Usd.SchemaRegistry.GetTypeFromSchemaTypeName("AecoElementAPI").isUnknown', env=env)
    actual = snapshot(case['twin'], env)
    assert actual['census'] == case['reference']['census']
    assert actual['transforms'] == case['reference']['transforms']


def test_connected_datacentre_matches_usd_only(tmp_path, format_env, file_format_evidence):
    root = os.environ.get('AECO_DATACENTRE_ROOT')
    connected = Path(root) / 'dist/full/dc.connected.usda' if root else None
    version = json.loads((Path(root) / 'library.json').read_text())['version'] if root else '0.0.0'
    if (connected is None or not connected.is_file()
            or tuple(map(int, version.split('-')[0].split('.'))) < (0, 5, 1)):
        file_format_evidence['fullFacility'] = 'not proven: dist/full from datacentre >=0.5.1 unavailable'
        pytest.skip(file_format_evidence['fullFacility'])
    vanilla = environment(tmp_path / 'vanilla-cache', plugins=False)
    manifest = json.loads(connected.with_name('dc.manifest.json').read_text())
    controlled = manifest['tessellationControlled']
    assert len(controlled) == 2
    assert {row['path'].split('/')[-2] for row in controlled} == {'pipe_clash_near', 'pipe_clash_tangent'}
    assert all(row['delivery'] == 'cooling' for row in controlled)

    def compare_meshes(actual, twin, exclusions):
        assert actual['meshes'].keys() == twin['meshes'].keys()
        excluded = {row['path'] for row in exclusions}
        assert excluded <= actual['meshes'].keys()
        for path in actual['meshes'].keys() - excluded:
            assert actual['meshes'][path] == twin['meshes'][path], path
        for row in exclusions:
            assert twin['meshes'][row['path']]['points'] == row['twinPointCount']
        return len(actual['meshes']) - len(excluded)

    packages = {}
    deliveries = sorted(connected.parent.glob('*.ifc'))
    assert len(deliveries) == 9, 'Expected shared spine and eight discipline deliveries'
    for source in deliveries:
        args = '' if source.stem == 'shared' else ':SDF_FORMAT_ARGS:spine=over'
        actual = snapshot(str(source) + args, format_env, meshes=True)
        twin = snapshot(source.with_suffix('.usda'), vanilla, meshes=True)
        assert actual['census'] == twin['census'], source.name
        assert actual['transforms'] == twin['transforms'], source.name
        assert actual['relationships'] == twin['relationships'], source.name
        packages[source.stem] = dict(census=actual['census'],
                                    worldTransformsCompared=len(actual['transforms']),
                                    meshesCompared=compare_meshes(actual, twin, [row for row in controlled
                                                                             if row['delivery'] == source.stem]),
                                    relationshipCounts=actual['relationshipCounts'],
                                    unresolvedRelationshipCounts=actual['unresolvedRelationshipCounts'])
    cross_package_targets = sum(p['unresolvedRelationshipCounts']['aeco:connectedPorts']
                                for p in packages.values())
    assert cross_package_targets == 1008
    actual = snapshot(connected, format_env, meshes=True)
    twin = snapshot(connected.with_name('dc.usda'), vanilla, meshes=True)
    assert actual['census'] == twin['census']
    assert actual['transforms'] == twin['transforms']
    assert actual['relationships'] == twin['relationships']
    assert actual['relationshipCounts']['aeco:serves'] == 9
    file_format_evidence['fullFacility'] = dict(packages=packages, census=actual['census'],
                                               crossPackagePortTargets=cross_package_targets,
                                               worldTransformsCompared=len(actual['transforms']),
                                               meshesCompared=compare_meshes(actual, twin, controlled),
                                               meshExclusions=[dict(row,
                                                   readerPointCount=actual['meshes'][row['path']]['points'])
                                                   for row in controlled],
                                               relationshipCounts=actual['relationshipCounts'])
