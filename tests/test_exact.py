"""The optional exact route runs outside the ordinary converter's USD ABI."""
import json
import os
from pathlib import Path
import pytest
from usdaeco_ifc.exact_runtime import RuntimeUnavailable, run_native, runtime_paths

HERE = Path(__file__).resolve().parent


def test_converter_sources_match_recorded_release():
    import hashlib
    wanted=json.loads((HERE/'fixtures/exact/default-converter.json').read_text())
    root=HERE.parent
    assert {str(p.relative_to(root)) for p in (root/'tools/usdaeco_ifc/convert').glob('*.py')}==set(wanted)
    assert all(hashlib.sha256((root/name).read_bytes()).hexdigest()==digest for name,digest in wanted.items())


def test_runtime_absence_is_explicit(monkeypatch):
    monkeypatch.delenv("USD_SOLID_OCCT_RUNTIME", raising=False)
    with pytest.raises(RuntimeUnavailable, match="USD_SOLID_OCCT_RUNTIME"):
        runtime_paths()


def test_broken_configured_runtime_fails(monkeypatch, tmp_path):
    monkeypatch.setenv('USD_SOLID_OCCT_RUNTIME', str(tmp_path))
    with pytest.raises(RuntimeError, match='paths.json'):
        runtime_paths()


def test_native_pin_compatibility_keeps_range_and_name_checks():
    from usdaeco_ifc.exact_contracts import check_pins
    source = dict(repos=dict(solid=dict(repo='usdSolid', library='usdSolid', ref='v0.1.0')))
    assert check_pins(source, {'usdSolid': '>=0.1,<0.2'})
    assert source['repos']['solid']['repo'] == 'usdSolid'
    with pytest.raises(ValueError, match='outside declared range'):
        check_pins(source, {'usdSolid': '>=0.2,<0.3'})
    source['repos']['solid']['repo'] = 'UnexpectedKit'
    with pytest.raises(ValueError, match='invalid repository name'):
        check_pins(source, {'usdSolid': '>=0.1,<0.2'})


def test_native_ifc_wall_and_pipe():
    if not os.environ.get("USD_SOLID_OCCT_RUNTIME"):
        pytest.skip("Optional exact runtime is absent; exact acceptance is NOT RUN")
    rows = json.loads(run_native(HERE / "exact_native_probe.py", HERE / "fixtures/exact").splitlines()[-1])
    assert {r["name"] for r in rows} == {"wall", "pipe"}
    assert all(r["volume"] > 0 and r["tolerance"] > 0 for r in rows)
