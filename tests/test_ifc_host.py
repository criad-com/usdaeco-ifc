"""WP4 native regression tests: topology, receipts, catalogs and diagnostics."""

import json
import numpy as np
from pathlib import Path
import pytest
from pxr import Gf, Sdf, Usd, UsdGeom
import ifcopenshell
from ifcopenshell.api import run
from aeco_sync import engine
from aeco_sync.edits import author, collect, preflight
from aeco_sync.diagnostics import Diagnostics
from usdaeco_ifc.host import IfcHost
from usdaeco_ifc._ifc_utils import uel, usys, world, base_extrusion
from usdaeco_ifc._ifc_validation import validation_rows
from aeco_sync.identity import guid_to_uuid
from scenarios.wp4 import select, configure, author_extra
from scenarios.run import prepare, fresh_session, author_case, assert_case
from tests.test_transactions import native_session


def apply_ok(session):
    result = engine.apply(session)
    assert result['accepted'] and result['pending'] == 0, result['diagnostics']
    assert not any(d['blocking'] for d in result['diagnostics']), result
    return result


@pytest.mark.parametrize('baseline,role', [('free-pipes-angle','BEND'), ('free-pipes','CONNECTOR'), ('free-pipes-step','TRANSITION')])
def test_generated_fitting_persists_with_ports_and_takeout(tmp_path, baseline, role):
    prepare(tmp_path, {'baseline':baseline})
    session = fresh_session(tmp_path, 'ifc')
    author_extra(session, {'relations':[['Pipe 1/end','aeco:connectedPorts',['Pipe 2/start']]]})
    result = apply_ok(session)
    assert len(result['touched']) == 3
    native = IfcHost(session)
    fitting, = native.f.by_type('IfcPipeFitting')
    assert fitting.PredefinedType == role
    ports = usys.get_ports(fitting)
    assert len(ports) == 2
    for port in ports:
        assert np.linalg.norm(world(port)[:3,3] - world(usys.get_connected_port(port))[:3,3]) < 1e-8
    for pipe in native.f.by_type('IfcPipeSegment'):
        assert base_extrusion(pipe).Depth < (2 if pipe.Name == 'Pipe 1' else 1.5)
    ref, path = fitting.GlobalId, native.path_for(fitting)
    prim = session.stage.GetPrimAtPath(path)
    assert prim.GetAttribute('aeco:id').Get() == guid_to_uuid(ref)
    assert prim.GetAttribute('aeco:pipeFitting:origin').Get() == 'generated'
    receipt = native.readback([ref])
    assert len(receipt['touched']) == len(receipt['meshes']) == 1
    assert len(receipt['touched'][0]['ports']) == 2
    assert receipt['touched'][0]['path'] == str(path)
    mesh = UsdGeom.Mesh(prim.GetChild('Geom'))
    subsets = UsdGeom.Subset.GetAllGeomSubsets(mesh)
    assert subsets
    indices = sorted(i for subset in subsets for i in subset.GetIndicesAttr().Get())
    assert indices == list(range(len(mesh.GetFaceVertexCountsAttr().Get())))
    # Reopening and another transaction must not mint the same fitting twice.
    assert engine.apply(session)['edits'] == 0
    assert len(IfcHost(session).f.by_type('IfcPipeFitting')) == 1


