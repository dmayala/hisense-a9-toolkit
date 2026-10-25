# Conditioned-target regression

Run `bash tests/mainline-a9eink-target/run.sh` offline. The harness compiles the
actual engine and TCON, with synthetic waveform tables (no vendor assets).
It compares complete conditioned canvases, packed old/new pairs and every byte
of every emitted frame against a full-canvas reference, with the production
damage mask enabled. Set `A9_WF_BLOB` to a validated `a9-waveform.bin` to
repeat every engine case with the actual waveform tables as well. Actual 513/518
comparisons also enable the mask; other actual modes use stock unmasked rendering
because their tables can drive unchanged diagonal pairs. Synthetic cases enable
the mask throughout and isolate dropped changed-pixel/sentinel drive.

- Modes 513/518 reproduce the report's native pixel `(20,20)` change from 128
  to 255: 164,760 changed targets, all outside the raw one-pixel rectangle.
- Modes 514 and bit-8-dithered Clear (`0x303`) use grey 136, which exposes
  propagation in DitherSStyle16; each changes 804 targets, 803 outside raw damage.
- Modes 521/515/520 retain local conditioning. Clear's sentinel-256 neighbor is
  included in the drive bounds even with the optional mask enabled.
- Coalesced A→B→A and changed raw bytes with identical final targets leave the
  engine eligible for an ordinary-job skip. Unknown history, explicit Clear,
  mode switch and initialization remain required sequences.

The synthetic waveforms drive each changed pair and sentinel 256, end neutral,
and make dropped drive pixels observable. These are software correctness tests;
they do not measure optical behavior or qualify larger halftone updates.

The policy harness extracts the actual production `__a9_update()` body and uses
mock dependencies to exercise eight bypass/required-drive cases. An ordinary
matching committed target makes no power, fresh post-power-up temperature
admission, waveform-start, frame-generation or history-commit calls. Dry jobs, initialization, forced Clear,
changed targets, aborted history and explicit compensation follow their required
paths; the bypass preserves the existing force-clear state and bin budget.

Review 22 adds a production rail-policy harness (actual temperature, power,
containment, update and job-selection functions). It checks PG low during a
duplicate, PG low in the temperature sample then good before skip, later
PG loss, explicit reset plus owed GC16, off-session no-op behavior, lookup/GPIO
errors and dry isolation. Hardware/status plumbing is mocked; optionally set
`A9_FP_STATUS_HEADER` to extract the actual status schema from a kernel tree.

A deterministic harness extracts the actual worker and controls clocks/wakes
only. It verifies that duplicate/rejected/dry jobs do not renew the idle hold,
including continuously queued duplicates and hold=0 successor behavior.
Physical output alone advances the idle origin; original energize origins and
caps remain separate. This is a worker soft deadline, checked after work;
blocked preparation or scheduling can delay the check without granting a new
hold interval. It is not a replacement for the independent hard FP cap.
