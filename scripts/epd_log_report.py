#!/usr/bin/env python3
"""Report saved EPD software logs offline; never acquire device data."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import sys

SCHEMA_VERSION = 1
NS = 1_000_000_000
EPOCH = datetime(1970, 1, 1)
THREADTIME = re.compile(
    r'^\s*(\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?\s+'
    r'(\d+)\s+(\d+)\s+([VDIWEFA])\s+([^:]+):\s*(.*)$')
ISO_TIME = re.compile(r'^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?$')
NUMBER = r'(?:0[xX][0-9a-fA-F]+|\d+)'
TOKEN_END = r'(?=$|[\s,])'
META = re.compile(r'clear\s*=\s*(\d+)' + TOKEN_END + r'.*?temp\s*=\s*(-?\d+)\s*,\s*(-?\d+)' + TOKEN_END + r'.*?mode\s*=\s*(' + NUMBER + ')' + TOKEN_END)
TOTAL = re.compile(r'total frame num\s*=\s*(\d+)' + TOKEN_END)
MODE_PAIR = re.compile(r'Pre_Mode\s*=\s*(' + NUMBER + r')\s*,\s*cur_mode\s*=\s*(' + NUMBER + ')' + TOKEN_END)
MODE_REQUEST = re.compile(r'set next_display_mode\s*=\s*(' + NUMBER + ')' + TOKEN_END)


class InputError(ValueError):
    pass


def timestamp_ns(day, time, fraction=''):
    try:
        delta = datetime.strptime(day + ' ' + time, '%Y-%m-%d %H:%M:%S') - EPOCH
    except ValueError as error:
        raise InputError('invalid calendar timestamp') from error
    return (delta.days * 86400 + delta.seconds) * NS + int((fraction or '').ljust(9, '0') or '0')


def iso_ns(value):
    match = ISO_TIME.fullmatch(value)
    if not match:
        raise InputError('window timestamps require YYYY-MM-DDTHH:MM:SS[.fraction], without a timezone suffix')
    return timestamp_ns(*match.groups())


def iso_text(value):
    seconds, fraction = divmod(value, NS)
    from datetime import timedelta
    return (EPOCH + timedelta(seconds=seconds)).strftime('%Y-%m-%dT%H:%M:%S') + f'.{fraction:09d}'


def uptime_ns(value):
    if not re.fullmatch(r'\d+(?:\.\d{1,9})?', value) or len(value) > 30:
        raise InputError('kernel bounds require bounded nonnegative decimal uptime seconds')
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise InputError('kernel bounds require nonnegative decimal uptime seconds') from error
    scaled = parsed * NS
    if not parsed.is_finite() or parsed < 0 or scaled != scaled.to_integral_value():
        raise InputError('kernel bounds require nonnegative uptime with at most nanosecond precision')
    return int(scaled)


def in_window(value, start, stop):
    return (start is None or value >= start) and (stop is None or value < stop)


def check_window(start, stop):
    if start is not None and stop is not None and stop <= start:
        raise InputError('stop must be later than start')


def number(value):
    return int(value, 16 if value.lower().startswith('0x') else 10)


def parse_mode_mask(value):
    if not re.fullmatch(NUMBER, value):
        raise InputError('mode code mask requires a positive decimal or hexadecimal integer')
    parsed = number(value)
    if not 0 < parsed <= 0xffffffff:
        raise InputError('mode code mask must be between 1 and 0xffffffff')
    return parsed


def mode(value):
    return hex(number(value))


def stats(values):
    ordered = sorted(values)
    if not ordered:
        return dict(n=0, median_ns=None, p95_ns=None, min_ns=None, max_ns=None)
    n = len(ordered)
    # Median can be half a nanosecond; numerator avoids rounding or float loss.
    numerator = ordered[n // 2] * 2 if n % 2 else ordered[n // 2 - 1] + ordered[n // 2]
    return dict(n=n, median_ns=numerator / 2, p95_ns=ordered[(95 * n + 99) // 100 - 1],
                min_ns=ordered[0], max_ns=ordered[-1])


def parse_logcat(raw, year, marker_messages=()):
    if not 1900 <= year <= 9998:
        raise InputError('reference year must be between 1900 and 9998')
    try:
        lines = raw.decode('utf-8').splitlines()
    except UnicodeDecodeError as error:
        raise InputError('logcat input must be UTF-8') from error
    counts = Counter()
    events, diagnostics = [], []
    last_date, last_time = None, None
    stream_times, stream_segments = {}, defaultdict(int)
    rollover_seen = False
    for index, line in enumerate(lines, 1):
        if not line.strip() or line.startswith('---------'):
            counts['header_or_blank_lines'] += 1
            continue
        match = THREADTIME.fullmatch(line)
        if not match:
            counts['unparsed_lines'] += 1
            diagnostics.append(dict(kind='unparsed_line_breaks_context', line=index))
            if 'epd::' in line:
                counts['unparsed_epd_lines'] += 1
            continue
        day, clock, fraction, pid, tid, level, tag, message = match.groups()
        # MM-DD carries no year. A forward December -> January wrap is the one
        # supported inference. Other backward dates/times remain discontinuities.
        rollover = last_date is not None and last_date.startswith('12-') and day.startswith('01-')
        if rollover_seen and day.startswith('12-'):
            raise InputError('ambiguous December record after inferred year rollover; split sessions or supply dated input separately')
        candidate_year = year + int(rollover)
        try:
            time = timestamp_ns(f'{candidate_year:04d}-' + day, clock, fraction)
        except InputError:
            counts['invalid_timestamp_lines'] += 1
            diagnostics.append(dict(kind='invalid_timestamp_breaks_context', line=index, pid=int(pid), tid=int(tid)))
            if 'epd::' in message:
                counts['invalid_timestamp_epd_lines'] += 1
            continue
        if rollover:
            year = candidate_year
            rollover_seen = True
            diagnostics.append(dict(kind='inferred_year_rollover', line=index, assigned_year=year))
        if last_time is not None and time < last_time:
            diagnostics.append(dict(kind='backward_timestamp_or_reordered_input', line=index,
                                    previous_timestamp=iso_text(last_time), timestamp=iso_text(time)))
        last_date, last_time = day, time
        stream_key = int(pid), int(tid)
        if stream_key in stream_times and time < stream_times[stream_key]:
            stream_segments[stream_key] += 1
            diagnostics.append(dict(kind='backward_stream_timestamp_or_reordered_input', line=index,
                                    pid=stream_key[0], tid=stream_key[1]))
        stream_times[stream_key] = time
        counts['parsed_logcat_records'] += 1
        is_marker = message in marker_messages
        if 'epd::' not in message and not is_marker:
            counts['non_epd_records'] += 1
            continue
        if not is_marker:
            counts['epd_records'] += 1
        event = dict(line=index, timestamp=iso_text(time), timestamp_ns=time,
                     pid=int(pid), tid=int(tid), clock_segment=stream_segments[stream_key])
        if is_marker:
            event.update(kind='window_marker', marker=message)
        elif match := META.search(message):
            clear, a, b, value = match.groups()
            event.update(kind='update_metadata', clear=int(clear), temperature_pair=[int(a), int(b)], mode=mode(value))
        elif match := TOTAL.search(message):
            event.update(kind='total_frame_num', waveform_frames=int(match.group(1)))
        elif match := MODE_PAIR.search(message):
            event.update(kind='mode_pair', previous_mode=mode(match.group(1)), current_mode=mode(match.group(2)))
        elif match := MODE_REQUEST.search(message):
            event.update(kind='mode_request', requested_mode=mode(match.group(1)))
        else:
            labels = [('epdImageDispose', 'update_start'), ('PanelOpen start', 'panel_open_start'),
                      ('PanelOpen complete', 'panel_open_complete'), ('PanelClose complete', 'panel_close_complete')]
            event['kind'] = next((label for text, label in labels if text in message), 'other_epd')
        if event['kind'] == 'other_epd' and (any(fragment in message for fragment in ('total frame num', 'Pre_Mode', 'set next_display_mode')) or re.search(r'\bclear\s*=', message)):
            event['kind'] = 'malformed_epd'
        if (event['kind'] == 'total_frame_num' and event['waveform_frames'] <= 0) or (event['kind'] == 'update_metadata' and event['clear'] not in (0, 1)):
            event['kind'] = 'malformed_epd'
        if event['kind'] == 'malformed_epd':
            counts['malformed_epd_records'] += 1
            diagnostics.append(dict(kind='malformed_epd_fields', line=index))
        events.append(event)
    if not counts['parsed_logcat_records']:
        raise InputError('no valid dated logcat threadtime records; time-only and kernel uptime formats are unsupported here')
    return events, dict(counts), diagnostics


def group_updates(events, diagnostics=(), mode_code_mask=None):
    pending, switch, prior_mode = {}, {}, {}
    updates = []
    last_segments = {}
    gaps = iter(d for d in diagnostics if d['kind'] in ('unparsed_line_breaks_context', 'invalid_timestamp_breaks_context'))
    next_gap = next(gaps, None)

    def unfinished(key, reason):
        metadata = pending.pop(key, None)
        if metadata:
            updates.append(dict(pid=key[0], tid=key[1], metadata=metadata, total=None,
                                association=reason, mode_switch=None, mode_switch_evidence=[],
                                metadata_to_total_log_gap_ns=None))

    def break_context(gap):
        keys = [(gap['pid'], gap['tid'])] if 'pid' in gap else list(set(pending) | set(prior_mode) | set(switch))
        for other in keys:
            unfinished(other, 'parse_gap_breaks_context')
            switch.pop(other, None)
            prior_mode.pop(other, None)

    for event in events:
        while next_gap is not None and next_gap['line'] < event['line']:
            break_context(next_gap)
            next_gap = next(gaps, None)
        key = event['pid'], event['tid']
        if key in last_segments and event['clock_segment'] != last_segments[key]:
            unfinished(key, 'clock_discontinuity')
            switch.pop(key, None)
            prior_mode.pop(key, None)
        last_segments[key] = event['clock_segment']
        kind = event['kind']
        if kind == 'malformed_epd':
            unfinished(key, 'malformed_epd_breaks_context')
            switch.pop(key, None)
            prior_mode.pop(key, None)
        elif kind == 'update_start':
            unfinished(key, 'new_update_before_total')
        elif kind == 'mode_pair':
            switch[key] = event
        elif kind == 'update_metadata':
            unfinished(key, 'metadata_replaced_before_total')
            event = dict(event)
            event['mode_pair_context'] = switch.pop(key, None)
            pending[key] = event
        elif kind == 'total_frame_num':
            metadata = pending.pop(key, None)
            evidence = []
            known = None
            context_status = 'none'
            mode_event = metadata.get('mode_pair_context') if metadata else None
            if mode_event:
                current = number(mode_event['current_mode'])
                previous_code = number(mode_event['previous_mode'])
                target = number(metadata['mode'])
                if mode_code_mask is not None:
                    current, previous_code, target = (v & mode_code_mask for v in (current, previous_code, target))
                context_status = ('consistent_with_declared_mask' if mode_code_mask is not None else 'consistent') if current == target else 'conflicting'
                if context_status != 'conflicting':
                    known = previous_code != current
                evidence.append(dict(kind='same_thread_mode_pair', line=mode_event['line'],
                                     previous_mode=mode_event['previous_mode'], current_mode=mode_event['current_mode'],
                                     comparison_mask=mode_code_mask))
            if metadata:
                previous = prior_mode.get(key)
                if previous is not None and context_status != 'conflicting':
                    changed = previous != metadata['mode']
                    # Any observed change excludes the batch from the steady set.
                    known = changed if known is None else known or changed
                    evidence.append(dict(kind='successive_metadata_modes', previous_mode=previous,
                                         current_mode=metadata['mode']))
                if context_status == 'conflicting':
                    prior_mode.pop(key, None)
                else:
                    prior_mode[key] = metadata['mode']
            gap = event['timestamp_ns'] - metadata['timestamp_ns'] if metadata else None
            updates.append(dict(pid=key[0], tid=key[1], metadata=metadata, total=event,
                                association='same_thread_order' if metadata else 'total_without_metadata',
                                mode_context_status=context_status, mode_switch=known, mode_switch_evidence=evidence,
                                metadata_to_total_log_gap_ns=gap))
    while next_gap is not None:
        break_context(next_gap)
        next_gap = next(gaps, None)
    for key in list(pending):
        unfinished(key, 'metadata_without_total_at_eof')
    return sorted(updates, key=lambda row: (row['total'] or row['metadata'])['line'])


def event_in_window(event, start, stop, begin_line=None, end_line=None):
    return in_window(event["timestamp_ns"], start, stop) and (begin_line is None or event["line"] > begin_line) and (end_line is None or event["line"] < end_line)


def select_updates(updates, start, stop, begin_line=None, end_line=None):
    selected = []
    for row in updates:
        metadata, total = row['metadata'], row['total']
        meta_in = bool(metadata and event_in_window(metadata, start, stop, begin_line, end_line))
        total_in = bool(total and event_in_window(total, start, stop, begin_line, end_line))
        if not (meta_in or total_in):
            continue
        row = dict(row, metadata_in_window=meta_in, total_in_window=total_in)
        if metadata and total and not (meta_in and total_in):
            row['window_status'] = 'crosses_window_boundary'
        else:
            row['window_status'] = 'inside_window'
        row['steady_state_eligible'] = bool(meta_in and total_in and row['mode_switch'] is False
                                            and row['association'] == 'same_thread_order' and metadata['clear'] == 0)
        selected.append(row)
    return selected


def gap_summary(updates, steady_only=False):
    previous, gaps = {}, defaultdict(list)
    for row in updates:
        if not row['total_in_window']:
            continue
        event = row['total']
        key = row['pid'], row['tid'], event['clock_segment']
        prior = previous.get(key)
        eligible = row['steady_state_eligible']
        if prior:
            prev_event, prev_eligible, prev_mode = prior
            current_mode = row['metadata']['mode'] if row['metadata'] else None
            if not steady_only or (eligible and prev_eligible and current_mode == prev_mode):
                delta = event['timestamp_ns'] - prev_event['timestamp_ns']
                if delta >= 0:
                    gaps[f'{key[0]}:{key[1]}'].append(delta)
        previous[key] = event, eligible, row['metadata']['mode'] if row['metadata'] else None
    return {key: stats(values) for key, values in sorted(gaps.items())}


def parse_kernel(raw, start, stop):
    check_window(start, stop)
    try:
        lines = raw.decode('utf-8').splitlines()
    except UnicodeDecodeError as error:
        raise InputError('kernel input must be UTF-8') from error
    counts = Counter()
    events, diagnostics = [], []
    previous = None
    for index, line in enumerate(lines, 1):
        if not line.strip():
            continue
        match = re.match(r'^\[\s*(\d+(?:\.\d{1,9})?)\]\s*(.*)$', line)
        if not match:
            counts['unparsed_lines'] += 1
            continue
        time = uptime_ns(match.group(1))
        counts['parsed_kernel_records'] += 1
        if previous is not None and time < previous:
            diagnostics.append(dict(kind='backward_uptime_or_reordered_input', line=index))
        previous = time
        if not in_window(time, start, stop):
            continue
        text = match.group(2)
        labels = [('epd power on', 'pmic_power_on'), ('enter sleep end', 'pmic_enter_sleep_end'),
                  ('epd 1v8 power off', 'panel_1v8_power_off')]
        kind = next((label for fragment, label in labels if fragment in text), None)
        if kind:
            events.append(dict(line=index, uptime_ns=time, kind=kind))
    if not counts['parsed_kernel_records']:
        raise InputError('no valid bracketed kernel uptime records')
    return dict(clock_domain='kernel_uptime', window=dict(start_ns=start, stop_ns=stop, semantics='[start,stop)'),
                parse_counts=dict(counts), diagnostics=diagnostics,
                event_counts=dict(Counter(e['kind'] for e in events)), events=events,
                limit='PMIC/rail lifecycle events; neither unique content refreshes nor optical completions.')


def report(raw, year, start=None, stop=None, kernel_raw=None, kernel_start=None, kernel_stop=None,
           begin_marker=None, end_marker=None, window_scope='unspecified', run_validity='unknown', invalid_reason=None, mode_code_mask=None):
    check_window(start, stop)
    if mode_code_mask is not None and not (type(mode_code_mask) is int and 0 < mode_code_mask <= 0xffffffff):
        raise InputError('mode code mask must be a positive 32-bit integer')
    if bool(begin_marker) != bool(end_marker):
        raise InputError('begin and end markers must both be supplied')
    if begin_marker is not None and (start is not None or stop is not None):
        raise InputError('marker bounds and explicit timestamp bounds cannot be combined')
    if begin_marker is not None and begin_marker == end_marker:
        raise InputError('begin and end marker messages must differ')
    if run_validity == 'invalid' and not invalid_reason:
        raise InputError('invalid runs require an explicit reason')
    if invalid_reason and run_validity != 'invalid':
        raise InputError('invalid reason requires run validity invalid')
    events, counts, diagnostics = parse_logcat(raw, year, (begin_marker, end_marker))
    marker_bounds = None
    if begin_marker is not None:
        begin = [e for e in events if e.get('marker') == begin_marker]
        end = [e for e in events if e.get('marker') == end_marker]
        if len(begin) != 1 or len(end) != 1:
            raise InputError('each exact marker message must occur once in the source')
        if begin[0]['line'] >= end[0]['line']:
            raise InputError('end marker must follow begin marker in source order')
        start, stop = begin[0]['timestamp_ns'], end[0]['timestamp_ns']
        check_window(start, stop)
        marker_bounds = dict(begin=begin[0], end=end[0])
    begin_line = marker_bounds['begin']['line'] if marker_bounds else None
    end_line = marker_bounds['end']['line'] if marker_bounds else None
    window_events = [e for e in events if event_in_window(e, start, stop, begin_line, end_line)]
    updates = select_updates(group_updates(events, diagnostics, mode_code_mask), start, stop, begin_line, end_line)
    total_records = [r for r in updates if r['total_in_window']]
    mode_counts, steady_counts, switch_counts, clear_counts, unknown_counts = (Counter() for _ in range(5))
    for row in total_records:
        key = row['metadata']['mode'] if row['metadata'] else 'unknown'
        mode_counts[key] += 1
        if row['steady_state_eligible']:
            steady_counts[key] += 1
        elif row['mode_switch'] is True:
            switch_counts[key] += 1
        elif row['metadata'] and row['metadata']['clear'] == 1:
            clear_counts[key] += 1
        else:
            unknown_counts[key] += 1
    event_counts = Counter(e['kind'] for e in window_events)
    kernel = parse_kernel(kernel_raw, kernel_start, kernel_stop) if kernel_raw is not None else None
    if kernel is not None:
        kernel['source_sha256'] = hashlib.sha256(kernel_raw).hexdigest()
    issues = any(r.get('mode_context_status') == 'conflicting' for r in updates) or any(counts.get(k) for k in ('unparsed_lines', 'invalid_timestamp_lines', 'malformed_epd_records')) or any(
        d['kind'] == 'backward_timestamp_or_reordered_input' for d in diagnostics)
    return dict(schema_version=SCHEMA_VERSION, evidence_layer='software_log_events',
                source_sha256=hashlib.sha256(raw).hexdigest(), reference_year=year,
                mode_code_comparison=dict(mask=mode_code_mask, assumption='caller-declared bitmask' if mode_code_mask is not None else 'exact numeric identity'),
                year_rule='year applies to first dated record; forward December-to-January infers +1 year',
                clock_domain='logcat_wall_clock_timezone_unspecified',
                window=dict(start=iso_text(start) if start is not None else None,
                            stop=iso_text(stop) if stop is not None else None, semantics='[start,stop)',
                            scope=window_scope, marker_bounds=marker_bounds),
                declared_run_validity=run_validity, invalid_reason=invalid_reason,
                parse_status='partial_or_discontinuous' if issues else 'parsed', parse_counts=counts,
                diagnostics=diagnostics, event_counts=dict(event_counts),
                summary=dict(engine_batch_events=event_counts['total_frame_num'],
                             reported_waveform_frames=sum(e['waveform_frames'] for e in window_events if e['kind'] == 'total_frame_num'),
                             observed_mode_pair_change_events=sum(e['previous_mode'] != e['current_mode'] for e in window_events if e['kind'] == 'mode_pair'),
                             clear_one_metadata_events=sum(e['clear'] == 1 for e in window_events if e['kind'] == 'update_metadata'),
                             batch_lengths=dict(sorted(Counter(str(r['total']['waveform_frames']) for r in total_records).items())),
                             batches_by_mode=dict(mode_counts), steady_state_batches_by_mode=dict(steady_counts),
                             mode_switch_batches_by_mode=dict(switch_counts), clear_batches_by_mode=dict(clear_counts),
                             other_or_unknown_batches_by_mode=dict(unknown_counts),
                             update_association_counts=dict(Counter(r['association'] for r in updates)),
                             boundary_update_records=sum(r['window_status'] != 'inside_window' for r in updates),
                             conflicting_mode_context_records=sum(r.get('mode_context_status') == 'conflicting' for r in updates),
                             inter_batch_log_gaps_by_pid_tid=gap_summary(updates),
                             steady_inter_batch_log_gaps_by_pid_tid=gap_summary(updates, True),
                             surfaceflinger_submissions=None, physical_content_updates=None, optical_completion_times=None),
                updates=updates, mode_events=[e for e in window_events if e['kind'] in ('mode_pair', 'mode_request')],
                panel_lifecycle_events=[e for e in window_events if e['kind'].startswith('panel_')], kernel=kernel,
                limitations=[
                    'Batch metadata is associated by PID/TID and source order; the log has no unique update identifier.',
                    'Unknown first-batch, orphan, boundary, clear-one and mode-switch records are excluded from steady-state counts.',
                    'Total-frame records report software waveform frame counts, not unique optical content updates.',
                    'Metadata-to-total gaps are log-event gaps, not drive duration or physical completion.',
                    'Logcat wall clock and kernel uptime remain separate; no latency join or clock mapping is inferred.',
                    'Absence of parsed events does not establish absence of physical refreshes or successful acquisition.',
                    'No optical FPS, settling, ghosting or SurfaceFlinger submission measurement is supplied.',
                    'Run validity, acquisition/stimulus scope and any mode code mask are caller declarations, not parser validation.'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logcat', type=Path, help='saved UTF-8 logcat threadtime file (MM-DD HH:MM:SS.fraction PID TID level tag: message)')
    parser.add_argument('--year', type=int, required=True, help='year of the first dated source record, not the start-window year')
    parser.add_argument('--start', help='inclusive full local wall timestamp YYYY-MM-DDTHH:MM:SS[.fraction]')
    parser.add_argument('--stop', help='exclusive full local wall timestamp YYYY-MM-DDTHH:MM:SS[.fraction]')
    parser.add_argument('--begin-marker', help='exact begin log message (paired with --end-marker)')
    parser.add_argument('--end-marker', help='exact end log message; both messages must occur exactly once')
    parser.add_argument('--window-scope', choices=('acquisition', 'stimulus', 'unspecified'), default='unspecified')
    parser.add_argument('--run-validity', choices=('valid', 'invalid', 'unknown'), default='unknown', help='caller-declared experimental validity, not parser certification')
    parser.add_argument('--invalid-reason', help='required for a declared invalid experiment')
    parser.add_argument('--mode-code-mask', help='optional declared bitmask (e.g. 0xff) for comparing mode codes with metadata bitfields; default exact identity')
    parser.add_argument('--kernel-log', type=Path, help='optional separate saved dmesg with bracketed uptime timestamps')
    parser.add_argument('--kernel-start', help='inclusive kernel uptime seconds, separate from logcat bounds')
    parser.add_argument('--kernel-stop', help='exclusive kernel uptime seconds, separate from logcat bounds')
    parser.add_argument('--output', type=Path, help='new JSON path; an existing path is never overwritten (default stdout)')
    args = parser.parse_args(argv)
    try:
        if (args.kernel_start is not None or args.kernel_stop is not None) and args.kernel_log is None:
            raise InputError('kernel bounds require --kernel-log')
        result = report(args.logcat.read_bytes(), args.year,
                        iso_ns(args.start) if args.start is not None else None,
                        iso_ns(args.stop) if args.stop is not None else None,
                        args.kernel_log.read_bytes() if args.kernel_log else None,
                        uptime_ns(args.kernel_start) if args.kernel_start is not None else None,
                        uptime_ns(args.kernel_stop) if args.kernel_stop is not None else None,
                        args.begin_marker, args.end_marker, args.window_scope, args.run_validity, args.invalid_reason,
                        parse_mode_mask(args.mode_code_mask) if args.mode_code_mask is not None else None)
        result['source_path'] = str(args.logcat.resolve())
        if result['kernel'] is not None:
            result['kernel']['source_path'] = str(args.kernel_log.resolve())
        output = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + '\n'
        if args.output:
            with args.output.open('x', encoding='utf-8') as file:
                file.write(output)
        else:
            print(output, end='')
    except (InputError, OSError) as error:
        print('epd-log-report: ' + str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
