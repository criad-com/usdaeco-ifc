"""Pinned full-variant exact acceptance, additional to the legacy integration gate."""
import json
from pathlib import Path
import shutil
import tempfile
from .acceptance import generator_root
from .runtime import python
from .exact import export_exact
from .exact_runtime import runtime_paths, RuntimeUnavailable


def check(report):
    try:
        runtime_paths()
    except RuntimeUnavailable as error:
        report.not_run('full clash variant exact export',str(error))
        return None
    print('== stage: full clash variant exact export',flush=True)
    source=generator_root()
    publication=json.loads((source/'dist/clash/dc.manifest.json').read_text())
    with tempfile.TemporaryDirectory(prefix='aeco-ifc-exact-check-') as temporary:
        work=Path(temporary)
        shutil.copytree(source/'spec',work/'spec')
        code='import sys;sys.dont_write_bytecode=True;sys.path.insert(0,sys.argv.pop(1));from dcbuild.cli import main;raise SystemExit(main())'
        python(['-c',code,str(source/'src'),'build-ifc','--variant','clash','--out','ifc','--manifest-dir','manifests'],
               cwd=work,check=True,capture_output=True,text=True,timeout=240)
        result=export_exact(work/'ifc/demo-datacentre-01.ifc',source/'dist/clash/dc.usda',work/'exact')
        report.check('every meshable product exact',result['selected']==result['exact']==publication['counts']['elements'] and result['failed']==0,
                     f"{result['exact']}/{result['selected']}; {len(result['perClass'])} IFC classes")
        report.check('all exact products are valid solids',all(r['valid'] and r['solidCount']>0 for r in result['bodies']))
        report.check('every twin within volume and area budgets',all(r['withinTolerance'] for r in result['bodies']),str(result['exact']))
        report.check('kernel tolerances recorded',all(r['tolerance']>0 for r in result['bodies']))
        report.check('mapped items use shared prototypes',0<result['prototypes']<result['mappedProducts'],f"{result['mappedProducts']} products / {result['prototypes']} prototypes")
        report.check('IFC material subsets authored',result['materialSubsets']>result['exact'],f"{result['materialSubsets']} exact subsets plus their twin subsets")
        # Byte parity is about the default converter's source as well as its
        # existing published-output comparisons; the optional pass never calls it.
        result={k:v for k,v in result.items() if k!='bodies'}
        return result
