# ROW29 FBE POC — host prepared, phone untested

Opt in with `--data-mode row29-fbe`; plain `row29` remains the integration
default. This profile uses the same checked B-only dm-linear extent and f2fs,
with `fileencryption=aes-256-xts:aes-256-cts:v2`. No inlinecrypt, wrappedkey,
metadata encryption, new vold, real userdata/metadata partition, or boot-time
format is used. The existing GSI system vold provisions the keys and policies.

The delivered N-based pair is under
`/home/user/A9-build/mainline/storage-row29-fbe-20261021`: `image/vendor.img`
derives from phone-passed R2 vendor66e95390, and `boot.img` is the unchanged
enforcing b4936f2f. All 100 N modules remain unchanged. This is a separate POC,
not a replacement for agent's integrated O pair. Keep the matching mainline
N test DTBO already used with that boot. The default stock-DTBO ABL simulation
cannot overlay mainline; the retained successful boot receipt uses the matching
N DTBO explicitly. The recovery-target receipt checks the embedded test DTBO.
Do not flash the stock DTBO or alter a live partition to satisfy a host check.

To apply over a future integrated O vendor while preserving its new features:

```bash
python3 -I scripts/mainline/storage-row29/data/integrate.py \
  --base-vendor /path/to/integrated-O/vendor.img --out /path/to/fresh/FBE \
  --kernel-config /path/to/integrated-O/kernel.config --data-mode row29-fbe
```

Keep O's own matching boot/modules together. The full builder also accepts
`--data-mode row29-fbe` on its three paths. The linker uses the current vendor
CIL and matching GSI cache, keeping O policy additions. No merge into the
coordinator's active checkout or O output was performed by this worker.

The mapper checks both f2fs encryption feature bits and root metadata, but does
not traverse encrypted children before vold. Init creates the boot-local
`/dev/a9-row29-hold` (shell:shell0770), with a narrow SELinux file type. Dead-man
and control-service hold path becomes `/dev/a9-row29-hold/stay`; freshness,
maximum uptime, original deadlines and bootloader return remain unchanged.
The vendor's adb-key/local-tmp setup runs at `boot`, after the platform
`post-fs-data` installkey/init_user0, preventing plaintext directory pollution.
Existing system vold/Keystore2/CF nonsecure KeyMint and Gatekeeper are retained;
no TEE daemon or substitute fixed fscrypt key is introduced.

## One-shot reformat — agent only, RAM-data B first

The current plain test filesystem is nonempty. Boot the known n-e RAM-data
vendor on B **before** running this command, keeping boot b4936f2f. Use the
existing healthy-A/no-resize vendor_b lp-write and B boot workflow. Retain any
wanted test data first. This preparation does not run or erase anything.
No tool here targets the daily /data partition.

Run on the host with agent's existing root-shell transport:

```bash
set -o pipefail
O=/home/user/A9-build/mainline/storage-row29-fbe-20261021
{ printf 'set -- --write-approved\n'; cat "$O/tools/row29-fbe-reformat.sh"; } |
  adb shell -T 'timeout 600 nc 127.0.0.1 2324' | tee "$O/reformat-phone.txt"
```

Require `ROW29 DATA REFORMAT PASS`, `ROW29 DATA CLEANUP PASS`,
`ROW29 FBE REFORMAT RUN PASS` and `ROW29_FBE_REFORMAT_RC=000`.
The binary independently requires B cmdline, RAM ext4 /data, exact UFS identity,
both pinned GPT CRCs/arrays, unused entry59/nonoverlap and excluded backup-GPT
tail, no existing disk holder/mapper, and both existing f2fs superblocks.
It uses the proven SIGCHLD/pipe formatter path: `make_f2fs -t 0 -O encrypt`
only on its verified bounded mapper. It prepares the root label/owner/mode,
flushes, checks GPT/boundaries, removes mapping and cleans up. There is no
generic device, offset, formatter flag, raw-disk writer or automatic restore.
The formatter enables fscrypt; **vold creates encryption policies next boot**.

Return through the existing owner path to healthy A, then install
`$O/image/vendor.img` into vendor_b only (same-size/no resize), verify boot_b
against `$O/boot.img`, and boot B. For example, the existing installation step:

