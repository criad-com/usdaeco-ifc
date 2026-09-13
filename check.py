#!/usr/bin/env python3
"""Run integration contracts and print N checks, M failed."""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import bootstrap
from usdaeco_check import Report
from usdaeco_check.structure import check_structure
from usdaeco_check.example import check_example
from usdaeco_ifc.acceptance import build_base, converter_parity, host_cases
from usdaeco_ifc.runtime import python
from usdaeco_ifc.roundtrip import render_foreground

ROOT = Path(__file__).resolve().parent


def main():
    report = Report()
    evidence = {}
    from pxr import Plug
    core = Path(os.environ.get('AECO_CORE_ROOT',ROOT.parent/'usdaeco-core'))
    Plug.Registry().RegisterPlugins(str(core/'usdAecoValidators'))
    # Deliberately fail if the core Python module is not importable.
    import usdAecoValidators
    print('== stage: integration structure',flush=True)
    for result in check_structure(ROOT): report.add(result)
    import aeco_sync
    aeco_sync.register_plugins()
    from aeco_sync.hosts.base import discover, host_class, Host
    from usdaeco_ifc.host import IfcHost
    report.check('sync 0.5 interface loaded', aeco_sync.__version__.startswith('0.5.'))
    entry = discover().get('ifc')
    report.check('IFC entry point', entry is not None and entry.value == 'usdaeco_ifc.host:IfcHost')
    report.check('entry point loads contract implementation', host_class('ifc') is IfcHost and issubclass(IfcHost,Host))
    print('== stage: scratch base variant and converter parity',flush=True)
    with tempfile.TemporaryDirectory(prefix='aeco-ifc-check-') as temporary:
        temporary = Path(temporary)
        source, manifest = build_base(temporary/'base')
        parity, model = converter_parity(source,temporary/'conversion',manifest)
        evidence['converter'] = parity
        evidence['manifestCounts'] = manifest['counts']
        report.check('converter counters unchanged', parity['moved'] == parity['reference'], json.dumps(parity['moved'],sort_keys=True))
        for layer in parity['layers']:
            detail = ('byte-identical' if layer['identical'] else
                      f"binary encoding differs: {layer['referenceBytes']} -> {layer['bytes']} bytes; "
                      f"{len(layer['differences'])} lines in authored-content diff")
            report.check(layer['name'] + ' published parity', layer['contentIdentical'], detail)
        report.check('all elements parented',parity['moved']['unparented'] == 0)
        report.check('space count matches variant manifest',parity['spaces'] == parity['manifestSpaces'],f"{parity['rooms']} rooms; {parity['spaces']} spaces")
        report.check('source facility is declared demo',manifest['facility'] == 'demo-datacentre-01' and manifest['variant'] == 'base')
        from pxr import Usd
        stage = Usd.Stage.Open(str(model))
        compatibility = parity['publishedCompatibility']
        report.check('converted and published stages compose; core validators pass',
                     stage is not None and not stage.GetCompositionErrors()
                     and compatibility['errors'] == compatibility['compositionErrors'] == 0
                     and compatibility['elements'] == parity['moved']['elements']
                     and compatibility['spaces'] == parity['spaces'], json.dumps(compatibility,sort_keys=True))
        elements = [p for p in stage.Traverse() if p.HasAPI('AecoElementAPI')]
        report.check('element identities unique',len({p.GetAttribute('aeco:id').Get() for p in elements}) == len(elements) == parity['moved']['elements'])
        print('== stage: IFC hosts through entry points',flush=True)
        log = io.StringIO()
        try:
            with contextlib.redirect_stdout(log):
                cases = host_cases(temporary/'host-cases')
        except Exception:
            print(log.getvalue()[-2000:])
            raise
        evidence['hostCases'] = cases
        for family,wanted in (('synthetic',21),('camera',9),('datacentre',9)):
            rows = cases[family]
            report.check(f'{family} case count',len(rows) == wanted,f'{sum(r["passed"] for r in rows)}/{len(rows)}')
            for row in rows: report.check(row['id'],row['passed'])
        print('== stage: roundtrip, reimport and render',flush=True)
        environment = {k:v for k,v in os.environ.items() if k != 'PYTHONPATH'}
        environment['PATH'] = str(Path(sys.executable).parent) + os.pathsep + environment.get('PATH','')
        result = python([str(ROOT/'examples/roundtrip/run.py'),'--source-ifc',str(source)],
                        env=environment,cwd=ROOT,capture_output=True,text=True,timeout=240)
        print(result.stdout)
        if result.returncode: print(result.stderr)
        report.check('roundtrip execution',result.returncode == 0)
        report.add(check_example(ROOT/'examples/roundtrip',execute=False))
        images = list((ROOT/'examples/roundtrip/out/renders').glob('*.png'))
        evidence['renders'] = [dict(path='renders/'+image.name, **render_foreground(image)) for image in images]
        report.check('render contains foreground, not only a background gradient',bool(images) and
                     all(row['horizontalRange'] > 20/255 and row['foregroundFraction'] >= .02
                         for row in evidence['renders']),json.dumps(evidence['renders'],sort_keys=True))
        if result.returncode == 0:
            evidence['renderFrame'] = json.loads((ROOT/'examples/roundtrip/out/render-frame.json').read_text())
            report.check('edited body bounds inside camera frustum',evidence['renderFrame']['allCornersInside'])
            convergence = json.loads((ROOT/'examples/roundtrip/out/findings.json').read_text())[0]
            evidence['roundtrip'] = convergence
            report.check('two elements compared',convergence['elements'] == 2 and convergence['driversCompared'] == 14)
            report.check('zero convergence differences',convergence['driversConverged'] and not any(convergence[key] for key in ('driverDifferences','missingDrivers','missingElements','diagnostics')))
            report.check('both bodies measured',len(convergence['bodies']) == 2 and not any(b['divergent'] for b in convergence['bodies']))
            final = Usd.Stage.Open(str(ROOT/'examples/roundtrip/result/example.usdc'))
            reimported = Usd.Stage.Open(str(ROOT/'examples/roundtrip/.work/roundtrip/reimport/session/stage.usda'))
            selected = json.loads((ROOT/'examples/roundtrip/out/final-stage.json').read_text())['elements']
            fields = {'wall': ('aeco:wall:height',), 'pipe': ('aeco:axis:start', 'aeco:axis:end')}
            final_matches = True
            for kind, row in selected.items():
                actual = final.GetPrimAtPath(row['path'])
                expected = reimported.GetPrimAtPath(row['path'])
                final_matches &= actual.GetAttribute('aeco:id').Get() == row['id']
                final_matches &= all(actual.GetAttribute(name).Get() == expected.GetAttribute(name).Get()
                                     for name in fields[kind])
                final_matches &= all(actual.GetChild('Geom').GetAttribute(name).Get() ==
                                     expected.GetChild('Geom').GetAttribute(name).Get()
                                     for name in ('points', 'faceVertexCounts', 'faceVertexIndices'))
            report.check('published drivers and bodies are the final reimport', final_matches)
            report.check('published stage has no live session bindings', not any(
                a.GetName().startswith('aeco:host:') for p in final.TraverseAll() for a in p.GetAttributes()))
        probe = '''import sys
from pxr import Usd
assert Usd.SchemaRegistry.GetTypeFromSchemaTypeName('AecoHostBindingAPI').isUnknown
s=Usd.Stage.Open(sys.argv[1]); assert s and not s.GetCompositionErrors() and s.Flatten()
'''
        clean = {k:v for k,v in environment.items() if k not in ('PXR_PLUGINPATH_NAME','PXR_AR_DEFAULT_SEARCH_PATH')}
        vanilla = python(['-c',probe,str(ROOT/'examples/roundtrip/out/example.usda')],env=clean,capture_output=True,text=True)
        report.check('roundtrip composes without family plugins',vanilla.returncode == 0,vanilla.stderr if vanilla.returncode else '')
        print('== stage: converter and IFC regression tests',flush=True)
        environment['AECO_CONTRACT_IFC'] = str(source)
        from usdaeco_ifc.exact_runtime import runtime_paths, RuntimeUnavailable
        try:
            runtime_paths()
            exact_available = True
        except RuntimeUnavailable:
            exact_available = False
            environment.pop('USD_SOLID_OCCT_RUNTIME', None)
        junit = temporary / 'pytest.xml'
        tests = python(['-m','pytest','-q','--tb=short','-rs','--junitxml',str(junit)],
                       cwd=ROOT,env=environment,capture_output=True,text=True,
                       timeout=600 if os.environ.get('USD_DEV') else 240)
        print(tests.stdout)
        if tests.returncode: print(tests.stderr)
        report.check('pytest',tests.returncode == 0)
        passed = re.search(r'(\d+) passed',tests.stdout)
        skipped = re.search(r'(\d+) skipped',tests.stdout)
        evidence['pytest'] = int(passed.group(1)) if passed else 0
        evidence['skipped'] = int(skipped.group(1)) if skipped else 0
        test_cases = ET.parse(junit).findall('.//testcase') if junit.is_file() else []
        allowed_skips = {'test_connected_datacentre_matches_usd_only'}
        if not exact_available:
            allowed_skips.add('test_native_ifc_wall_and_pipe')
        unexpected_skips = [case.get('name') for case in test_cases if case.find('skipped') is not None
                            and case.get('name') not in allowed_skips
                            and not (not os.environ.get('USD_DEV') and case.get('classname','').endswith('test_file_format'))]
        report.check('native integration tests executed',evidence['pytest'] >= 100 and not unexpected_skips,
                     'optional skips: ' + str(evidence['skipped']))
        print('== stage: optional IFC file format',flush=True)
        if os.environ.get('USD_DEV'):
            resources = Path(os.environ.get('USD_IFC_PLUGIN_DIR', ROOT/'out/plugins/usdIfc/resources'))
            descriptor_path = resources/'plugInfo.json'
            products = False
            if descriptor_path.is_file():
                descriptor = json.loads(descriptor_path.read_text())['Plugins'][0]
                products = (resources / descriptor['Root'] / descriptor['LibraryPath']).is_file()
            report.check('usdIfc build products',products)
            native_cases = [case for case in test_cases if case.get('classname','').endswith('test_file_format')
                            and case.get('name') not in ('test_file_format_descriptor','test_connected_datacentre_matches_usd_only')]
            report.check('usdIfc native contract tests', len(native_cases) >= 12 and all(
                not any(case.find(tag) is not None for tag in ('failure','error','skipped')) for case in native_cases),
                str(len(native_cases)) + ' native tests')
            evidence['fileFormat'] = {}
            for case in test_cases:
                for prop in case.findall('./properties/property'):
                    if prop.get('name') == 'fileFormat':
                        evidence['fileFormat'].update(json.loads(prop.get('value')))
            full = evidence.get('fileFormat',{}).get('fullFacility','not proven: native tests did not report')
            if isinstance(full,dict):
                report.check('connected full facility twin parity',True,json.dumps(full,sort_keys=True))
            else:
                report.not_run('connected full facility twin parity',full)
        else:
            report.not_run('usdIfc build products','optional: set USD_DEV and run build.sh')
            report.not_run('usdIfc native contract tests','not proven: USD_DEV is unset')
    from usdaeco_ifc.exact_acceptance import check as check_exact
    evidence['exact'] = check_exact(report)
    evidence.update(checks=len(report.results),failed=report.failed)
    output = ROOT/'.work';output.mkdir(exist_ok=True)
    (output/'check.json').write_text(json.dumps(evidence,indent=2,default=list)+'\n')
    return report.finish()

if __name__=='__main__': raise SystemExit(main())
