"""A wall and pipe edited through the discovered host, exported and reimported."""
from pathlib import Path
import json
import numpy as np
from .runtime import python


def frame_camera(stage, paths, size=(1280, 800)):
    """Fit the edited bodies with a 15% margin; author in the display layer."""
    from itertools import product
    from pxr import Gf, Usd, UsdGeom
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ['default', 'render', 'proxy', 'guide'])
    bounds = Gf.Range3d()
    for path in paths:
        body = UsdGeom.Mesh(stage.GetPrimAtPath(path).GetChild('Geom'))
        if not body or body.ComputeVisibility() == 'invisible' or body.ComputePurpose() not in ('default', 'render'):
            raise ValueError('Edited body must be visible with default or render purpose')
        bounds.UnionWith(cache.ComputeWorldBound(body.GetPrim()).ComputeAlignedRange())
    if bounds.IsEmpty() or bounds.GetSize().GetLength() == 0:
        raise ValueError('Edited bodies have no usable bounds')
    camera = UsdGeom.Camera(stage.GetPrimAtPath('/Renders/edited_corner'))
    value = camera.GetCamera()
    value.verticalAperture = value.horizontalAperture * size[1] / size[0]
    center = bounds.GetMidpoint()
    # Choose an oblique Z-up view; position and distance come only from bounds.
    back = Gf.Vec3d(1, 2, 1).GetNormalized()
    up = Gf.Vec3d(0, 0, 1)
    view = Gf.Matrix4d().SetLookAt(center + back, center, up)
    corners = [Gf.Vec3d(*p) for p in product(*zip(bounds.GetMin(), bounds.GetMax()))]
    local = [view.TransformDir(p - center) for p in corners]
    tan_x = value.horizontalAperture / (2 * value.focalLength)
    tan_y = value.verticalAperture / (2 * value.focalLength)
    distance = max(p[2] + 1.15 * max(abs(p[0]) / tan_x, abs(p[1]) / tan_y) for p in local)
    eye = center + back * distance
    value.transform = Gf.Matrix4d().SetLookAt(eye, center, up).GetInverse()
    depths = [distance - p[2] for p in local]
    value.clippingRange = Gf.Range1f(min(depths) * .5, max(depths) * 1.5)
    camera.SetFromCamera(value)
    projection = value.frustum.ComputeViewMatrix() * value.frustum.ComputeProjectionMatrix()
    inside = all(all(-1 <= v <= 1 for v in projection.Transform(p)) for p in corners)
    if not inside:
        raise ValueError('Edited body bounds do not fit the camera frustum')
    return dict(bounds=[list(bounds.GetMin()), list(bounds.GetMax())], position=list(eye),
                direction=list(value.frustum.ComputeViewDirection()),
                clipping=[value.clippingRange.GetMin(), value.clippingRange.GetMax()],
                allCornersInside=inside, purposes='proxy,render')


