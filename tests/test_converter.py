from usdaeco_ifc.runtime import python as run_python
import json
from pathlib import Path
import subprocess
import sys

import pytest

from converter_cases import CASES


def probe(tmp_path, case):
    result = run_python([ str(Path(__file__).with_name("converter_probe.py")),
                             str(tmp_path), case],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize("case", CASES)
def test_converter_contract(tmp_path, case):
    claim = probe(tmp_path, case)
    assert claim["ok"], claim["detail"]


def test_probe_ignores_existing_artifacts(tmp_path):
    residue = {tmp_path / ("ifc4x3" + suffix): b"unrelated prior output\n"
               for suffix in (".ifc", ".usda", ".semantics.usda", ".geometry.usdc")}
    for path, content in residue.items():
        path.write_bytes(content)
    for _ in range(2):
        claim = probe(tmp_path, "determinism")
        assert claim["ok"], claim["detail"]
        assert {p: p.read_bytes() for p in tmp_path.iterdir()} == residue
