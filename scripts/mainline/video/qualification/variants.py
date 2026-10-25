"""Opt-in A/B/C/D startup comparison within the existing B-boot sweep owner."""
import copy
import importlib.util
import json
from pathlib import Path
import re
import time

HERE = Path(__file__).resolve().parent
# Match the ranking in mainline-venus-hfi-causality.md. EOS stays mapped.
CASES = ((1, 1, 1), (2, 1, 0), (3, 0, 1), (4, 0, 0))
EXTRA_RESERVE = 480  # Three additional bounded attempts; owner hold never grows.


def parameters(eos, output, work):
    script = 'set -e\np=/sys/module/venus_enc/parameters\n'
    for name, value in (('a9_mapped_eos', eos), ('a9_output_size', output),
                        ('a9_stock_work_mode', work)):
        want = 'Y' if value else 'N'
        script += f'''q="$p/{name}"
[ -w "$q" ]
printf '%s\\n' {want} > "$q"
case "$(cat "$q")" in Y|1) got=1;; N|0) got=0;; *) exit 1;; esac
[ "$got" = {value} ]
printf 'HVENC_PARAMETER {name} %s\\n' "$got"
'''
    return script


def run(s, qualify=None):
    if getattr(s, 'hvenc_qualification_attempted', False):
        return
    if qualify is None:
        spec = importlib.util.spec_from_file_location('variant_single', HERE/'run.py')
        single = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(single)
        qualify = single.run
    out, end = s.out, s.qualification_end
    root = out/'hvenc-variants'
    root.mkdir(mode=0o700, exist_ok=True)
    cases, reset_ok = [], False
    # This is the original hold's qualification budget, with reset time retained.
    work_end = end-12
    s.hvenc_qualification_attempted = True
    try:
        for case, output, work in CASES:
            row = dict(case=case, mapped_eos=1, output_size=output,
                       stock_work_mode=work, attempted=0, parameters_ok=0,
                       budget_ok=int(work_end-time.monotonic() >= 160), passed=0)
            cases.append(row)
            if not row['budget_ok']:
                continue
            print(f'HVENC_VARIANT case={case} phase=1 output_size={output} stock_work_mode={work} mapped_eos=1', flush=True)
            s.command_end = min(work_end, time.monotonic()+10)
            try:
                rc, text = s.native(parameters(1, output, work), timeout=10)
            except InterruptedError:
                raise
            except (OSError, ValueError):
                rc, text = 1, ''
            expected = {'a9_mapped_eos': 1, 'a9_output_size': output,
                        'a9_stock_work_mode': work}
            actual = {name: int(value) for name, value in re.findall(
                r'^HVENC_PARAMETER (a9_mapped_eos|a9_output_size|a9_stock_work_mode) ([01])$',
                text, re.M)}
            row['parameters_ok'] = int(rc == 0 and actual == expected)
            if not row['parameters_ok']:
                print(f'HVENC_VARIANT case={case} phase=2 parameters_ok=0', flush=True)
                continue
            s.out = root/('case-'+str(case))
            s.out.mkdir(mode=0o700)
            # The qualifier admits against the owner's real remaining window and
            # bounds each attempt itself with command_end=now+140. Passing
            # now+145 here races its >=145 admission check (integ27:144999ms).
            s.qualification_end = work_end
            s.hvenc_qualification_attempted = False
            # A new native process/codec is created by the ordinary qualifier.
            try:
                qualify(s)
                result = copy.deepcopy(s.results['18_hardware_encoder'])
                row.update(attempted=int(result['evidence'].get('attempted', 0)),
                           passed=int(result['status'] == 'PASS'), result=result)
            except InterruptedError:
                raise
            except (OSError, ValueError, KeyError, AssertionError):
                row['internal_error'] = 1
            finally:
                s.out, s.qualification_end = out, end
                s.hvenc_qualification_attempted = True
            print(f"HVENC_VARIANT case={case} phase=2 attempted={row['attempted']} passed={row['passed']}", flush=True)
    finally:
        s.out, s.qualification_end = out, end
        s.hvenc_qualification_attempted = True
        s.command_end = end
        reset_interrupted = False
        try:
            rc, text = s.native(parameters(0, 0, 0), timeout=10)
        except InterruptedError:
            rc, text, reset_interrupted = 1, '', True
        except (OSError, ValueError):
            rc, text = 1, ''
        reset_ok = rc == 0 and all(re.search(
            r'^HVENC_PARAMETER '+name+r' 0$', text, re.M)
            for name in ('a9_mapped_eos', 'a9_output_size', 'a9_stock_work_mode'))
        result = dict(cases=cases, reset_ok=int(reset_ok), phone_boots_requested=0)
        path = out/'hvenc-variants.json'
        path.write_text(json.dumps(result, sort_keys=True)+'\n')
        path.chmod(0o600)
        passed = sum(row['passed'] for row in cases)
        s.put('18_hardware_encoder', passed > 0 and reset_ok, qualification=1,
              variants=1, attempted=sum(row['attempted'] for row in cases),
              cases=len(cases), cases_passed=passed, parameters_restored=int(reset_ok))
        if reset_interrupted:
            raise InterruptedError()
