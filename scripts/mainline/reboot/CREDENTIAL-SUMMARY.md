# One-command credential/RPMB sweep summary

Host only; reads the supplied sweep directory and its adjacent owner `.out`
file when needed. No subprocess/device calls, writes or credential values:

```sh
python3 scripts/mainline/reboot/credential-summary.py /path/to/sweep-log-dir
```

Normal output contains fixed tags and integer-valued fields only. It prints:

- `A9_PIN`: saved PIN lifecycle verdict and the numeric set/verify/lock/unlock/
  remove/cleanup stages. Verdict `-1`=unknown, `0`=FAIL, `1`=PASS, `2`=NOT_RUN.
  Source `0`=absent, `1`=`acceptance.json`, `2`=owner stdout fallback. JSON takes
  precedence over `sweep.log` or adjacent `SWEEP_DIR.out`. Missing evidence is
  unknown, never an inferred pass/fail. Unknown/missing stage fields are `-1`.
- `A9_GATEKEEPER`: start/link status and retained-message counts. Start
  `-1`=unknown, `0`=exit observed, `1`=running/registered observed,
  `2`=start request only, `3`=running and exit observations. Link `-1`=unknown,
  `0`=link failure, `1`=HIDL registration, `2`=both. Init's start request alone
  does not prove the executable linked. No failure line is not proof of health.
- `A9_PERSIST_RO`: observed phase/command exit status; `records=0` means absent.
  Phase3rc0 qualifies only the worker namespace, as documented in
  [the read-only mount report](../../../docs/mainline-persist-readonly.md).
- `A9_SFS_OPEN`: counts by root and errno, including failed/read/write/create
  counts. Root1=persist/legacy alias, root2=tzstorage. `errno=-1 count=0` means
  no retained observations. Reads/writes classify O_ACCMODE; creates overlap.
- RPMB registration phases, SCM result, callback/query/guard/backend records,
  refusal metadata (`A9_RPMB_REJECT`) and listener status (`A9_RPMB_RESULT`).
  `A9_RPMB_MAP` reports the scoped GK map's validity; GUARD mode2 operations5/6
  are GK data-write and result-request submissions, not proof of completion.
  These numeric records are deduplicated across kernel/logcat mirrors.
  The listener's operation_rc is not the QSEE response status. See
  [the INTEG23 diagnosis](../../../docs/mainline-fbe-rr6.md) for field meanings.
- `A9_KEYSTORE`: occurrences of `Error::Km(-10003)` and `Error::Km(-67)`,
  non-rollback fallback messages, TA storage-read and add-key-ID failure lines.
  These are retained-message counts, not counts of unique key operations.

`boot` is an ordinal for sorted numeric `boot-*` directories. Files such as
`boot-probe.log` are not boots. A single saved boot or `manual/logcat.txt` layout
also works. Coverage reports logcat/kernel/TA file counts; read errors are
numeric and do not print filenames, exception messages or private payloads.

Keystore/SFS counts use raw logcat files in preference to derived daemon
extracts. Repeated SFS outcomes remain separate; kernel mirrors are not another
SFS event source. Early/full kernel streams and rotated logcat can overlap, so
counts of retained lines are not unique-event or complete-capture guarantees.
Older capped ATTEST_FS records are not reclassified as new per-root SFS events.

Exit0 means a directory was summarized, not that any acceptance row passed.
An invalid directory emits a numeric failure record and exits2. Help/argument
usage remains standard CLI text. Fixture smoke:

```sh
python3 -B scripts/mainline/reboot/test-credential-summary.py
```
