# Manual-clear host regressions

Run `bash tests/mainline-a9eink-clear/run.sh` offline. Optionally set
`A9_WF_BLOB` to a validated `a9-waveform.bin` to repeat engine cases with the
actual waveform tables. Synthetic fixtures contain no vendor data.

The engine harness links the production engine/TCON. For entered modes
513/518/521, an explicit clear of the same target must run a full sequence,
finish neutral and commit history in the selected mode. Every complete frame
and packed target is compared with the direct core request/conditioning/render
path. Actual warm-bin counts are respectively 30/48/42. The following ordinary
update stays in the selected mode without reentry compensation; an identical
ordinary target remains eligible for duplicate suppression.

The selector harness extracts the production sysfs stores and `a9_take_job`
verbatim. It tests exact parsing, repeat requests, real/dry mode preservation,
and requests queued after an active job has been taken. Twenty-two independent
real/dry precedence cases cover full GC16 for forced recovery, init, unknown
history, pending mode entry, controls, diagnostic patterns and HV/rail/stall
faults. Legacy `force_clear`, separate real/dry recovery flags, and the recovery
still owed after fault release retain their conservative behavior.

All tests are host software checks. They do not access hardware, qualify
optical clearing, or change electrical admission and drive budgets.
