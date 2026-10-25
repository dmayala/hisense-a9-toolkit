# Integ9 host candidate — 24 October

Output: `/home/user/A9-build/mainline/integ-20261024-9`.
Frozen owner-only launcher: `run-sweep.sh` in that directory. It was syntax
checked and never executed. The image also contains frozen fingerprint and
FBE collectors; every launcher selects this same image pair.

| Artifact | SHA256 |
|---|---|
| boot.img | `8650aa7fd12d8b8ea48d681ccfd41f819b0fd3615b927d686f67b6d8f04d7b24` |
| vendor.img | `2ad6634238d4bded554fcb5319d381d77ce625efd8652a8dc59b92e254c5b7ca` |

Built from main `9b8e7423930e63758ad4f17da7e865183e3e3101`, directly on
agent's integ8 through FP9. All firmware, boot/kernel/DT and101 modules
are preserved. The inherited module audit checked6243 imports with zero CRC,
vermagic or module failures. Seven explicit vendor paths form the overlay;
the other1346 regular files compare byte-for-byte with FP9. File modes, owners,
timestamps and SELinux labels are preserved. New policy queries on the actual
packaged binary find zero permissive domains.

Merged fixes: `0b8af89` native WLAN readiness and distinct stability transitions;
`146a4e1` MTP getter stderr/status; `77b60ba` enforcing diagnostic policy;
`ebfe2ed` default mono capture routed to MM2; `5c3b0db` declared second RIL slot;
`0744b36` stock-backed front AF/torch applicability; `9b8e742` integrated build
and unified launchers. The detached worktree used no short-lived branch to
leave behind. No force operation or phone operation occurred.

Host PASS: filesystem, full init verifier, ABL, image/source/runtime/APK pins,
all unmodified vendor bytes and metadata,67 positive/2 negative policy decisions,
five acceptance fixture groups, five stock-radio groups, five fingerprint
fixture groups, five actual Android arm64 audio-route groups, and four actual
Android/native transport fixture groups. The historical fifth transport case
requires a retired `integ-20261023-6r/vendor.img` which is no longer present;
its first attempted run failed on that missing input. It is not claimed PASS.
The new builder instead checks the complete actual FP9-to9 image delta.

The frozen acceptance hook imports successfully and refuses execution outside
the owning hook context with rc2. Runtime15 and source28 pins match. Local
receipts: `integrated-build.json`, `host-checks.json`, `SHA256SUMS`,
`vendor-fsck.log`, `init-verifier.log`, `abl-check.log`, and
`integ9-host/{audio-final,policy}`. Final disk free is approximately24GiB,
above the15GiB floor. No active build job, mount or lock remains.

**Zero FAILs is unproven.** [Diagnosis and limits](README9.md)
distinguish the corrected gates/counters from actual hardware recovery. The
mic endpoint fix is a candidate cause, and MTP/Wi-Fi recovery needs an owned
sweep. QCRIL slot1's delayed admission may still produce startup ANRs. Real
wrapped FBE remains agent's task. The old signed Venus firmware still lacks
mainline encoding; rear AF/flash require implementation and cannot be N/A.
Only the stock front camera's absent AF/flash receive N/A evidence. Checkin
and Play continue to require real validated Wi-Fi and were not run on this host.
