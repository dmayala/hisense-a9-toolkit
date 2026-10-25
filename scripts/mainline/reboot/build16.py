#!/usr/bin/env python3
"""One latest-main INTEG16: encoder AVC+HEVC sweep and INTEG15 crypto evidence."""
import importlib.util
from pathlib import Path
HERE = Path(__file__).resolve().parent
REQUIRED = ('5f12149', '041cc50', '575eddb', '79d1928', '4ee369e',
            '3e731aa', 'f36a62f', '05b2f8d', '165d734', '639358d',
            '8e2492c', '0c4218c', '06942f6')
if __name__ == '__main__':
    spec = importlib.util.spec_from_file_location('integ_assembly', HERE/'build14.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.main(generation=16, extra_required=REQUIRED)
