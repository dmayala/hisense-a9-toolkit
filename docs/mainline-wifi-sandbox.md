# A9 Wi-Fi storage containment and host proof

2026-10-16. Offline implementation draft; no phone access or modem start.

The threat boundary is an untrusted modem request reaching the storage/firmware
servers. The preparation helper, kernel, immutable vendor payload, verified
private seed inventory and build toolchain are trusted. The design proves
ordinary Linux storage confinement; it does not prove that opaque MPSS/TZ
firmware cannot reach another secure-world or hardware flash path.

## Fixed capabilities instead of backing paths

The two upstream patches select restricted compile-time modes. Ordinary modes
remain visible in the patch context for audit, but their discovery/translation
implementations are **not linked into the A9 executable**. The build checks the
five source pins and deterministic source archive hashes before patching copies.
There is no runtime switch back to ordinary storage mode.

| Role | Descriptor | Permitted object |
| --- | --- | --- |
| rmtfs | 10 | `/boot/modem_fs1`: writable 2 MiB RAM image seeded from private modemst1 backup |
| rmtfs | 11 | `/boot/modem_fs2`: writable 2 MiB RAM image seeded from private modemst2 backup |
| rmtfs | 12 | `/boot/modem_fsg`: writable 2 MiB RAM image seeded from private fsg backup |
| rmtfs | 15 | `/boot/modem_fsc`: writable 128 KiB RAM image seeded from private fsc backup |
| rmtfs | 13 | Validated qcom_rmtfs_mem1 **RAM character device**, base0x89b01000/size0x200000 |
| rmtfs | 14 | Prebound QRTR port14 socket, opened before capability drop |
| tqftpserv | 20 | Exact `/readonly/firmware/image/wlanmdsp.mbn`, regular O_RDONLY file, 3,814,580 bytes |
| tqftpserv | 21 | Exact `/readwrite/mcfg.tmp`, precreated regular O_RDWR RAM scratch file, maximum1MiB |
| tqftpserv | 22 | Exact `/readwrite/server_check.txt`, separate regular O_RDWR RAM scratch file, maximum1MiB |
| optional pd-mapper | none | Fixed root/WLAN service map; no JSON/sysfs/decompression opens |
| diag-router | none | Four bounded modem-only QRTR endpoints; no diagnostic host clients or command forwarding |

Unknown names, aliases, traversal, wrong access modes/types/sizes and duplicated
storage-role inodes fail closed. The rmtfs backend checks TMPFS_MAGIC and exact
sizes, preserves its working files across protocol CLOSE, preflights all I/O
vectors before mutation, and validates both file and shared-memory ranges. It
contains no open/partition discovery call. tqftpserv checks every role's inode,
flags, access and RAM backing, duplicates only admitted descriptors and performs
bounded positioned I/O; sessions do not share a mutable file offset. No runtime
file creation, truncation, compression, filename/root configuration or directory
discovery is available to the restricted server.

## Preparation and enforcement

`scripts/mainline/wifi/sandbox.c` is both the native preparation helper and the
launcher. Its role argument selects only rmtfs, tqftpserv, optional pd-mapper or modem-only diag-router;
it accepts no arbitrary executable/path/FD map. Its fixed vendor manifest is
root-owned and not group/world writable. It checks each selected immutable
regular asset's owner, mode, link count, size and SHA-256 before use. There is no
boot-time partition read, `dd`, seed synthesis, by-name/partlabel scan or
writeback operation. `fsc` is seeded too; only the two explicitly identified
TFTP scratch files start blank.

The helper verifies the rmtfs sysfs class client ID, physical base, size and
major/minor against the exact character device. It does not accept /dev/mem,
UIO or RFSA fallbacks. It prebinds the rmtfs QRTR socket because mainline
requires CAP_NET_ADMIN to bind low port14; granting that capability to the
serving process would be unnecessary privilege.

All source descriptors are moved above64 before fixed handles or safe stdin
are installed, preventing collisions with closed stdio. Stdin is a validated
read-only /dev/null descriptor. Logging accepts only write-only pipes or
validated /dev/null, never arbitrary sockets: otherwise a Unix log socket
could receive SCM_RIGHTS even when new socket families and path opens are
blocked. Existing host descriptors are not trusted.

The helper unshares a mount namespace, makes propagation recursively private,
mounts a fresh `tmpfs` with `noswap,nodev,nosuid,noexec` and a32MiB cap, copies
verified EFS seeds to new regular files, and precreates the two scratch files
where needed. It pivots into that RAM root, changes cwd and detaches the old
root. No /dev, /proc, /sys or firmware directories remain reachable. A static
daemon ELF is the only temporary outside-file capability, FD30, O_RDONLY and
CLOEXEC. Every unrelated inherited FD is closed through UINT_MAX with
close_range, independently of RLIMIT_NOFILE; a lowered limit must not hide a
previously opened high descriptor.

It clears supplementary groups, capability bounding/ambient/permitted/effective/
inheritable sets, changes all UID/GID identities to65534, sets limits and
no_new_privs, and installs `sandbox-policy.h`'s default-deny seccomp filter.
There is no SELinux-permissive exception to these native checks. The current
ROM uses permissive SELinux, so no enforcing-policy claim is made; an enforcing
vendor needs a separate policy integration pass.

The filter denies all pathname opens and filesystem mutation, device ioctls,
mount/mknod, descriptor replacement, ptrace/process creation, privilege changes,
new executable memory and all non-QRTR socket creation. It admits required
descriptor I/O, bounded backend operations, static-libc initialization and
QRTR transport calls, including connect() for TFTP transfer sockets. This is a
syscall boundary as well as a source-level lookup restriction.

