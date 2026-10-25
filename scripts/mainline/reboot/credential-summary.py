#!/usr/bin/env python3
"""Read-only sweep credential summary. Normal output has integer fields only."""
import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('rpmb_evidence', HERE / 'rpmb-evidence.py')
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)
PIN_FIELDS = ('attempted', 'preconditions', 'set_completed', 'set', 'verified',
              'locked', 'unlocked', 'removed', 'finally_unsecured',
              'cleanup_attempted', 'cleanup_ok', 'interrupted_or_exception')
STATUS = {'FAIL': 0, 'PASS': 1, 'NOT_RUN': 2}
GK = 'android.hardware.gatekeeper@1.0::igatekeeper/default'


def emit(tag, **fields):
    assert all(re.fullmatch(r'[a-z][a-z0-9_]*', k) and type(v) is int for k, v in fields.items())
    print(tag + ' ' + ' '.join(k + '=' + str(v) for k, v in fields.items()))


def regular(path):
    return path.is_file() and not path.is_symlink()


def lines(path, errors):
    try:
        with path.open(errors='replace') as stream:
            yield from stream
    except OSError:
        errors[0] += 1


def pin_result(root, errors):
    path = root / 'acceptance.json'
    if regular(path):
        try:
            obj = json.loads(path.read_text())
            row = obj.get('29_pin_lifecycle') if isinstance(obj, dict) else None
            if isinstance(row, dict):
                return row, 1
        except (OSError, ValueError):
            errors[0] += 1
    # The adjacent owner stdout can survive an interrupted JSON collection.
    for path in (root / 'sweep.log', root.parent / (root.name + '.out')):
        if not regular(path):
            continue
        found = None
        for line in lines(path, errors):
            match = re.fullmatch(r'29_pin_lifecycle (PASS|FAIL|NOT_RUN) (?:reason=\S+ )?(\{.*\})\s*', line)
            if not match:
                continue
            try:
                data = json.loads(match[2])
                if isinstance(data, dict):
                    found = {'status': match[1], 'evidence': data}
            except ValueError:
                errors[0] += 1
        if found is not None:
            return found, 2
    return {}, 0


def public_fields(record):
    return {m[1]: int(m[2]) for token in record.split()[1:]
            if (m := re.fullmatch(r'([a-z_]+)=(-?[0-9]+)', token))}


