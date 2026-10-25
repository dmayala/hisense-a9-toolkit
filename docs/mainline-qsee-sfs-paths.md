# Stock QSEE filesystem paths and mainline routing

Follow-up: [physical persist is now mounted read-only](mainline-persist-readonly.md)
by new builds. The isolated metadata bind described in this baseline comparison
is historical; the stock binary/path analysis still applies.

Host-only comparison of the selected stock vendor image and the inherited
integ20 vendor/GSI configuration, followed by the current packaging source.
No phone, filesystem key contents or actual storage request was used. No persist
mount, permission, label, backing store or content is changed. The existing rule
against KMTB creation/writes or RPMB programming while reads fail remains intact.

## Listener identities and file selection

Stock qseecomd's relocated table at0x105308 identifies ordinary filesystem
listener0xA (`fs_start`, table0x1053e8), GP filesystem listener0x7000
(`gpfs_start`, table0x105420), and request-cancellation listener0x1100
(`gp_reqcancel_start`, table0x1054c8). Both filesystem listeners use libdrmfs.so.
Table REA ev_126985ea381f278260284c2fd0e349a2820608022e2bb59d1cd64693e026e0e3.
`start_listener`0x10206c dynamically loads those entries; main0x102334 initializes
RPMB/SSD, drops to UID/GID1000, then starts the listener threads. Its registered
property does not prove all filesystems or secure reads work.
REA ev_07c80cfce6cfab81919cb99f0799b9f09a71b2c4e93067e66002490c79c3f5c9
and ev_7078d774012a505220869ff1cc45cb4edc1f0f97582a0744426cfe42b1c92599.

`gpfs_start`0x105698 opens `/data/misc/qsee/` and `/persist/data/` as directories
and caches whether each is unavailable. It parses
`/vendor/etc/gpfspath_oem_config.xml`; its default roots match that stock XML:

| Path | Stock role and mainline comparison |
| --- | --- |
| `/data/vendor/tzstorage/` | GP data root. Inherited mainline already creates it0770 system:system and restores its label during post-fs-data. |
| `/mnt/vendor/persist/data/` | GP persist root. Stock uses physical persist; mainline's existing bind uses `/metadata/a9-tee-persist/data`. This task does not alter either. |
| `/persist/data/` | Legacy path. If unavailable and the requested parent cannot be opened, libdrmfs can prepend `/mnt/vendor/`. Actual `/persist` alias availability matters. |
| `/data/misc/qsee/` | Legacy path and XML rewrite prefix. Our inherited builder additionally creates this directory, unlike the scanned stock vendor init files; corrected below. |
| `/data/system/users/`, `/qwes`, `/qwes/licenses` | Other three stock XML rewrite prefixes. These are conditional path rewrites, not instructions to create new copies of those trees. |

REA ev_b3579644d4c60cd019a894d6b199035a5169be6bc55c38b44a31326c49d51cb6.
The XML is configuration, not a list of objects that exist on the device.

`get_vendor_path`0x106b24 first tries the requested pathname's parent. Only if
that fails does it consider the cached legacy state and XML prefix list. It can
prepend the GP data root to a listed legacy path, or `/mnt/vendor/` to
`/persist/data/...`; it then removes one leading slash from the original suffix.
For example, a rewritten `/data/misc/qsee/<object>` becomes
`/data/vendor/tzstorage/data/misc/qsee/<object>`, not
`/data/vendor/misc/qsee/<object>`.
REA ev_cdfbaa556f4b6a91645ec21a07328b9080c40d64b3b663b568dbd63662d9a9b3.