def render_foreground(path):
    """Measure contrast against each row's background using normalized pixels."""
    from usdaeco_check.images import pixels
    image = pixels(path)
    # The fitted frame leaves the outer 2.5% strips clear on both sides.
    edge = max(1, image.shape[1] // 40)
    background = np.median(np.concatenate((image[:, :edge], image[:, -edge:]), axis=1), axis=1)
    foreground = np.max(np.abs(image - background[:, None, :]), axis=2) > 20 / 255
    return dict(foregroundPixels=int(foreground.sum()), totalPixels=int(foreground.size),
                foregroundFraction=float(foreground.mean()), threshold8bit=20,
                horizontalRange=float(np.ptp(image, axis=1).max()))


def review_layer(layer, target):
    """Export reusable opinions without live session bindings or transaction facts."""
    from aeco_sync.stack import all_specs
    from pxr import Sdf
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    copy = Sdf.Layer.CreateNew(str(target))
    copy.TransferContent(layer)
    copy.customLayerData = {}
    for spec in all_specs(copy):
        for prop in list(spec.properties.values()):
            if prop.name.startswith('aeco:host:'):
                spec.RemoveProperty(prop)
        if spec.HasInfo('apiSchemas'):
            applied = spec.GetInfo('apiSchemas')
            for field in ('explicitItems',) if applied.isExplicit else (
                    'prependedItems', 'appendedItems', 'addedItems', 'deletedItems', 'orderedItems'):
                setattr(applied, field, [name for name in getattr(applied, field)
                                        if not name.startswith('AecoHostBindingAPI:')])
            spec.SetInfo('apiSchemas', applied)
    copy.Save()
    return copy


def roundtrip(source, model, directory, *, review=None):
    from aeco_sync import engine
    from aeco_sync.hosts.base import host_class
    from aeco_sync.edits import author
    from aeco_sync.convergence import compare_receipts
    from aeco_sync.stack import Session
    from ._ifc_utils import world, base_extrusion, pipe_profile, usys
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    print('== stage: import native drivers', flush=True)
    cls = host_class('ifc')
    session = cls.initialize(str(model), str(source), directory / 'session', policy='disconnect', kind_import=True)
    native = engine.adapter(session, 'ifc')
    try:
        walls = [w for w in native.f.by_type('IfcWall') if base_extrusion(w)]
        pipes = [p for p in native.f.by_type('IfcPipeSegment')
                 if pipe_profile(p)]
        if not walls or not pipes:
            raise ValueError('Round trip needs a wall and an extruded circular pipe')
        wall_positions = [world(w)[:3,3] for w in walls]
        pipe_positions = [world(p)[:3,3] for p in pipes]
        distances = np.linalg.norm(np.array(wall_positions)[:,None,:] - np.array(pipe_positions)[None,:,:],axis=2)
        wi, pi = np.unravel_index(np.argmin(distances),distances.shape)
        wall, pipe = walls[wi], pipes[pi]
        selected = {kind: dict(ref=e.GlobalId, path=str(native.path_for(e))) for kind,e in [('wall',wall),('pipe',pipe)]}
    finally:
        native.close()
    current = session.current()
    wp = current.GetPrimAtPath(selected['wall']['path'])
    pp = current.GetPrimAtPath(selected['pipe']['path'])
    height = wp.GetAttribute('aeco:wall:height').Get()
    length = np.linalg.norm(np.array(pp.GetAttribute('aeco:axis:end').Get()) - np.array(pp.GetAttribute('aeco:axis:start').Get()))
    # Disconnect the edited endpoint explicitly; other connected routes stay put.
    from pxr import Sdf, Usd
    wall_property = Sdf.Path(selected['wall']['path']).AppendProperty('aeco:wall:height')
    session.capture_base([wall_property])
    with Usd.EditContext(session.stage, session.intent):
        session.stage.GetAttributeAtPath(wall_property).Set(height + .15)
    session.intent.Save()
    author(session, selected['pipe']['path'], ['length=' + str(length + .2)])
    if review is not None:
        review_layer(session.intent, Path(review) / 'session/intent.usda')
    print('== stage: apply wall and pipe intent', flush=True)
    result = engine.apply(session, 'ifc')
    if result['accepted'] != 2 or result['pending']:
        raise AssertionError(result['diagnostics'])
    exported = Path(session.document('ifc'))
    reimport = directory / 'reimport'
    reimport.mkdir()
    code = 'import sys;from usdaeco_ifc.convert import convert;convert(sys.argv[1],sys.argv[2],threads=2)'
    print('== stage: reimport exported IFC', flush=True)
    python(['-c', code, str(exported), str(reimport / 'model.usda')],check=True,capture_output=True,text=True,timeout=240)
    imported_session = cls.initialize(str(reimport / 'model.usda'), str(exported), reimport / 'session',kind_import=True)
    ids = [row['ref'] for row in selected.values()]
    print('== stage: compare reimported USD drivers and bodies', flush=True)
    original_host = engine.adapter(session,'ifc')
    try:
        before = original_host.readback(ids)
    finally:
        original_host.close()
    from pxr import UsdGeom
    imported = imported_session.current()
    by_id = {p.GetAttribute('aeco:id').Get():p for p in imported.Traverse()
             if p.GetAttribute('aeco:id') and p.GetAttribute('aeco:id').Get()}
    after = dict(touched=[],meshes={})
    cache = UsdGeom.XformCache()
    for row in before['touched']:
        prim = by_id[row['id']]
        drivers = {name:prim.GetAttribute(name).Get() for name in row['drivers'] if prim.GetAttribute(name)}
        mesh = UsdGeom.Mesh(prim.GetChild('Geom'))
        if not mesh or any(n != 3 for n in mesh.GetFaceVertexCountsAttr().Get()):
            raise AssertionError('Reimported triangulated body is missing')
        after['touched'].append(dict(row,path=str(prim.GetPath()),drivers=drivers,
                                    matrix=cache.GetLocalToWorldTransform(prim)))
        after['meshes'][row['ref']] = dict(verts=[float(c) for p in mesh.GetPointsAttr().Get() for c in p],
                                          faces=list(mesh.GetFaceVertexIndicesAttr().Get()))
    comparison = compare_receipts(before,after)
    if not comparison['driversConverged'] or comparison['diagnostics']:
        raise AssertionError(comparison)
    repeat = engine.apply(session,'ifc')
    if repeat['mutations']:
        raise AssertionError('Repeat apply mutated the native document')
    # Native correspondence and imported stage drivers must both agree.
    imported = imported_session.current()
    for row in before['touched']:
        prim = next(p for p in imported.Traverse() if p.GetAttribute('aeco:id') and p.GetAttribute('aeco:id').Get() == row['id'])
        for field in ('aeco:wall:height',) if row['kind']=='wall' else ('aeco:axis:start','aeco:axis:end'):
            value = prim.GetAttribute(field).Get()
            if not np.allclose(value,row['drivers'][field],atol=1e-6):
                raise AssertionError('Reimported driver differs: ' + field)
    (directory/'convergence.json').write_text(json.dumps(comparison,indent=2,default=list)+'\n')
    if review is not None:
        for name in ('result.ifc.usda', 'diagnostics.ifc.usda', 'derived.usda', 'kind.usda'):
            review_layer(session.layer(name), Path(review) / 'session' / name)
        for name in ('kind.usda', 'derived.usda'):
            review_layer(imported_session.layer(name), Path(review) / 'reimport' / name)
    for row in selected.values():
        identity = next(item['id'] for item in before['touched'] if item['ref'] == row['ref'])
        row['id'] = identity
        row['path'] = str(by_id[identity].GetPath())
    return imported_session, comparison, selected
