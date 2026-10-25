#!/usr/bin/env python3
"""Inherited D1/S1 fake-transport fault/admission suite against active host."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
fixture = ROOT / 'tests/mainline-sensors/test-s1-host.py'
source = fixture.read_text()
source = source.replace("str(ROOT/'scripts/mainline/sensors/s1-derive.py')",
                        "str(ROOT/'scripts/mainline/sensors/s1-active-derive.py')")
source = source.replace('S1-ENUMERATION-ONLY-OFFLINE', 'S1-PASS-ONE-START-SENSOR-UP-SNS-RAM-REGISTRY-OFFLINE')
exec(compile(source, str(fixture), 'exec'), {'__file__': str(fixture), '__name__': '__main__'})
