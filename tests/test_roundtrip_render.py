"""Regression coverage for the empty-looking roundtrip render."""
from itertools import product

import numpy as np
from pxr import Gf, Usd, UsdGeom

from usdaeco_check.images import write_png
from usdaeco_ifc.roundtrip import frame_camera, render_foreground


def test_gradient_has_no_foreground(tmp_path):
    image = np.broadcast_to(np.linspace(0, 1, 100)[:, None, None], (100, 200, 3)).copy()
    path = tmp_path / 'gradient.png'
    write_png(path, image)
    assert render_foreground(path)['foregroundPixels'] == 0


def test_normalized_foreground_coverage(tmp_path):
    image = np.zeros((100, 200, 3))
    image[20:40, 50:100] = (.82, .58, .30)
    path = tmp_path / 'body.png'
    write_png(path, image)
    result = render_foreground(path)
    assert result['foregroundPixels'] == 1000
    assert result['foregroundFraction'] == .05
    assert result['horizontalRange'] > 20 / 255


def test_camera_contains_translated_body_bounds():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    parent = UsdGeom.Xform.Define(stage, '/Element')
    parent.AddTranslateOp().Set(Gf.Vec3d(260, -140, 30))
    mesh = UsdGeom.Mesh.Define(stage, '/Element/Geom')
    mesh.CreateExtentAttr([(-2, -.1, 0), (2, .1, 7.15)])
    mesh.CreatePurposeAttr('render')
    camera = UsdGeom.Camera.Define(stage, '/Renders/edited_corner')
    camera.CreateFocalLengthAttr(48)
    result = frame_camera(stage, ['/Element'])
    value = camera.GetCamera()
    projection = value.frustum.ComputeViewMatrix() * value.frustum.ComputeProjectionMatrix()
    for corner in product(*zip(*result['bounds'])):
        assert all(-1 <= v <= 1 for v in projection.Transform(Gf.Vec3d(*corner)))
    assert 0 < value.clippingRange.GetMin() < value.clippingRange.GetMax()
    assert result['allCornersInside']
