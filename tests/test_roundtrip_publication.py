"""Public review opinions preserve authored data without live transaction state."""
import pytest
from pxr import Sdf
from usdaeco_ifc.roundtrip import review_layer


@pytest.mark.parametrize('explicit', [False, True])
def test_review_layer_preserves_opinions_and_source(tmp_path, explicit):
    source = Sdf.Layer.CreateAnonymous()
    source.customLayerData = {'aeco:sync:time': 'transaction time'}
    prim = Sdf.CreatePrimInLayer(source, '/Wall')
    apis = ['AecoElementAPI', 'AecoWallAPI', 'AecoHostBindingAPI:ifc']
    prim.SetInfo('apiSchemas', Sdf.TokenListOp.CreateExplicit(apis) if explicit
                 else Sdf.TokenListOp.Create(prependedItems=apis))
    Sdf.AttributeSpec(prim, 'aeco:wall:height', Sdf.ValueTypeNames.Double).default = 3.15
    Sdf.AttributeSpec(prim, 'aeco:host:ifc:document', Sdf.ValueTypeNames.String).default = 'live.ifc'
    before = source.ExportToString()
    review = review_layer(source, tmp_path / 'review.usda')
    assert source.ExportToString() == before
    assert not review.customLayerData
    assert review.GetAttributeAtPath('/Wall.aeco:wall:height').default == 3.15
    assert not review.GetAttributeAtPath('/Wall.aeco:host:ifc:document')
    applied = review.GetPrimAtPath('/Wall').GetInfo('apiSchemas').GetAppliedItems()
    assert applied == ['AecoElementAPI', 'AecoWallAPI']
