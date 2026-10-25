#!/usr/bin/env python3
"""Apply the ADSP patch privately and execute production gate/registration code."""
import argparse
import hashlib
from pathlib import Path
import re
import resource
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
PATCH = HERE / 'patches/0001-remoteproc-qcom-a9-adsp-manual-start.patch'
PAS = Path('drivers/remoteproc/qcom_q6v5_pas.c')


def function(source, name):
    match = re.search(r'^(?:static )?(?:int|void) ' + name + r'\([^;]*?\)\n\{', source, re.M)
    if not match:
        raise AssertionError('missing function ' + name)
    opening = source.index('{', match.start())
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[match.start():end]


def source_checks(pas, core):
    assert 'static bool a9_adsp_manual_start;' in pas, 'gate must default off'
    assert 'module_param(a9_adsp_manual_start, bool, 0400);' in pas, 'gate must be read-only'
    probe = function(pas, 'qcom_pas_probe')
    gate = probe.index('ret = qcom_pas_a9_apply_manual_start(')
    assert probe.index('rproc = devm_rproc_alloc(') < gate
    for later in ('qcom_q6v5_init(', 'qcom_add_sysmon_subdev(', 'rproc_add(rproc)'):
        assert gate < probe.index(later), 'gate must precede IRQs/subdevices/publication'
    after = probe[gate:]
    assert 'rproc->auto_boot =' not in after, 'gate must not be overwritten'
    assert 'rproc->recovery_disabled =' not in after
    assert 'rproc->dump_conf =' not in after
    assert '\n\tif (ret)\n\t\treturn ret;\n' in after[:after.index('pas = rproc->priv;')]
    assert '\trproc->state = RPROC_OFFLINE;' in core[core.index('struct rproc *rproc_alloc('):]
    return probe


def render(pas, core):
    probe = source_checks(pas, core)
    preamble = probe[probe.index('\trproc->auto_boot = desc->auto_boot;'):
                     probe.index('\trproc_coredump_set_elf_info(')]
    template = (HERE / 'manual-start-host.c.in').read_text()
    values = {
        'HELPER': function(pas, 'qcom_pas_a9_apply_manual_start'),
        'CALLBACK': function(core, 'rproc_auto_boot_callback'),
        'TRIGGER': function(core, 'rproc_trigger_auto_boot'),
        'ADD': function(core, 'rproc_add'),
        'PREAMBLE': preamble,
    }
    for key, value in values.items():
        template = template.replace('@' + key + '@', value)
    return template


def execute(source, directory, compiler, label):
    path = directory / (label + '.c')
    binary = directory / label
    path.write_text(source)
    subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-O2', str(path), '-o', str(binary)], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return subprocess.run([str(binary)], capture_output=True, text=True)


def main():
    # Negative controls deliberately abort; never leave large core dumps.
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, default=Path.home() / 'A9-build/mainline/linux-7.2.10-gpu')
    parser.add_argument('--cc', default='cc')
    args = parser.parse_args()
    old = (args.kernel / PAS).read_bytes()
    core = (args.kernel / 'drivers/remoteproc/remoteproc_core.c').read_text()
    with tempfile.TemporaryDirectory(prefix='a9-adsp-manual-') as tmp:
        directory = Path(tmp)
        target = directory / PAS
        target.parent.mkdir(parents=True)
        target.write_bytes(old)
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(PATCH)],
                       cwd=directory, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pas = target.read_text()
        run = execute(render(pas, core), directory, args.cc, 'nominal')
        assert run.returncode == 0, run.stderr
        print(run.stdout.strip())
        # Execute actual helper mutations; each must fail an invariant at runtime.
        mutations = {
            'autoboot': ('rproc->auto_boot = false;', 'rproc->auto_boot = true;'),
            'recovery': ('rproc->recovery_disabled = true;', 'rproc->recovery_disabled = false;'),
            'coredump': ('rproc->dump_conf = RPROC_COREDUMP_DISABLED;',
                         'rproc->dump_conf = RPROC_COREDUMP_INLINE;'),
            'board': ('!of_machine_is_compatible("hisense,hlte556n")', 'false'),
            'node': ('!of_device_is_compatible(dev->of_node, "qcom,sm6115-adsp-pas")', 'false'),
            'pasid': ('desc->pas_id != 1 || desc->early_boot', 'desc->early_boot'),
            'early': ('desc->pas_id != 1 || desc->early_boot', 'desc->pas_id != 1'),
        }
        for label, (before, after) in mutations.items():
            assert pas.count(before) == 1
            result = execute(render(pas.replace(before, after), core), directory, args.cc, label)
            assert result.returncode != 0, 'mutation survived: ' + label
        print(f'manual-start negative controls PASS: {len(mutations)} production-helper mutations rejected')
        core_guard = '\tif (rproc->auto_boot) {\n'
        assert core.count(core_guard) == 1
        result = execute(render(pas, core.replace(core_guard, '\tif (true) {\n')),
                         directory, args.cc, 'core-unconditional-autoboot')
        assert result.returncode != 0, 'unconditional rproc_add auto-boot mutation survived'
        print('manual-start registration negative control PASS: unconditional core auto-boot rejected')
        static_mutations = {
            'writable': pas.replace('bool, 0400);', 'bool, 0600);'),
            'default-on': pas.replace('static bool a9_adsp_manual_start;',
                                      'static bool a9_adsp_manual_start = true;'),
        }
        # Moving the hook after IRQ registration must fail source-order admission.
        hook = '\tret = qcom_pas_a9_apply_manual_start(&pdev->dev, desc, rproc);\n\tif (ret)\n\t\treturn ret;\n'
        assert hook in pas
        late = pas.replace(hook, '').replace('\tret = rproc_add(rproc);', hook + '\tret = rproc_add(rproc);')
        static_mutations['late-hook'] = late
        for label, mutated in static_mutations.items():
            try:
                source_checks(mutated, core)
            except AssertionError:
                pass
            else:
                raise AssertionError('source mutation survived: ' + label)
        print(f'manual-start source checks PASS: {len(static_mutations)} parameter/order mutations rejected')
        assert (args.kernel / PAS).read_bytes() == old, 'shared source changed'
        print('patch SHA256 ' + hashlib.sha256(PATCH.read_bytes()).hexdigest())
        print('patched PAS SHA256 ' + hashlib.sha256(pas.encode()).hexdigest())
    print('ADSP-MANUAL-TEST PASS (offline; not a phone admission)')


if __name__ == '__main__':
    main()