The initial execveat is restricted to FD30 with AT_EMPTY_PATH. It closes on
exec; dup2/dup3/F_DUPFD, open, memfd creation and executable writable mounts are
unavailable afterward. A reused FD30 can only refer to a socket, the immutable
Hexagon firmware or noexec RAM, not another host executable. All daemons link
`postexec.c`, which requires the expected UID, no_new_privs, an active filter,
empty capabilities and a closed FD30. It resets dumpability after exec (the
pre-exec setting alone would reset). Static ARM64 ELF/interpreter and artifact
hash checks are mandatory during vendor staging. Direct unsandboxed daemon
execution exits before processing requests.

## What the tests establish

The backend tests compile the actual patched C and exercise its real storage
and translation functions. They use dummy RAM files and a mocked RAM transport,
not actual blocks or a remote processor. ASAN/UBSAN test invalid types, disk
scratch backing, inode aliases, closed/negative handles, unknown/traversal names,
out-of-range/overflow vectors, malformed QMI/TFTP, independent offsets and
unchanged sentinels. rmtfs wire tests include100,000 malformed inputs. Symbol
checks and a preprocessor review confirm ordinary path/discovery implementations
are absent from the selected binaries. LeakSanitizer is disabled where host
ptrace restrictions prevent its operation; this is not a leak-proof claim.

The sandbox test compiles the same launcher/filter and post-exec code for
**x86_64 host execution only**, with a synthetic probe. The production build
does not define the host-test mode. The host test uses root to mount an actual
noswap tmpfs in the initial user namespace: Linux explicitly rejects noswap
mounts in unprivileged user namespaces (`mm/shmem.c`, Opt_noswap). There is no
fallback to ordinary tmpfs in production or in this test.

The probe verifies the detached cwd/root and initial exec, zero capabilities,
no_new_privs and seccomp via the constructor, high inherited FD150 closure
despite a lower soft FD limit, closed FD30, and actual EPERM for pathname opens,
fake block paths, socket-family escapes, descriptor injection, mount/mknod and
privilege/dumpability increases. A connect(-1) check proves the required syscall
is admitted without making a connection. An inherited Unix log socket is
rejected before exec. Strace records actual syscall outcomes. This test never
runs an ARM64 daemon, creates a QRTR endpoint or performs a phone operation.

The test-only UID0 and user-namespace supplementary-group exception exist solely
in the host-test compilation path; production requires UID65534 and successful
group clearing. The successful root/initial-namespace run uses no group-clearing
exception. The test validates the host kernel's enforcement of the policy; it
does not assert a completed target smoke test, whose execution is forbidden in
this task.

The actual vendor packer's fs_config generator is tested separately. Source
st_mode alone is insufficient because that packer defaults etc files to0644.
Wi-Fi builds append `wifi/fs-config.txt` to retain root-only0700 seed directories
and0400 seeds/manifests, independent of SELinux. Firmware, EFS/MAC inputs,
generated includes/DTBs/images and executable build products stay outside git.

## Remaining radio gates

PaddleStroke's Hisense A6L project, local reference commit
`ede802bcb12d3c8854b8ed0e147d6477d0ea0f9d`, demonstrated four disposable stores and
identified a DIAG4097 handshake requirement before modem startup. We credit and
reuse that structure, with golden-file seeds instead of live reads, noswap
mounts and an enforced sandbox. Its later real-partition/write-through path is
not imported. SM6115 uses q6v5_pas/PAS4 and modem.mdt, not SDM660's q6v5_mss/MBA
path. DIAG needs cannot be inferred away merely because SM6115 has no DIAG DT
node: QRTR services do not require one.

A confined, audited QRTR DIAG adaptation and a staged one-shot lifecycle now
exist; see `mainline-wifi-diag-audit.md` and the coordinator guide. A9 disposable
storage/firmware tolerance, RF behavior, silicon/BDF acceptance and target stack
execution remain measurement gates. No arbitrary A6L prebuilt or diagnostic
command client is installed. The default deployment guard stays strict; an
explicit Wi-Fi path validates current source/patch/binary/snapshot provenance.
The Android bundle is separately opt-in and experimental.

The user/coordinator accepted opaque modem/TZ storage access as residual risk,
with golden backups and an explicit read-only integrity audit. This does not
change the Linux confinement proof: rmtfs/tqftpserv/DIAG still cannot open a real
partition. Only the coordinator script may copy the four real stores read-only
to its separate noswap audit mount before/after a trial, and any hash difference
ends the trial. These audit copies never seed a daemon and are never written
back. Hash comparison detects changes; it does not prevent firmware access or
establish an XPU/TZ isolation boundary.


Trial-7 admission adds exactly one RMTFS capability: FD16 is a read-only,
13-byte root-owned TMPFS policy/release record. Its node0 policy is fixed
before start; the controller changes one gate byte only after proving the
single-MPSS live topology. It is never a writable store or a path-discovery
capability. The launcher adds only the fixed O_RDONLY preparation open of
`/mnt/a9-wifi/trial/rmtfs.admission`; the existing seccomp filter is unchanged.
All non-RMTFS roles close FD16. See `mainline-wifi-rmtfs-audit.md`,
"Fix round (trial 7)", for source attribution, bounded pending requests,
topology conditions, and the residual gap versus a kernel ingress binding.