`gpdispatch`0x105b80 selects data-root operations4..7 and persist-root
operations8..11; legacy operations0..3 use parent/legacy availability when
building paths. `file_open`0x1065f8 opens the selected pathname and records errno
on failure; on a create request it can create parent directories through
`mkdir_h`. `gpfile_readwrite_helper`0x1070cc performs the actual open/read/write
and optional backup-file operations. Ordinary filesystem dispatch0x104438
likewise delegates pathname selection to `get_vendor_path` for relevant calls.
REA ev_24646cedae9342374168e12311efa5626de020bb20474fca638cc9a138fdeff1,
ev_187244b8139155a6da133121674fcfaccb2ecd8867ca955b1c464a2a2a523011,
ev_6e402bab6b374c856d65de6f69ab340a59121354aa3544c05acce258cdfb422a,
ev_03f014e9550ff4506184f71dc312a7178ae8e1a6c3ed0b3b0b740e801cf2c2c0.
These are static capabilities, not evidence of runtime write requests.

The listener receives object names from the TEE; it does not contain a fixed
inventory of every SFS filename. The stock Keymaster TA separately names
`attest_key`, `/persist/data/rsa_attest_key`, `/persist/data/ecc_attest_key` and
an alternate `attest_key_sb` path. Its null qsee_sfs_open produces-10003, as
[previously traced](mainline-keystore-compat.md). A logical SFS name is not proof
of the exact on-disk backing filename or its presence/health. The selected
listener evidence does not establish a fixed KMTB pathname.

## Request cancellation is not a storage directory

libGPreqcancel.so `gp_reqcancel_start`0x103014 registers its listener and calls
libGPreqcancel_svc.so. The latter's `gp_reqcancel_svc_sock_init`0x103014 requires
a leading-NUL name, creates an AF_UNIX/SOCK_DGRAM socket and binds an abstract
address. It does not need a `/data/vendor` socket directory. No storage-directory
fix follows from this cancellation listener.
REA ev_8fe08f08170a01c86e3074bbb0f87b3bcc54098ca48c5ac06f37f02dcaac4952
and ev_9bf1abe2b0969e09ed55bc8d04e04edb869b64d8ac5455f18ec0d754c8afb436.

## Init correction and limits

Stock vendor init.qcom.rc creates `/data/vendor/tzstorage`0770 system:system.
The inherited mainline hardware init already matches that mode and restores
its label. Its extra `/data/misc/qsee` mkdir/restorecon directives originate in
the earliest located historical builder82169de98ee54ad7c649dc466cfad3f69b2bda65,
`scripts/mainline/storage-row29/data/wrapped/build-vendor.py`64..71. That builder
is absent from HEAD and is not an ancestor of main. The matching generated
block persists in the actual inherited image, which build14 carries forward;
every intermediate artifact-generation step was not reconstructed.

The current credentials packaging step now omits exactly those two legacy
directives. Otherwise a later request whose original parent now exists can
bypass the vendor-data rewrite and select another store. This corrects a
configuration divergence on freshly formatted data; it is not a demonstrated
cause or repair of the retained attestation error.

No runtime unlink, migration, cleanup, service restart or data rewrite is added.
Existing legacy directories and files remain untouched and can still affect
routing. No global directory or service-start timing change is made. Stock
vendor init absence does not establish absence from every stock system image;
normal stock SYSTEM init was unavailable in the selected reference.

Stock vendor init also creates `/data/vendor/misc`01771 and `/data/vendor/qtee`
0770 system:system; these are absent from the selected mainline init files.
No selected filesystem/cancellation listener consumer of those parents was
established, so adding them would not be a supported fix for these failures.
In particular, `/data/vendor/misc/qsee` is not the XML rewrite destination.

Mainline starts qseecomd before mounting real data; its post-fs-data directory
action follows the mount. Stock qseecomd.rc also explicitly starts the daemon
on init, as does init.target.rc after preparing bootdevice. Its class/core
membership alone does not describe that earliest start. Deferring the daemon
until data exists would change the pre-FBE dependency rather than simply match
stock, and is not part of this correction.
The selected GSI also conditionally bind-mounts `/mnt/vendor/persist` at
`/persist` during post-fs. No runtime alias, cache state or successful read is
inferred from that script's presence.

The selected vendor file-context mappings differ in type names:

