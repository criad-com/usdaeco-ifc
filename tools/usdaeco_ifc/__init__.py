"""IFC converter and native host integration."""
__version__ = "0.2.2"

import os
from pathlib import Path
import sys
_core = Path(os.environ.get('AECO_CORE_ROOT', Path(__file__).resolve().parents[3] / 'usdaeco-core'))
if (_core / 'tools/usdaeco_tools').is_dir():
    sys.path.insert(0, str(_core / 'tools'))


def register_plugins():
    """Load the converter's core and axis schemas after native tessellation."""
    from pxr import Plug
    from usdaeco_check.plugins import check_requirements, discover
    axis = Path(os.environ.get('AECO_AXIS_ROOT', _core.parent / 'usdaeco-axis'))
    paths = [os.environ.get('CORE_PLUGIN_DIR', str(_core / 'out/plugins/usdAeco/resources')),
             os.environ.get('AXIS_PLUGIN_DIR', str(axis / 'out/plugins/usdAecoAxis/resources'))]
    plugins = discover(paths)
    metadata = {name: plugin.info['aeco'] for name, plugin in plugins.items()}
    requirements = {'usdAeco': '>=0.9,<1.0', 'usdAecoAxis': '>=0.1,<0.2'}
    contract = dict(version=__version__, tier='integration', requires=requirements)
    check_requirements({**metadata, 'usdaeco-ifc': contract})
    registry = Plug.Registry()
    for path in paths:
        registry.RegisterPlugins(str(path))
    active = {name: registry.GetPluginWithName(name).metadata['aeco'] for name in requirements}
    check_requirements({**active, 'usdaeco-ifc': contract})