def test_create_pipe_and_delete(native_session):
    session = native_session
    pipe = select(session.stage, 'Pipe 1')
    typ = select(session.stage, 'Pipe 1/type')
    path = pipe.GetPath().GetParentPath().AppendChild('NewPipe')
    session.capture_base()
    with Usd.EditContext(session.stage, session.intent):
        new = UsdGeom.Xform.Define(session.stage, path).GetPrim()
        new.GetInherits().SetInherits([typ.GetPath()])
        for api in ('AecoElementAPI','AecoAxisAPI','AecoPipeAPI'):
            new.ApplyAPI(api)
        new.GetAttribute('aeco:axis:start').Set(Gf.Vec3d(0,0,0))
        new.GetAttribute('aeco:axis:end').Set(Gf.Vec3d(3,0,0))
        new.GetAttribute('aeco:pipe:nominalDiameter').Set(.065)
    result = apply_ok(session)
    assert result['accepted'] == 1
    new = session.stage.GetPrimAtPath(path)
    native = IfcHost(session)
    entity = native.f.by_guid(new.GetAttribute('aeco:host:ifc:ref').Get())
    assert new.GetAttribute('aeco:id').Get() == guid_to_uuid(entity.GlobalId)
    assert len(usys.get_ports(entity)) == 2
    assert base_extrusion(entity).Depth == pytest.approx(3)
    assert len(new.GetInherits().GetAllDirectInherits()) == 1
    assert new.GetAttribute('aeco:pipe:outerDiameter').Get() == pytest.approx(.065)
    session.capture_base()
    with Usd.EditContext(session.stage, session.intent):
        new.SetActive(False)
    apply_ok(session)
    assert not session.stage.GetPrimAtPath(path).IsActive()
    assert len(IfcHost(session).f.by_type('IfcPipeSegment')) == 2


@pytest.mark.parametrize('token,offset', [('centerline',-.1),('finishFaceInterior',-.2),('coreFaceExterior',0.)])
def test_wall_location_line_and_flip(native_session, token, offset):
    session = native_session
    wall = select(session.stage,'Wall A')
    session.capture_base()
    with Usd.EditContext(session.stage,session.intent):
        wall.GetAttribute('aeco:wall:locationLine').Set(token)
    # coreFaceExterior may have the same native offset, but is still a driver choice.
    apply_ok(session)
    native = IfcHost(session)
    entity = native.entity_for_path(wall.GetPath())
    assert uel.get_material(entity).OffsetFromReferenceLine == pytest.approx(offset)
    session.capture_base()
    with Usd.EditContext(session.stage,session.intent):
        wall.GetAttribute('aeco:wall:flipped').Set(True)
    apply_ok(session)
    native = IfcHost(session)
    usage = uel.get_material(native.entity_for_path(wall.GetPath()))
    assert usage.DirectionSense == 'NEGATIVE'
    assert usage.OffsetFromReferenceLine == pytest.approx(-offset)


def test_type_edit_regenerates_all_occurrences(native_session):
    session = native_session
    author_extra(session, {'type':[['Wall B','Wall A/type']]})
    apply_ok(session)
    author_extra(session, {'typeRaw':[['Wall A/type','aeco:buildUp:thicknesses',[.27]],
                                     ['Wall A/type','aeco:buildUp:materials',['Masonry']]]})
    result = apply_ok(session)
    assert len(result['touched']) == 3
    native = IfcHost(session)
    for name in ('Wall A','Wall B'):
        wall = select(session.stage,name)
        assert wall.GetAttribute('aeco:wall:thickness').Get() == pytest.approx(.27)
        assert uel.get_material(native.entity_for_path(wall.GetPath()),should_skip_usage=True).MaterialLayers[0].Material.Name == 'Masonry'
    typ = select(session.stage,'Wall A/type')
    assert typ.GetAttribute('aeco:buildUp:totalThickness').Get() == pytest.approx(.27)


def test_validation_scope_and_instance_mapping(native_session, monkeypatch):
    native = IfcHost(native_session)
    wall = native.entity_for_path(select(native_session.stage,'Wall A').GetPath())
    pipe = native.entity_for_path(select(native_session.stage,'Pipe 1').GetPath())
    seen = []
    original = ifcopenshell.validate.validate
    def capture(f, logger, express_rules=False):
        seen.append(([e.id() for e in f], express_rules))
        return original(f, logger, express_rules=express_rules)
    monkeypatch.setattr(ifcopenshell.validate, 'validate', capture)
    native.validation_refs = {pipe.GlobalId}
    native.validate()
    assert pipe.id() in seen[-1][0] and wall.id() not in seen[-1][0]
    assert not seen[-1][1]
    native.validate_all = True
    native.validate()
    assert wall.id() in seen[-1][0] and seen[-1][1]
    rows = validation_rows(native,[{'instance':wall.id(),'rule':'IfcWall.TestRule','message':'native finding'}])
    assert rows[0]['code'] == 'ifc:IfcWall.TestRule'
    assert rows[0]['about'] == [str(native.path_for(wall))]


