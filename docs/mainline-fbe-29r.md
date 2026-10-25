# FBE-29R: wrapped-FBE evidence and RPMB

Host-only task. ENC-HANG was handed to agent at da2011e; no further encoder
work is included here. Integ18's row29 has real F2FS/file-encryption evidence
but zero retained ICE/wrapped-key/native-crypto markers. That verdict alone
does not establish plain FBE: these three fields come from kernel-log strings.
Integ18's return archive was empty after its hold ended early; that collection
is final. No retained boot directories or qseecomd log are available for this
run. Its all-zero extractor summaries represent missing input. Integ19,
containing the corrected logger identity, supplies the next evidence opportunity.

A concrete collection regression is fixed. a8ef6d9 appended early-ring metrics
to the first info.txt line, changing the logger identity from
`A9_MAINLINE_LOG boot=... pid=...` to that text followed by extra fields.
Acceptance's current_kernel intentionally compares this line exactly when
opening the logger's private /proc/PID/root namespace. It therefore rejects
the active logger, tries the normally unavailable public /kdebuginfo mount,
then falls back to live dmesg. Early crypto records can already have rotated
out of that small live ring. The resulting three zero counts can be a false
negative. No relaxed crypto predicate or plain-key fallback is introduced.

The logger now preserves the original first-line contract and emits ring
metrics on a separate second line. A focused test compiles the production
header writer and runs the actual current_kernel shell function with cached
Android mksh against fake proc/log files. The old extended header loses all
three known crypto records; the corrected header finds all three; a wrong boot
ID still refuses the retained log. No logger daemon, mount, device or phone
operation runs in this test. ARM64 logger compilation and diff checks accompany
this integration regression test.

This is not evidence that the listener-extent change1f11850 broke wrapped
keys. Its descriptor registers the stock mapped0x7000 extent while preserving
logical0x6400 user bounds. Independent medium source review found no changed
SCM call/argument or lower-SMC descriptor/result mapping establishing a crypto
regression. Successful registration can expose previously unavailable RPMB
callbacks; whether this boot reached those callbacks still requires returned
registration, storage, TA and vold evidence. Existing BLOCKED handling differs
from stock in waiting/terminal-result validation, but no observed traversal
supports changing it now.

Next: inspect integ19's returned boot kernel and vold/keymaster logs for positive
ICE selection, raw68 wrapped-key import and native blk-crypto; correlate
request/mapped length, raw secure SCM status and phases4/5/6; then diagnose
actual RPMB read completion/TA validation. Do not rewrite the original sweep
verdict or claim a repaired device from host fixtures. Evidence goes under
A9-build/mainline/fbe-29r-host; the user's sweep is preserved unchanged.

The UFS read-first guard and all KMTB logic remain unchanged. Never create or
write KMTB, program an RPMB key/counter or permit authenticated writes while
reads fail. Registration/read submission alone is not authenticated-read or
rollback-resistance success.