```bash
R=/home/user/A9
"$R/scripts/a9-health.sh"
"$R/scripts/a9-readback.sh" boot_b "$O/boot.img"
"$R/scripts/mainline/android/lp-write.sh" "$O/image/vendor.img" | tee "$O/install-check.txt"
! grep -q 'resizes it' "$O/install-check.txt"
CODE=$(awk '/CHECK: PASS\. code:/ {print $4}' "$O/install-check.txt")
test -n "$CODE"
"$R/scripts/mainline/android/lp-write.sh" "$O/image/vendor.img" --write --code "$CODE"
```

Use the owner's existing B reboot/return sequence; this script does not change
boot, DTBO or slot A. Replace the **owned** legacy keeper with `keeper-fbe.sh`
for this FBE boot. Stop only that identified owned process, never another
owner's keeper, and do not run both. Before waiting for boot completion:

```bash
SER=$(adb get-serialno)
bash "$O/keeper-fbe.sh" "$(command -v adb)" "$SER" >"$O/keeper-phone.log" 2>&1 &
FBE_KEEPER_PID=$!
```

The new keeper requires B and the volatile directory and refreshes it every
60s. Its 7200s requested hold retains the vendor's21600s cap. First boot remains
subject to the existing boot-completion/dead-man limits. No credentials or PIN
automation is needed: start with an uncredentialed disposable user0.

## Boot check and second-boot persistence

After Android reports boot_completed=1, invoke the self-contained read-only
check through rootsh (no staging of a binary onto /data):

```bash
{ printf 'set -- check\n'; cat "$O/tools/check-fbe.sh"; } |
  adb shell -T 'timeout 90 nc 127.0.0.1 2324' | tee "$O/check-phone.txt"
```

It requires B, enforcing, crypto state encrypted/file, the exact FBE fstab and
linear mapping, running vold/Keystore2/KeyMint/Gatekeeper, Android user0 state
RUNNING_UNLOCKED, persistent vold key directories, and actual kernel v2
AES-XTS/CTS policies/key-PRESENT on `/data/data`, `/data/user_de/0`,
`/data/system_ce/0`, `/data/system_de/0`. CE and DE identifiers must differ;
app/system policies must match within each class. Properties alone cannot pass.
The helper uses only GET-policy/GET-key-status ioctls; it never exports keys.

For stronger acceptance, run the same command with `set -- create` before a
second B reboot, then `set -- verify` afterwards, without formatting or
recreating markers. This creates two public fsynced markers in system_ce/de,
prints their hashes and boot ID, and verifies their exact original bytes.
Require both markers, unchanged policy identifiers and different boot IDs.
The no-credential POC proves vold provisioning and recovery, not Direct Boot
credential isolation. Disposable UI credential/wrong-unlock tests remain later.

Keep all captures private. On failure use the existing `CAPTURE.md` root dmesg
AVC/init/vold/Keystore recipe while B is alive, before returning A. Capture
`getprop init.svc.*`, `cmd activity get-started-user-state 0` and policy-check
failure too. Never dump vold key files, Keystore databases or auth tokens.
If init stalls before USB/adb setup, adb cannot collect it; retain the normal
owner timeout/return path rather than changing enforcement or formatting again.

## Undo and limits

Known RAM-data undo: `integrated-20261021n-e/vendor.img`, SHA256
3640432d0fb384c9762c1546348f20677565dd1020994d10728391789efcbbb8.
Restoring it via healthy A vendor_b leaves encrypted contents intact. To return
to plain ROW29, use this explicit RAM-B reformat again, then the plain vendor;
simply swapping a vendor does not decrypt FBE. Original zero-range restoration
remains the separate step3 tool with original hash/GPT/boundary checks. Never
restore GPT or erase daily userdata/metadata. Original candidate.bin is retained.

This is software FBE for a functional POC, without hardware key isolation or
metadata encryption. Stock nonsecure provider behavior and key-blob persistence
must still be proven on the phone; host tests cannot establish that result.
Source baseline: [AOSP vold Keystore](https://android.googlesource.com/platform/system/vold/+/refs/heads/main/Keystore.cpp)
describes Keystore2's software fallback; [FsCrypt](https://android.googlesource.com/platform/system/vold/+/refs/heads/android16-qpr2-release/FsCrypt.cpp)
defines the systemwide/user key and policy lifecycle. The selected GSI init and
provider APEX configs were inspected locally; no GSI binary was patched.