def test_geometry_log_preserves_severity_and_message(native_session, monkeypatch):
    native = IfcHost(native_session)
    pipe = native.entity_for_path(select(native_session.stage,'Pipe 1').GetPath())
    message = 'Native tessellation warning'
    monkeypatch.setattr(ifcopenshell,'get_log',lambda:json.dumps({'level':'Warning','message':message}))
    native.geometry_log(pipe)
    row, = native.diagnostics()
    assert row['code'] == 'ifcopenshell:geometry' and row['severity'] == 'warning'
    assert row['message'] == message and row['hostRefs'] == [pipe.GlobalId]


def test_readback_tessellates_only_requested_closure(native_session, monkeypatch):
    native = IfcHost(native_session)
    pipe = native.entity_for_path(select(native_session.stage,'Pipe 1').GetPath())
    seen = []
    create_shape = ifcopenshell.geom.create_shape
    def capture(settings, entity):
        seen.append(entity.GlobalId)
        return create_shape(settings,entity)
    monkeypatch.setattr(ifcopenshell.geom,'create_shape',capture)
    native.readback([pipe.GlobalId])
    assert seen == [pipe.GlobalId]


def test_ids_findings_map_to_prims(native_session, tmp_path):
    pytest.importorskip('ifctester', reason='optional IDS extra is not installed')
    from ifctester import ids, facet
    spec = ids.Ids(title='Pipe names')
    rule = ids.Specification(name='Named pipes',identifier='pipe-name',ifcVersion=['IFC4X3_ADD2'])
    rule.applicability.append(facet.Entity(name='IFCPIPESEGMENT'))
    rule.requirements.append(facet.Attribute(name='Name',value='Required name'))
    spec.specifications.append(rule)
    path = tmp_path/'rules.ids'
    spec.to_xml(str(path))
    native = IfcHost(native_session,ids=path)
    rows = native.validate()
    findings = [d for d in rows if d['code']=='ids:Named pipes']
    assert len(findings) == 2, rows
    assert all(d['about'] and d['hostRefs'] for d in findings)


def test_resize_generated_coupling_to_transition_preserves_ids(tmp_path):
    prepare(tmp_path, {'baseline':'free-pipes'})
    session = fresh_session(tmp_path,'ifc')
    author_extra(session, {'relations':[['Pipe 1/end','aeco:connectedPorts',['Pipe 2/start']]]})
    apply_ok(session)
    native = IfcHost(session)
    fitting, = native.f.by_type('IfcPipeFitting')
    ref, ports = fitting.GlobalId, {p.GlobalId for p in usys.get_ports(fitting)}
    path = native.path_for(fitting)
    author(session,str(select(session.stage,'Pipe 1').GetPath()),['diameter=.065'])
    apply_ok(session)
    native = IfcHost(session)
    fitting, = native.f.by_type('IfcPipeFitting')
    assert fitting.GlobalId == ref and native.path_for(fitting) == path
    assert {p.GlobalId for p in usys.get_ports(fitting)} == ports
    assert fitting.PredefinedType == 'TRANSITION'
    for port in usys.get_ports(fitting):
        assert np.linalg.norm(world(port)[:3,3]-world(usys.get_connected_port(port))[:3,3]) < 1e-8


