from usdaeco_ifc.runtime import python as run_python
"""Exact generated expectations survive changes in the security design."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

from scenarios.datacentre import CASES, generator_root, prepare, resolve_expectations, run_case


@pytest.fixture(scope="module")
def changed_design(tmp_path_factory):
    directory = tmp_path_factory.mktemp("changed-design")
    root = generator_root()
    shutil.copytree(root / "spec", directory / "spec")
    script = """
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
from dcbuild.cli import main
raise SystemExit(main(['plan', '--out', 'out/build_plan.json']))
"""
    run_python([ "-c", script, str(root / "src")], cwd=directory,
                   env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
                   check=True, capture_output=True, text=True, timeout=30)
    original = json.loads((directory / "out/build_plan.json").read_text())
    security_path = directory / "spec/security.yaml"
    security = yaml.safe_load(security_path.read_text())
    security["corridor_cameras"]["max_spacing"] /= 2
    security_path.write_text(yaml.safe_dump(security, sort_keys=False))
    seed = prepare(directory / "seed", spec_directory=directory / "spec")
    expectations = resolve_expectations(seed)
    assert expectations["counts"]["cameras"] > len(original["cameras"])
    return seed


@pytest.mark.parametrize("case", CASES)
def test_changed_camera_population(changed_design, tmp_path, case):
    row = run_case(tmp_path, case, seed=changed_design)
    assert row["passed"] and row["repeatMutations"] == 0
    assert row["census"] == row["expectations"]["after"]


@pytest.mark.parametrize("key", ["cameras", "heads", "camera_types", "presets", "doors", "spaces"])
def test_inconsistent_manifest_is_rejected(changed_design, tmp_path, key):
    for name in ("out", "manifests"):
        shutil.copytree(changed_design / name, tmp_path / name)
    path = tmp_path / "manifests/resolved.json"
    manifest = json.loads(path.read_text())
    owner = manifest["counts"] if key in ("cameras", "doors", "spaces") else manifest
    owner[key] += 1
    path.write_text(json.dumps(manifest))
    with pytest.raises(AssertionError, match="Generator .* counts disagree"):
        resolve_expectations(tmp_path)


def test_wrong_target_identity_is_rejected(changed_design, tmp_path):
    for name in ("out", "manifests"):
        shutil.copytree(changed_design / name, tmp_path / name)
    path = tmp_path / "out/targets.json"
    targets = json.loads(path.read_text())
    targets["cameras"][0]["GlobalId"] = targets["cameras"][1]["GlobalId"]
    path.write_text(json.dumps(targets))
    with pytest.raises(AssertionError, match="Generator camera identities disagree"):
        resolve_expectations(tmp_path)


def test_missing_manifest_is_rejected(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out/build_plan.json").write_text("{}")
    with pytest.raises(FileNotFoundError):
        resolve_expectations(tmp_path)
