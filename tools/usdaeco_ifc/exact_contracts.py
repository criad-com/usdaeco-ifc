"""S04 compatibility for the two case-sensitive native kit repository names.

Toolchain v0.3.2 permits lowercase repository names only. Keep its complete
pin/range checks, normalizing only the two declared kit names in a copy.
No manifest, dependency range or toolchain module is modified.
"""
from copy import deepcopy
import json
from pathlib import Path


def check_pins(document, requires):
    from usdaeco_check.contracts import pins, require
    normalized = deepcopy(document)
    for pin in normalized['repos'].values():
        if pin.get('repo') in ('usdSolid', 'usdSolidOcct'):
            require(pin.get('library') == pin['repo'], 'native kit library/repository mismatch')
            pin['repo'] = pin['repo'].lower()
    return pins(normalized, requires)


def check_structure(root, **kwargs):
    from usdaeco_check.structure import check_structure as standard
    from usdaeco_check.report import Result
    root = Path(root)
    results = standard(root, **kwargs)
    for index, row in enumerate(results):
        if row.name != 'S04':
            continue
        try:
            check_pins(json.loads((root/'dependencies.json').read_text()), json.loads((root/'library.json').read_text())['requires'])
            results[index] = Result('S04', True, 'all pins/ranges checked; native kit name compatibility (stock S04 rejects uppercase)')
        except Exception as error:
            results[index] = Result('S04', False, str(error))
    return results