| Path | Stock vendor mapping | Mainline mapping |
| --- | --- | --- |
| `/data/vendor/tzstorage(/.*)?` | vendor_data_tzstorage_file | tee_data_file |
| `/data/vendor/misc/qsee(/.*)?` | vendor_data_qsee_file | No dedicated rule; this is not the GP rewrite destination. |
| `/mnt/vendor/persist/data(/.*)?` | vendor_persist_data_file | tee_data_file, covered by the existing persist-root rule. |
| `/metadata/a9-tee-persist(/.*)?` | Not a stock path | tee_data_file |
| `/data/misc/qsee(/.*)?` | No dedicated rule in selected stock vendor contexts | tee_data_file |

Different type names alone do not establish an access failure. The mainline
tzstorage mkdir/restorecon already matches its declared tee_data_file mapping;
no label or policy change is included in this correction.

Stock vendor policy grants tee_30_0 full directory/file management for its
tzstorage, qsee and persist-data types (vendor CIL4806..4810,6610..6611).
Mainline grants the equivalent management sets on tee_data_file_202504 to
tee_202504 (vendor CIL2463,2466), symlink read/getattr (5575) and mount-directory
traversal (5576). The current platform202504 mapping resolves tee, tee_data_file
and mnt_vendor_file directly (598,4075,4183). No missing TEE access rule for
tzstorage is established by this comparison. Policy source cannot prove that
mkdir/restorecon succeeded on hardware.

Stock fstab.default:46 mounts persist as ext4 with
noatime,nosuid,nodev,barrier=1,wait and no read-only option. Init.target.rc51..55
sets its root0771 root:system;103..104 restores labels and creates data0700
system:system. Mainline's existing metadata-backed namespace matches these DAC
modes while deliberately using a different backing store. Neither stock's
writable mount nor its directory-creation action is copied into a physical
persist operation by this change.

## Persist decision boundary

The remaining attestation candidates include logical `/persist/data/rsa_attest_key`
and `/persist/data/ecc_attest_key`, potentially translated to the corresponding
`/mnt/vendor/persist/data/...` paths. GP persist operations use the latter root
directly. These reach the existing isolated metadata-backed namespace on mainline,
not automatically the original factory store.

Changing persist writability, creating/replacing its files, exposing factory
credentials through a new mount, or changing the backing store is unimplemented.
The retained evidence does not yet prove that a persist write is required or
safe. Making it writable would not itself establish valid SFS state. Actual
numeric observer results are still needed to separate absent objects, access
failure and secure/backend rejection. RPMB sector reads use their separate SG
backend; creating a data directory does not prove those reads work.

## Evidence and host validation

Stock target SHA256 values:

| Artifact | SHA256 |
| --- | --- |
| qseecomd | 5be26351f46f1e129fa5c780bbb49817bd91f38d58cadc07152620ccc4fc1f51 |
| libdrmfs.so | ec6c598181c0de0f8e776b5c1560a62c1c48a971c0a6c137f6b9bc6f780e6ec0 |
| libGPreqcancel.so | 9602fc69424bf0b77d20796278fdadfc33103ccf09ca48992d3d630f340be3fe |
| libGPreqcancel_svc.so | ecbace27e0afef056878811d59a8ae33a8a4e877ca8a97d86fb26a905d0d5aa9 |

Complete REA bundles, decoded relocated listener table and stock XML are retained
at `A9-build/mainline/fbe-sfs-paths-20261024/`; all sessions were exported before
closing. Addresses above are Ghidra image addresses with base0x100000.
The focused `credentials/test-sfs-data.py` smoke uses the immutable integ20
hardware init. It passes exact two-directive removal, idempotence, unchanged
remaining bytes including tzstorage/persist/service order, and host_init_verifier.
Results: `fbe-sfs-paths-20261024/init-test/tests.json`. No full image build or
hardware attestation/RPMB success is claimed.
Configuration extracts and the independent scoped GO are retained under
`A9-build/mainline/fbe-sfs-config-host/{stock,mainline}` and
`A9-wt/fbe-pin-package/runs/fbe-sfs-config.md` respectively.
