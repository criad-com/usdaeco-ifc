"""Generate and convert a large room/pipe grid, then time one closure edit.

Run as python -m scenarios.benchmark --directory .work/performance --rooms 4000.
The default contains 20,004 IfcElements (four walls and one pipe per room).
"""

import argparse
import json
import os
from pathlib import Path
import time
import numpy as np
import ifcopenshell
from ifcopenshell.api import run
from aeco_sync import register_plugins

os.environ.setdefault('AECO_KIND_PLUGIN',str(Path(__file__).resolve().parent.parent/'tests/fixtures/usdAecoKindProto'))
register_plugins()
from pxr import Gf, Usd, UsdGeom
from usdaeco_ifc._ifc_utils import uel, urep, pipe_profile
from usdaeco_ifc._ifc_authoring import frame, add_ports
from usdaeco_ifc.host import IfcHost
from aeco_sync.stack import create
from aeco_sync.readback import bind
from aeco_sync.edits import Edit
from aeco_sync.closure import Closure
from aeco_sync.identity import guid_to_uuid
from scenarios.baseline import build
from usdaeco_ifc.convert import convert


def generate(path, rooms):
    native = build(str(path))
    storey = native.by_type('IfcBuildingStorey')[0]
    wall_type = native.by_type('IfcWallType')[0]
    pipe_type = native.by_type('IfcPipeSegmentType')[0]
    body = urep.get_context(native,'Model','Body','MODEL_VIEW')
    axis = urep.get_context(native,'Plan','Axis','GRAPH_VIEW')
    wall_rep = run('geometry.add_wall_representation',native,context=body,length=4.,height=2.4,thickness=.2)
    wall_axis = run('geometry.add_axis_representation',native,context=axis,axis=[(0.,0.),(4.,0.)])
    profile = pipe_profile(native.by_type('IfcPipeSegment')[0])
    wall_products,pipe_products=[],[]
    for i in range(rooms):
        x,y=(i%80)*6.,(i//80)*6.+10
        for j,(origin,angle) in enumerate([((x,y,0),0),((x+4,y,0),np.pi/2),((x+4,y+4,0),np.pi),((x,y+4,0),-np.pi/2)]):
            wall=run('root.create_entity',native,ifc_class='IfcWall',name=f'Room {i} wall {j}',predefined_type='STANDARD')
            matrix=np.eye(4);c,s=np.cos(angle),np.sin(angle)
            matrix[:3,:3]=[[c,-s,0],[s,c,0],[0,0,1]];matrix[:3,3]=origin
            run('geometry.edit_object_placement',native,product=wall,matrix=matrix)
            run('geometry.assign_representation',native,product=wall,representation=wall_rep)
            run('geometry.assign_representation',native,product=wall,representation=wall_axis)
            wall_products.append(wall)
        pipe=run('root.create_entity',native,ifc_class='IfcPipeSegment',name=f'Room {i} pipe',predefined_type='RIGIDSEGMENT')
        matrix=frame((1,0,0),(x+.5,y+2,1))
        run('geometry.edit_object_placement',native,product=pipe,matrix=matrix)
        # Give each segment a distinct extrusion so an axis edit is truly local.
        rep=run('geometry.add_profile_representation',native,context=body,profile=profile,depth=3.)
        run('geometry.assign_representation',native,product=pipe,representation=rep)
        add_ports(native,pipe,[matrix[:3,3],matrix[:3,3]+matrix[:3,2]*3],[.05,.05],[-matrix[:3,2],matrix[:3,2]])
        pipe_products.append(pipe)
        if (i+1)%500==0:print(f'Generated {i+1} rooms',flush=True)
    run('type.assign_type',native,related_objects=wall_products,relating_type=wall_type,should_map_representations=False)
    run('type.assign_type',native,related_objects=pipe_products,relating_type=pipe_type,should_map_representations=False)
    run('material.assign_material',native,products=pipe_products,type='IfcMaterialProfileSetUsage',material=uel.get_material(pipe_type,should_skip_usage=True))
    run('material.assign_material',native,products=wall_products,type='IfcMaterialLayerSetUsage',material=uel.get_material(wall_type,should_skip_usage=True))
    run('spatial.assign_container',native,products=wall_products+pipe_products,relating_structure=storey)
    native.write(str(path))
    return len(native.by_type('IfcElement')),pipe_products[0].GlobalId


def benchmark(directory, rooms=4000):
    directory.mkdir(parents=True,exist_ok=True)
    source=directory/'large.ifc';model=directory/'model.usda'
    if not source.exists():
        count,target=generate(source,rooms)
        (directory/'source.json').write_text(json.dumps(dict(elements=count,target=target)))
    source_info=json.loads((directory/'source.json').read_text())
    if not model.exists():
        print('Converting complete IFC with ifc2usdaeco',flush=True)
        convert(str(source),str(model))
    # The core converter already supplied the whole stage, axes and bodies. Bind
    # the target and its ports for this host microbenchmark; no fixture kind pass
    # over 20k elements is included in the measured interval.
    import tempfile
    session = create(model, source, tempfile.mkdtemp(prefix='session-', dir=directory))
    current=session.current()
    prim=next(p for p in current.Traverse() if p.GetAttribute('aeco:id') and p.GetAttribute('aeco:id').Get()==guid_to_uuid(source_info['target']))
    path=prim.GetPath()
    with Usd.EditContext(session.stage,session.layer('kind.usda')):
        p=session.stage.GetPrimAtPath(path);p.ApplyAPI('AecoPipeAPI')
        bind(p,'ifc',source_info['target'],'',session.version('ifc'),source)
    host=IfcHost(session)
    edit=Edit(str(path),'aeco:axis:end',Gf.Vec3d(0,0,3.1),Gf.Vec3d(0,0,3),'axis',ref=source_info['target'])
    start=time.perf_counter();touched=host.apply([edit],Closure([str(path)]));applied=time.perf_counter()
    receipt=host.readback(touched);read=time.perf_counter()
    diagnostics=host.validate();validated=time.perf_counter()
    assert len(touched)==len(receipt['meshes'])==1
    assert len(receipt['touched'][0]['ports'])==2
    assert abs(receipt['touched'][0]['derived']['aeco:axis:length']-3.1)<1e-9
    assert not any(d['severity']=='error' for d in diagnostics)
    result=dict(elements=source_info['elements'],rooms=rooms,touched=len(touched),meshes=len(receipt['meshes']),
                applySeconds=applied-start,readbackSeconds=read-applied,applyReadbackSeconds=read-start,
                scopedValidationSeconds=validated-read,ifcopenshell=ifcopenshell.version)
    # Also report the complete engine call (open/hash, preflight, native write,
    # closure receipt, USD publication). Intent authoring is timed separately.
    from aeco_sync.edits import author
    from aeco_sync import engine
    started = time.perf_counter()
    author(session, str(path), ['length=3.1'])
    authored = time.perf_counter()
    outcome = engine.apply(session)
    finished = time.perf_counter()
    assert outcome['accepted'] == 1 and outcome['pending'] == 0, outcome
    assert len(outcome['touched']) == 1
    result.update(intentAuthorSeconds=authored-started, engineApplySeconds=finished-authored)
    (directory/'timing.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)
    assert result['elements']>=20000 and result['applyReadbackSeconds']<5
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,default=Path('.work/performance'))
    parser.add_argument('--rooms',type=int,default=4000)
    args=parser.parse_args();benchmark(args.directory,args.rooms)