def test_multiple_materials_partition_body_faces(native_session):
    native = IfcHost(native_session)
    pipe = native.entity_for_path(select(native_session.stage,'Pipe 1').GetPath())
    from ifcopenshell.util import representation as rep
    from ifcopenshell.util.shape_builder import ShapeBuilder
    body = rep.get_representation(pipe,'Model','Body','MODEL_VIEW')
    builder = ShapeBuilder(native.f)
    second = builder.extrude(builder.profile(builder.rectangle(size=(.02,.02))),.2,position=(.1,0.,0.))
    body.Items = tuple(body.Items) + (second,)
    styles = []
    for i,item in enumerate(body.Items):
        style = run('style.add_style',native.f,name=f'Surface {i}')
        run('style.add_surface_style',native.f,style=style,ifc_class='IfcSurfaceStyleShading',
            attributes={'SurfaceColour':{'Name':None,'Red':float(i),'Green':.5,'Blue':.2}})
        run('style.assign_item_style',native.f,item=item,style=style)
        styles.append(style.id())
    receipt = native.readback([pipe.GlobalId])
    mesh = receipt['meshes'][pipe.GlobalId]
    assert len(set(mesh['materialIds'])) == 2
    assert {m['instanceId'] for m in mesh['materials']} == set(styles)
    from aeco_sync.readback import publish
    publish(native_session,receipt,native_session.layer('result.ifc.usda'),'ifc',native.version(),native.document)
    parent=native_session.stage.GetPrimAtPath(native.path_for(pipe))
    subsets=UsdGeom.Subset.GetAllGeomSubsets(UsdGeom.Mesh(parent.GetChild('Geom')))
    assert len(subsets)==2
    assert sum(len(s.GetIndicesAttr().Get()) for s in subsets)==len(mesh['faces'])//3
    # A subsequent single-style receipt must deactivate the obsolete weaker subset.
    body.Items = (body.Items[0],)
    receipt = native.readback([pipe.GlobalId])
    publish(native_session,receipt,native_session.layer('result.ifc.usda'),'ifc',native.version(),native.document)
    assert len(UsdGeom.Subset.GetAllGeomSubsets(UsdGeom.Mesh(parent.GetChild('Geom'))))==1


def test_reverse_unjoin_authorizes_wall_end(native_session):
    session=native_session
    author_extra(session,{'relations':[['Wall B','aeco:wall:joinAtStart',[]]]})
    author(session,str(select(session.stage,'Wall A').GetPath()),['length=5'])
    apply_ok(session)
    assert select(session.stage,'Wall A').GetAttribute('aeco:axis:length').Get()==pytest.approx(5)


def test_cli_author_captures_only_edited_base_properties(native_session):
    from aeco_sync.stack import PREFIX
    session=native_session
    pipe=select(session.stage,'Pipe 1')
    author(session,str(pipe.GetPath()),['length=2.2'])
    base=json.loads(session.intent.customLayerData[PREFIX+'baseValues'])
    assert list(base)==[str(pipe.GetPath())+'.aeco:axis:end']
    author(session,str(pipe.GetPath()),['length=2.3','diameter=.065'])
    base=json.loads(session.intent.customLayerData[PREFIX+'baseValues'])
    assert len(base)==2 and base[str(pipe.GetPath())+'.aeco:axis:end']==[0,0,2]


def test_fitting_takeout_failure_rolls_back(native_session):
    session=native_session
    # The opposite free ends point away from each other; a coupling would invert a pipe.
    author_extra(session,{'relations':[['Pipe 1/start','aeco:connectedPorts',['Pipe 2/end']]]})
    before=Path(session.document('ifc')).read_bytes()
    result=engine.apply(session)
    assert result['accepted']==0 and result['pending']==1
    assert Path(session.document('ifc')).read_bytes()==before
    assert not IfcHost(session).f.by_type('IfcPipeFitting')


def test_pipe_type_swap_reads_selected_type_size(tmp_path):
    prepare(tmp_path, {'baseline':'free-pipes-step'})
    session=fresh_session(tmp_path,'ifc')
    author(session,str(select(session.stage,'Pipe 1').GetPath()),['diameter=.065'])
    apply_ok(session)
    author_extra(session,{'type':[['Pipe 1','Pipe 2/type']]})
    apply_ok(session)
    pipe=select(session.stage,'Pipe 1')
    assert pipe.GetAttribute('aeco:pipe:nominalDiameter').Get()==pytest.approx(.08)
    assert pipe.GetAttribute('aeco:pipe:outerDiameter').Get()==pytest.approx(.08)