def summarize_boot(root, boot, errors):
    logcat = [p for name in ('logcat.log.prev', 'logcat.log') if regular(p := root / name)]
    if not logcat:
        logcat = [p for name in ('logcat.txt', 'qseecomd-logcat.log') if regular(p := root / name)]
        logcat = logcat[:1]  # Do not count a derived daemon extract again.
    kernel = [p for name in ('kernel.early.log', 'kernel.log.prev', 'kernel.log') if regular(p := root / name)]
    ta = [p for name in ('qsee_log.prev', 'qsee_log', 'tz_log') if regular(p := root / name)]
    emit('A9_CREDENTIAL_LOGS', boot=boot, logcat_files=len(logcat), kernel_files=len(kernel), ta_files=len(ta))
    public = set()
    opens = Counter()
    gk = Counter()
    km = Counter()
    for path in kernel + logcat:
        for line in lines(path, errors):
            record = evidence.public_record(line)
            if record:
                if record.startswith('A9_SFS_OPEN '):
                    if path in logcat:
                        f = public_fields(record)
                        opens[(f['root'], f['errno'], f['result'], f['access'], f['create'])] += 1
                else:
                    public.add(record)
            low = line.lower()
            if 'gatekeeper' in low:
                gk['start'] += int('starting service' in low and 'vendor.gatekeeper-1-0' in low)
                gk['exits'] += int('vendor.gatekeeper-1-0' in low and ('exited with status' in low or 'killed by signal' in low))
                gk['running'] += int(bool(re.search(r'init\.svc\.vendor\.gatekeeper-1-0(?:\]|\s|:|=).*\brunning\b', low)))
                gk['registered'] += int(GK in low and bool(re.search(r'\bregistered\b', low))
                                        and not re.search(r'failed|unable|cannot|not\s+registered', low))
                gk['link_failed'] += int(bool(re.search(r'cannot link executable|dlopen failed|could not load|cannot locate symbol', low)))
            if path in logcat:
                km['m10003'] += len(re.findall(r'Error::Km\(\s*-10003\s*\)', line))
                km['m67'] += len(re.findall(r'Error::Km\(\s*-67\s*\)', line))
                km['fallback'] += int('falling back to non-rollback' in low)
    for path in ta:
        for line in lines(path, errors):
            km['ta_read_failed'] += int('qsee_stor_read_sectors' in line and 'failed' in line)
            # Stock uses function:line:rc, without the word "failed". -56
            # identifies its full cached-table scan, not a guard rejection.
            result = re.search(r'\bkm_add_key_id:[0-9]+:(-[0-9]+)\s*$', line)
            km['ta_key_id_failed'] += int(bool(result) or ('km_add_key_id' in line and 'failed' in line.lower()))
            km['ta_key_id_full'] += int(bool(result) and int(result[1]) == -56)
    ran = bool(gk['running'] or gk['registered'])
    start = 3 if ran and gk['exits'] else 1 if ran else 0 if gk['exits'] else 2 if gk['start'] else -1
    link = 2 if gk['registered'] and gk['link_failed'] else 1 if gk['registered'] else 0 if gk['link_failed'] else -1
    emit('A9_GATEKEEPER', boot=boot, start=start, link=link, start_records=gk['start'],
         running_records=gk['running'], registered_records=gk['registered'],
         exit_records=gk['exits'], link_failed_records=gk['link_failed'])
    seen = Counter()
    for record in sorted(public):
        tag = record.split()[0]
        f = public_fields(record)
        if tag == 'A9_RPMB_REGISTER' and 'phase' in f:
            tag = 'A9_RPMB_PHASE'
        elif tag == 'A9_RPMB_SCM':
            if 'transport' not in f or 'secure_status' not in f:
                continue
        elif tag not in ('A9_PERSIST_RO', 'A9_RPMB_CALLBACK', 'A9_RPMB_QUERY',
                         'A9_KMTB_WRITE', 'A9_KMTB_READ', 'A9_RPMB_GUARD', 'A9_RPMB_MAP', 'A9_RPMB_REJECT', 'A9_RPMB_RESULT',
                         'A9_RPMB_REQUEST_TRACE', 'A9_RPMB_WRITE_TRACE', 'A9_RPMB_BOOTSTRAP',
                         'A9_RPMB_BACKEND', 'A9_RPMB_BACKEND_LIMIT'):
            continue
        emit(tag, boot=boot, **f)
        seen[tag] += 1
    for tag in ('A9_PERSIST_RO', 'A9_RPMB_PHASE'):
        if not seen[tag]:
            emit(tag, boot=boot, records=0)
    for root_id in (1, 2, 3):
        errnos = sorted({k[1] for k in opens if k[0] == root_id})
        for number in errnos or [-1]:
            rows = [(k, n) for k, n in opens.items() if k[:2] == (root_id, number)]
            emit('A9_SFS_OPEN', boot=boot, root=root_id, errno=number,
                 count=sum(n for _, n in rows), failed=sum(n for k, n in rows if k[2] == 0),
                 reads=sum(n for k, n in rows if k[3] == 0),
                 writes=sum(n for k, n in rows if k[3] in (1, 2)),
                 creates=sum(n for k, n in rows if k[4] == 1))
    emit('A9_KEYSTORE', boot=boot, km_m10003=km['m10003'], km_m67=km['m67'],
         rollback_fallback=km['fallback'], ta_read_failed=km['ta_read_failed'],
         ta_key_id_failed=km['ta_key_id_failed'], ta_key_id_full=km['ta_key_id_full'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sweep_dir', type=Path)
    args = parser.parse_args()
    root = args.sweep_dir
    errors = [0]
    if not root.is_dir():
        emit('A9_CREDENTIAL_SUMMARY', valid=0, boots=0, read_errors=1)
        return 2
    row, source = pin_result(root, errors)
    stages = row.get('evidence', {})
    if not isinstance(stages, dict):
        stages = {}
    fields = {name: int(v) if type(v := stages.get(name)) in (int, bool) and v in (0, 1) else -1
              for name in PIN_FIELDS}
    status = row.get('status')
    emit('A9_PIN', verdict=STATUS.get(status, -1) if isinstance(status, str) else -1, source=source, **fields)
    try:
        boots = sorted((p for p in root.iterdir() if re.fullmatch(r'boot-[0-9]+', p.name)
                        and p.is_dir() and not p.is_symlink()), key=lambda p: int(p.name[5:]))
    except OSError:
        errors[0] += 1
        boots = []
    if not boots:
        boots = [root / 'manual'] if (root / 'manual').is_dir() and not (root / 'manual').is_symlink() else [root]
    for number, path in enumerate(boots, 1):
        summarize_boot(path, number, errors)
    emit('A9_CREDENTIAL_SUMMARY', valid=1, boots=len(boots), read_errors=errors[0])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
