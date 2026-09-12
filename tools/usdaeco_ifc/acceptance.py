"""Reproducible conversion and host acceptance; all generated inputs are scratch data."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import sys
from .runtime import python


def generator_root():
    root = Path(os.environ.get('AECO_DATACENTRE_ROOT', Path(__file__).resolve().parents[3] / 'usdaeco-datacentre'))
    if not (root / 'src/dcbuild').is_dir():
        raise FileNotFoundError('Set AECO_DATACENTRE_ROOT to the pinned datacentre source')
    return root


def build_base(directory):
    directory = Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    source = generator_root()
    shutil.copytree(source / 'spec', directory / 'spec')
    code = 'import sys;sys.dont_write_bytecode=True;sys.path.insert(0,sys.argv.pop(1));from dcbuild.cli import main;raise SystemExit(main())'
    python(['-c', code, str(source / 'src'), 'build-ifc','--variant','base','--out','ifc','--manifest-dir','manifest'],
           cwd=directory,check=True,capture_output=True,text=True,timeout=240)
    return directory/'ifc/demo-datacentre-01.ifc', json.loads((directory/'manifest/demo-datacentre-01.base.json').read_text())


def convert_process(module, source, output):
    output = Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    code = 'import sys,json;from ' + module + ' import convert;stats=convert(sys.argv[1],sys.argv[2],threads=2);stats.pop("out",None);print(json.dumps(stats))'
    result = python(['-c',code,str(source),str(output)],check=True,capture_output=True,text=True,timeout=240)
    return json.loads(result.stdout.splitlines()[-1])


def converter_parity(source, directory, manifest):
    """Compare with the published base, using its binary semantic-layer format."""
    directory = Path(directory)
    published = generator_root() / 'dist/base'
    publication = json.loads((published/'dc.manifest.json').read_text())
    conversion = convert_process('usdaeco_ifc.convert',source,directory/'moved/dc.usda')
    from pxr import Plug, Sdf, Usd, UsdValidation
    from usdaeco_check.validation import run
    core = Path(os.environ.get('AECO_CORE_ROOT', generator_root().parent/'usdaeco-core'))
    Plug.Registry().RegisterPlugins(str(core/'usdAecoValidators'))
    import usdAecoValidators  # Required: import failure must never pass vacuously.
    registry = UsdValidation.ValidationRegistry()
    names = [m.name for m in registry.GetValidatorMetadataForKeyword('UsdAecoValidators')]
    loaded = registry.GetOrLoadValidatorsByName(names)
    if len(names) != 8 or len(loaded) != len(names) or not all(loaded):
        raise RuntimeError('All eight core validators must load through UsdValidation')
    reference_stage = Usd.Stage.Open(str(published/'dc.usda'))
    validation = run(reference_stage, ['UsdAecoValidators'])
    compatibility = dict(
        elements=sum(p.HasAPI('AecoElementAPI') for p in reference_stage.Traverse()),
        spaces=sum(p.GetTypeName() == 'AecoSpace' for p in reference_stage.Traverse()),
        compositionErrors=len(reference_stage.GetCompositionErrors()),
        errors=sum(e.GetType() == UsdValidation.ValidationErrorType.Error for e in validation),
        warnings=sum(e.GetType() == UsdValidation.ValidationErrorType.Warn for e in validation),
        validators=len(UsdValidation.ValidationRegistry().GetValidatorMetadataForKeyword('UsdAecoValidators')))
    stage = Usd.Stage.Open(str(directory/'moved/dc.usda'))
    spaces = sum(p.GetTypeName() == 'AecoSpace' for p in stage.Traverse())
    reference = publication['counts']
    moved = {key: conversion[key] for key in reference if key not in ('spaces', 'levels')}
    moved.update(spaces=spaces, levels=sum(p.GetTypeName() == 'AecoLevel' for p in stage.Traverse()))
    packed = directory/'published'; packed.mkdir(parents=True)
    semantics = Sdf.Layer.FindOrOpen(str(directory/'moved/dc.semantics.usda'))
    if not semantics.Export(str(packed/'dc.semantics.usdc')):
        raise RuntimeError('Could not export the published semantic-layer format')
    root = Sdf.Layer.CreateAnonymous('dc.usda')
    root.TransferContent(stage.GetRootLayer())
    root.subLayerPaths = ['dc.semantics.usdc', 'dc.geometry.usdc']
    root.Export(str(packed/'dc.usda'))
    shutil.copyfile(directory/'moved/dc.geometry.usdc', packed/'dc.geometry.usdc')
    layers = []
    for name, expected in publication['layers'].items():
        before = (published/name).read_bytes()
        after = (packed/name).read_bytes()
        reference_hash = hashlib.sha256(before).hexdigest()
        if reference_hash != expected['sha256'] or len(before) != expected['bytes']:
            raise ValueError('Published layer differs from its manifest: ' + name)
        import difflib
        before_text = Sdf.Layer.FindOrOpen(str(published/name)).ExportToString()
        after_text = Sdf.Layer.FindOrOpen(str(packed/name)).ExportToString()
        differences = list(difflib.unified_diff(before_text.splitlines(), after_text.splitlines(),
                                               fromfile='published/'+name, tofile='converted/'+name, lineterm=''))
        layers.append(dict(name=name,identical=before==after,bytes=len(after),sha256=hashlib.sha256(after).hexdigest(),
                           referenceBytes=len(before), referenceSha256=reference_hash,
                           contentIdentical=before_text == after_text,
                           contentSha256=hashlib.sha256(after_text.encode()).hexdigest(),
                           referenceContentSha256=hashlib.sha256(before_text.encode()).hexdigest(),
                           differences=differences))
    return dict(reference=reference,moved=moved,spaces=spaces,rooms=manifest['counts']['rooms'],
                manifestSpaces=manifest['counts']['spaces'],layers=layers,conversion=conversion,
                publishedCompatibility=compatibility,
                normalization='Text semantics exported to usdc; root sublayer extension follows the publisher.'), directory/'moved/dc.usda'


def host_cases(directory):
    from scenarios.run import run_suite
    report = run_suite(Path(directory),hosts=('ifc',))
    kinds = {'synthetic': [],'camera': [],'datacentre': []}
    root = Path(__file__).resolve().parents[2]
    definitions = json.loads((root/'scenarios/cases.json').read_text())
    categories = {c['id']: c.get('family','synthetic') for c in definitions}
    for row in report['hosts']['ifc']:
        kinds[categories[row['id']]].append(dict(id=row['id'],passed=row['passed']))
    return kinds
