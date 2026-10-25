#!/usr/bin/env python3
"""Host-only INTEG15, using the established native kernel/vendor assembly.

Build kernel with build14-kernel.py --base integ14 --out integ15-host/kernel;
reuse the unchanged integ14-host/msm module. Refresh the FBE host receipt after
wrapped worker edits. Both launchers use only adjacent images/runtime/assets.
"""
import importlib.util
from pathlib import Path
HERE=Path(__file__).resolve().parent
REQUIRED=('3e731aa','f36a62f','05b2f8d','165d734','639358d','8e2492c','0c4218c','06942f6')
if __name__=='__main__':
    spec=importlib.util.spec_from_file_location('integ_assembly',HERE/'build14.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.main(generation=15,extra_required=REQUIRED)
