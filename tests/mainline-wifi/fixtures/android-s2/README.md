The power fixtures and `power-provenance.json` are copied byte-for-byte from
wifi-assoc dfde942. The source S1b device block had its address masked; the
trial-15 PHY listing is the full real captured format, including its trailing
space. `s1-reg-get.txt` is the real S1 regulatory format (global US, custom PHY
99). Actual trial 21 reported PHY CN; both ordinary domains are accepted, and
neither overrides global US or enabled channel ceilings.

The Java harness generates scan rows using the exact printf format recovered
from the pinned GSI framework-wifi.jar's ScanResultUtil.dumpScanResults. These
scan rows and status strings are SYNTHETIC, not retained S1 scans: S1 kept counts
only. This validates the observed renderer's grammar; it does not establish that
S2's Binder/permission/link/NetworkStack path has executed on hardware.

The final blank line in the S1 regulatory capture is retained byte-for-byte too.

The 87e boot regression is separate from the earlier 87d audit failure.
`s2-87e-boot-stage.txt` selects verbatim non-identity lines from the captured
87e boot log; `s2-87e-console.txt` is its exact fixed-label console. Source hashes,
selection limits and measured link/content sizes are in `s2-87e-provenance.json`.
The capture reports `new 71 B` and `image size 71 is not a multiple of 512`.
The frozen 87e vendor argument is a 71-byte symlink to the exact reviewed 87d
300470272-byte image. A synthetic 71-byte symlink reproduces that refusal using
only the real lp-write local size/hash guard. Resolving its target passes the
same guard. No whole boot/lp-write script, real device or credential client runs.
The complete driver CLI rehearsal uses the production driver unchanged, real
sealed core tools, a synthetic token/fake op, fake transport/health/boot/worker
responses and a private fake lock/permit. It models success and cleanup; it is
not captured hardware evidence or proof that network gates passed on the phone.

The 87f root diagnosis uses **real hardware** evidence: the exact three-line
`root-status.jsonl`, fixed-label console, selected boot milestones, and actual
boot/vendor hash records. `s2-87f-provenance.json` binds their original files and
copies. Root-ready succeeded; PRE/stop returned a valid remote failure at
session-service with transport rc=0. The init.svc raw value was not captured.
The unpublished/never-started service fixture is a derived lifecycle case,
not an invented recovered property. Its complete PRE envelope and 14 service
state/history/request/read-failure cases execute on cached Android shell/clients.
The root channel and exact vendor bytes remain unchanged for 87g.

87h adds repeatable --probe and delivery-acknowledgement accounting. The complete
CLI fixtures provide a nonexistent synthetic token during probes, assert no op /
receive / network invocation, repeat three probes including with a previously
consumed marker, and verify framework cleanup, POST and restore each time.
Pre-delivery failure retries keep the identical synthetic seal and preserve both
runs. Lost delivery receipt keeps a public uncertainty fence and blocks another
delivery; it does not block a public probe. These are fake hardware runs, separate
from the unchanged real 87f root-channel proof. No real token/client is accessed.

87i includes the exact fixed-label 87h worker failure and selected root status
events. The rejection was phase=ram-data, before Bridge or any Wi-Fi command;
the root Binder cleanup succeeded on that same boot. The selected data mount
rows are actual earlier same-GSI hardware output, NOT raw mounts recovered from
87h. Tests derive /proc/mounts syntax, reproduce 87h's rejection of the RAM-backed
/data/user/0 alias, and exercise the complete new worker plus strict negative
mount cases. s2-87h-provenance.json binds the original evidence hashes.

87j retains the real 87i run/probe/cleanup fixed labels and selected root status
events, bound by s2-87i-provenance.json. Transport succeeds; the worker fails
after input in the old broad scan phase, before connect-dispatch. The evidence
does not distinguish scan dispatch, retrieval or target selection. The pure
PublicScanTest replay models identity transitions and command dispatch only;
it does not claim native setuid, SELinux or Binder execution on the phone.
