"""Analytic synthetic contract tests; no device acquisition or real logs."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'epd_log_report.py'
SPEC = importlib.util.spec_from_file_location('epd_log_report', SCRIPT)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def line(time, message, pid=10, tid=20, day='10-12'):
    return f'{day} {time} {pid} {tid} D SDM : epd:: {message}\n'


def marker(time, message, **kwargs):
    return line(time, message, **kwargs).replace('epd:: ', '', 1)


def metadata(time, mode='0x206', clear=0, **kwargs):
    return line(time, f'clear={clear},temp=26,27,mode={mode}', **kwargs)


def total(time, frames=8, **kwargs):
    return line(time, f'------------------ total frame num={frames}', **kwargs)


def report(text, **kwargs):
    return M.report(text.encode(), kwargs.pop('year', 2026), **kwargs)


class TimestampTests(unittest.TestCase):
    def test_subsecond_start_stop(self):
        text = (total('04:00:00.049') + total('04:00:00.050') + total('04:00:00.051')
                + total('04:00:00.750') + total('04:00:00.751'))
        r = report(text, start=M.iso_ns('2026-10-12T04:00:00.050'), stop=M.iso_ns('2026-10-12T04:00:00.750'))
        self.assertEqual(r['summary']['engine_batch_events'], 2)
        self.assertEqual([v['total']['line'] for v in r['updates']], [2, 3])
        self.assertEqual(r['summary']['reported_waveform_frames'], 16)

    def test_prior_day_and_after_stop(self):
        text = total('23:59:59.000', day='10-11') + total('04:00:01.000') + total('04:00:05.000')
        r = report(text, start=M.iso_ns('2026-10-12T04:00:00'), stop=M.iso_ns('2026-10-12T04:00:03'))
        self.assertEqual(r['summary']['engine_batch_events'], 1)
        self.assertEqual(r['updates'][0]['total']['line'], 2)

    def test_midnight(self):
        text = total('23:59:59.990') + total('00:00:00.010', day='10-13')
        r = report(text, start=M.iso_ns('2026-10-12T23:59:59.990'), stop=M.iso_ns('2026-10-13T00:00:00.011'))
        self.assertEqual(r['summary']['engine_batch_events'], 2)
        self.assertEqual(r['summary']['inter_batch_log_gaps_by_pid_tid']['10:20']['median_ns'], 20_000_000)

    def test_year_rollover_reference_first_record(self):
        text = total('23:59:59.999', day='12-31') + total('00:00:00.001', day='01-01')
        r = report(text, start=M.iso_ns('2027-01-01T00:00:00'), stop=M.iso_ns('2027-01-01T00:00:01'))
        self.assertEqual(r['summary']['engine_batch_events'], 1)
        self.assertEqual(r['updates'][0]['total']['timestamp'], '2027-01-01T00:00:00.001000000')
        self.assertEqual(r['diagnostics'][0]['kind'], 'inferred_year_rollover')

    def test_invalid_rollover_does_not_advance_year(self):
        text = total('23:59:59.999', day='12-31') + total('00:00:00.000', day='01-32') + total('00:00:00.001', day='01-01')
        r = report(text)
        self.assertEqual(r['parse_counts']['invalid_timestamp_lines'], 1)
        self.assertEqual(r['updates'][-1]['total']['timestamp'][:4], '2027')
        self.assertEqual([d['kind'] for d in r['diagnostics']], ['invalid_timestamp_breaks_context', 'inferred_year_rollover'])

    def test_nanosecond_precision(self):
        start = M.iso_ns('2026-10-12T04:00:00.123456789')
        r = report(total('04:00:00.123456788') + total('04:00:00.123456789') + total('04:00:00.123456790'), start=start, stop=start+1)
        self.assertEqual(r['summary']['engine_batch_events'], 1)
        self.assertEqual(r['updates'][0]['total']['timestamp_ns'], start)

    def test_discontinuity_cancels_pair_and_gaps(self):
        text = metadata('04:00:02.000') + total('04:00:01.000') + total('04:00:01.010')
        r = report(text)
        self.assertEqual(r['updates'][0]['association'], 'clock_discontinuity')
        self.assertEqual(r['updates'][1]['association'], 'total_without_metadata')
        self.assertEqual(r['parse_status'], 'partial_or_discontinuous')
        self.assertEqual(r['summary']['inter_batch_log_gaps_by_pid_tid']['10:20']['n'], 1)

    def test_bad_timestamp_formats_and_window(self):
        for value in ('04:00:00.1', '2026-10-12T04:00:00Z', '2026-02-30T04:00:00', '2026-10-12T04:00:00.1234567890'):
            with self.subTest(value=value), self.assertRaises(M.InputError):
                M.iso_ns(value)
        with self.assertRaises(M.InputError):
            report(total('04:00:00.100'), start=2, stop=2)


class AssociationTests(unittest.TestCase):
    def test_mixed_pids_and_tids_do_not_pair(self):
        text = (metadata('04:00:00.100', pid=1, tid=2) + metadata('04:00:00.101', pid=3, tid=2)
                + total('04:00:00.102', pid=1, tid=4) + total('04:00:00.103', pid=3, tid=2)
                + total('04:00:00.104', pid=1, tid=2))
        r = report(text)
        self.assertEqual(r['summary']['engine_batch_events'], 3)
        self.assertEqual([u['association'] for u in r['updates']], ['total_without_metadata', 'same_thread_order', 'same_thread_order'])
        self.assertEqual(r['updates'][1]['metadata']['pid'], 3)
        self.assertEqual(r['updates'][2]['metadata']['line'], 1)

    def test_metadata_replaced_and_eof(self):
        text = metadata('04:00:00.100') + metadata('04:00:00.110', clear=1) + total('04:00:00.120') + metadata('04:00:00.130')
        r = report(text)
        self.assertEqual([u['association'] for u in r['updates']], ['metadata_replaced_before_total', 'same_thread_order', 'metadata_without_total_at_eof'])
        self.assertEqual(r['updates'][1]['metadata']['line'], 2)
        self.assertEqual(r['summary']['engine_batch_events'], 1)
        self.assertEqual(r['summary']['clear_one_metadata_events'], 1)
        self.assertEqual(r['summary']['clear_batches_by_mode'], {'0x206': 1})

    def test_new_update_interrupts_old_metadata(self):
        text = metadata('04:00:00.100') + line('04:00:00.110', 'epdImageDispose') + total('04:00:00.120')
        self.assertEqual([u['association'] for u in report(text)['updates']], ['new_update_before_total', 'total_without_metadata'])

    def test_repeated_totals_are_events_not_deduplicated(self):
        text = total('04:00:00.100') * 2
        r = report(text)
        self.assertEqual(r['summary']['engine_batch_events'], 2)
        self.assertEqual(r['summary']['inter_batch_log_gaps_by_pid_tid']['10:20']['min_ns'], 0)

    def test_window_context_and_partial_metadata(self):
        text = metadata('04:00:00.100') + total('04:00:00.200') + metadata('04:00:00.300') + total('04:00:00.400')
        r = report(text, start=M.iso_ns('2026-10-12T04:00:00.150'), stop=M.iso_ns('2026-10-12T04:00:00.350'))
        self.assertEqual(r['summary']['engine_batch_events'], 1)
        self.assertEqual(r['summary']['boundary_update_records'], 2)
        self.assertEqual([(u['metadata_in_window'], u['total_in_window']) for u in r['updates']], [(False, True), (True, False)])
        self.assertTrue(all(not u['steady_state_eligible'] for u in r['updates']))

    def test_mode_changes_clear_and_steady_gaps(self):
        text = (metadata('04:00:00.000') + total('04:00:00.001')
                + metadata('04:00:00.010') + total('04:00:00.011')
                + metadata('04:00:00.020') + total('04:00:00.021')
                + line('04:00:00.025', 'set next_display_mode=513', tid=8)
                + line('04:00:00.026', 'Pre_Mode=518, cur_mode=513')
                + metadata('04:00:00.030', mode='0x201') + total('04:00:00.031', frames=38)
                + metadata('04:00:00.040', mode='0x201', clear=1) + total('04:00:00.041', frames=15))
        r = report(text)
        s = r['summary']
        self.assertEqual(s['steady_state_batches_by_mode'], {'0x206': 2})
        self.assertEqual(s['mode_switch_batches_by_mode'], {'0x201': 1})
        self.assertEqual(s['clear_batches_by_mode'], {'0x201': 1})
        self.assertEqual(s['other_or_unknown_batches_by_mode'], {'0x206': 1})
        self.assertEqual(s['steady_inter_batch_log_gaps_by_pid_tid']['10:20']['n'], 1)
        self.assertEqual(r['updates'][3]['mode_switch_evidence'][0]['kind'], 'same_thread_mode_pair')
        self.assertEqual(len(r['mode_events']), 2)

    def test_mode_request_on_other_tid_cannot_prove_switch(self):
        text = line('04:00:00.090', 'set next_display_mode=513', tid=8) + metadata('04:00:00.100') + total('04:00:00.110')
        r = report(text)
        self.assertIsNone(r['updates'][0]['mode_switch'])
        self.assertFalse(r['updates'][0]['steady_state_eligible'])

    def test_unparsed_and_malformed_are_visible(self):
        text = ('--------- beginning of main\n' + metadata('04:00:00.100')
                + line('04:00:00.110', 'total frame num=N') + total('04:00:00.120', frames=0)
                + line('04:00:00.130', 'clear=9,temp=26,27,mode=518') + 'unparseable epd:: record\n')
        r = report(text)
        self.assertEqual(r['parse_counts']['malformed_epd_records'], 3)
        self.assertEqual(r['parse_counts']['unparsed_epd_lines'], 1)
        self.assertEqual(r['parse_status'], 'partial_or_discontinuous')
        self.assertEqual(r['summary']['engine_batch_events'], 0)
        self.assertEqual(r['updates'][0]['association'], 'malformed_epd_breaks_context')

    def test_valid_zero_and_invalid_input_differ(self):
        r = report('10-12 04:00:00.100 1 2 D Other : ordinary\n')
        self.assertEqual(r['summary']['engine_batch_events'], 0)
        self.assertEqual(r['parse_status'], 'parsed')
        for raw in (b'', b'[100.0] epd power on\n', b'04:00:00.1 1 2 D SDM : epd:: total frame num=8\n', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(M.InputError):
                M.report(raw, 2026)


class KernelAndCLITests(unittest.TestCase):
    def test_many_batches_one_power_cycle_separate_clocks(self):
        text = total('04:00:00.100') + total('04:00:00.200')
        kernel = b'[100.000] [thread] epd power on\n[104.000] epd fp9929a enter sleep end, state=1\n'
        r = report(text, kernel_raw=kernel)
        self.assertEqual(r['summary']['engine_batch_events'], 2)
        self.assertEqual(r['summary']['reported_waveform_frames'], 16)
        self.assertEqual(r['kernel']['event_counts']['pmic_power_on'], 1)
        self.assertEqual(r['kernel']['clock_domain'], 'kernel_uptime')
        self.assertEqual(r['clock_domain'], 'logcat_wall_clock_timezone_unspecified')
        self.assertIsNone(r['summary']['physical_content_updates'])
        self.assertIsNone(r['summary']['optical_completion_times'])
        self.assertIsNone(r['summary']['surfaceflinger_submissions'])

    def test_kernel_window_discontinuity_and_bad_input(self):
        raw = b'[100.000] epd power on\n[100.001] epd power on\n[101.000] epd 1v8 power off\n[99.0] ordinary\nbad\n'
        r = M.parse_kernel(raw, M.uptime_ns('100.001'), M.uptime_ns('101'))
        self.assertEqual(r['event_counts'], {'pmic_power_on': 1})
        self.assertEqual(r['parse_counts']['unparsed_lines'], 1)
        self.assertEqual(len(r['diagnostics']), 1)
        with self.assertRaises(M.InputError):
            M.parse_kernel(b'not kernel', None, None)
        for value in ('NaN', 'sNaN', 'Infinity', '1e999999', '-1', '0.0000000001'):
            with self.subTest(value=value), self.assertRaises(M.InputError):
                M.uptime_ns(value)

    def test_cli_json_and_output_protection(self):
        with tempfile.TemporaryDirectory() as temp:
            p, out = Path(temp) / 'input.txt', Path(temp) / 'report.json'
            p.write_text(metadata('04:00:00.100') + total('04:00:00.200'))
            self.assertEqual(M.main([str(p), '--year', '2026', '--output', str(out)]), 0)
            first = out.read_bytes()
            self.assertEqual(json.loads(first)['summary']['engine_batch_events'], 1)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(M.main([str(p), '--year', '2026', '--output', str(out)]), 2)
                self.assertEqual(M.main([str(p), '--year', '2026', '--kernel-start', '100']), 2)
                self.assertEqual(M.main([str(p), '--year', '2026', '--start', 'wrong']), 2)
                kernel = Path(temp) / 'kernel.txt'
                kernel.write_text('[100.0] epd power on\n')
                for invalid_bound in ('sNaN', '1e999999', 'Infinity', '-1'):
                    with self.subTest(invalid_bound=invalid_bound):
                        self.assertEqual(M.main([str(p), '--year', '2026', '--kernel-log', str(kernel), '--kernel-start', invalid_bound]), 2)
            self.assertEqual(out.read_bytes(), first)
            with contextlib.redirect_stdout(io.StringIO()) as stdout:
                self.assertEqual(M.main([str(p), '--year', '2026']), 0)
            self.assertEqual(json.loads(stdout.getvalue())['source_sha256'], json.loads(first)['source_sha256'])

    def test_repeat_report_is_deterministic(self):
        text = metadata('04:00:00.100') + total('04:00:00.200')
        self.assertEqual(json.dumps(report(text), sort_keys=True), json.dumps(report(text), sort_keys=True))



class MarkerAndHardeningTests(unittest.TestCase):
    def test_exact_markers_keep_buffer_outside_window(self):
        text = (total('04:00:00.100') + marker('04:00:00.200', 'BEGIN_run', tid=7)
                + metadata('04:00:00.210') + total('04:00:00.220')
                + marker('04:00:00.300', 'END_run', tid=7) + total('04:00:00.400'))
        r = report(text, begin_marker='BEGIN_run', end_marker='END_run', window_scope='acquisition')
        self.assertEqual(r['summary']['engine_batch_events'], 1)
        self.assertEqual(r['window']['scope'], 'acquisition')
        self.assertEqual(r['window']['marker_bounds']['begin']['line'], 2)
        self.assertEqual(r['declared_run_validity'], 'unknown')
        self.assertIsNone(r['summary']['physical_content_updates'])

    def test_markers_must_be_exact_unique_ordered_and_dated(self):
        valid = marker('04:00:00.100', 'BEGIN_run') + marker('04:00:00.200', 'END_run')
        examples = [valid.replace('BEGIN_run', 'prefix BEGIN_run suffix'), valid + marker('04:00:00.300', 'END_run'),
                    marker('04:00:00.100', 'END_run') + marker('04:00:00.200', 'BEGIN_run'),
                    marker('04:00:00.200', 'BEGIN_run') + marker('04:00:00.100', 'END_run')]
        for text in examples:
            with self.subTest(text=text), self.assertRaises(M.InputError):
                report(text, begin_marker='BEGIN_run', end_marker='END_run')
        with self.assertRaises(M.InputError):
            report(valid, begin_marker='BEGIN_run')
        with self.assertRaises(M.InputError):
            report(valid, begin_marker='BEGIN_run', end_marker='END_run', start=1)

    def test_declared_invalid_run_is_preserved(self):
        r = report(total('04:00:00.100'), run_validity='invalid', invalid_reason='shade pulled by stimulus')
        self.assertEqual(r['declared_run_validity'], 'invalid')
        self.assertEqual(r['invalid_reason'], 'shade pulled by stimulus')
        self.assertEqual(r['summary']['engine_batch_events'], 1)
        with self.assertRaises(M.InputError):
            report(total('04:00:00.100'), run_validity='invalid')

    def test_numeric_suffixes_are_not_valid_tokens(self):
        for message in ('total frame num=8BROKEN', 'total frame num=8.5',
                        'clear=0x,temp=26,27,mode=518', 'clear=0,temp=26,27x,mode=518',
                        'clear=0,temp=26,27,mode=518x', 'set next_display_mode=518.5',
                        'Pre_Mode=513, cur_mode=518junk', 'clear = N,temp=26,27,mode=518'):
            with self.subTest(message=message):
                r = report(line('04:00:00.100', message))
                self.assertEqual(r['parse_counts']['malformed_epd_records'], 1)
                self.assertEqual(r['summary']['engine_batch_events'], 0)
                self.assertEqual(r['parse_status'], 'partial_or_discontinuous')

    def test_conflicting_mode_context_is_unknown(self):
        text = (line('04:00:00.100', 'Pre_Mode=0x201, cur_mode=0x201')
                + metadata('04:00:00.110') + total('04:00:00.120')
                + metadata('04:00:00.130') + total('04:00:00.140'))
        r = report(text)
        self.assertEqual(r['summary']['conflicting_mode_context_records'], 1)
        self.assertEqual(r['updates'][0]['mode_context_status'], 'conflicting')
        self.assertIsNone(r['updates'][0]['mode_switch'])
        self.assertFalse(r['updates'][0]['steady_state_eligible'])
        self.assertFalse(r['updates'][1]['steady_state_eligible'])

    def test_malformed_and_parse_gaps_break_stale_pairing(self):
        examples = [line('04:00:00.110', 'clear=2,temp=26,27,mode=513'),
                    'unparseable epd:: metadata\n',
                    line('04:00:00.110', 'clear=0,temp=26,27,mode=513', day='10-32')]
        for interruption in examples:
            with self.subTest(interruption=interruption):
                r = report(metadata('04:00:00.100') + interruption + total('04:00:00.120', frames=14))
                self.assertEqual(r['updates'][-1]['association'], 'total_without_metadata')
                self.assertEqual(r['summary']['batches_by_mode'], {'unknown': 1})
                self.assertFalse(r['updates'][-1]['steady_state_eligible'])

    def test_other_thread_timestamp_jitter_does_not_destroy_pair(self):
        text = (metadata('04:00:00.110', pid=1, tid=2)
                + line('04:00:00.105', 'PanelOpen start', pid=1, tid=3)
                + total('04:00:00.120', pid=1, tid=2))
        r = report(text)
        self.assertEqual(r['updates'][0]['association'], 'same_thread_order')
        self.assertEqual(r['summary']['batches_by_mode'], {'0x206': 1})
        self.assertEqual(r['diagnostics'][0]['kind'], 'backward_timestamp_or_reordered_input')

    def test_mode_code_mapping_is_explicit_and_recorded(self):
        text = (line('04:00:00.100', 'Pre_Mode=6, cur_mode=1')
                + metadata('04:00:00.110', mode='0x201') + total('04:00:00.120', frames=38))
        exact = report(text)
        mapped = report(text, mode_code_mask=255)
        self.assertEqual(exact['summary']['observed_mode_pair_change_events'], 1)
        self.assertEqual(exact['summary']['conflicting_mode_context_records'], 1)
        self.assertEqual(exact['summary']['mode_switch_batches_by_mode'], {})
        self.assertEqual(mapped['summary']['mode_switch_batches_by_mode'], {'0x201': 1})
        self.assertEqual(mapped['updates'][0]['mode_context_status'], 'consistent_with_declared_mask')
        self.assertEqual(mapped['mode_code_comparison']['mask'], 255)
        for value in ('0', '-1', '0x100000000', '0xffjunk'):
            with self.subTest(value=value), self.assertRaises(M.InputError):
                M.parse_mode_mask(value)
        self.assertEqual(M.parse_mode_mask('0xff'), 255)
        conflict = (line('04:00:00.100', 'Pre_Mode=0x201, cur_mode=0x201')
                    + metadata('04:00:00.110', mode='0x206') + total('04:00:00.120'))
        self.assertEqual(report(conflict, mode_code_mask=255)['summary']['conflicting_mode_context_records'], 1)
        with self.assertRaises(M.InputError):
            report(text, mode_code_mask=True)

    def test_marker_same_timestamp_before_begin_is_excluded(self):
        text = (total('04:00:00.100') + marker('04:00:00.100', 'BEGIN_run')
                + total('04:00:00.100') + marker('04:00:00.200', 'END_run'))
        r = report(text, begin_marker='BEGIN_run', end_marker='END_run')
        self.assertEqual(r['summary']['engine_batch_events'], 1)
        self.assertEqual(r['updates'][0]['total']['line'], 3)

    def test_reordered_december_after_rollover_is_ambiguous(self):
        text = total('23:59:59.999', day='12-31') + total('00:00:00.001', day='01-01') + total('23:59:59.999', day='12-31')
        with self.assertRaises(M.InputError):
            report(text)


if __name__ == '__main__':
    unittest.main()
