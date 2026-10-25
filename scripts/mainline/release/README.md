# A9 row27 vendor release

For current mainline system **and** vendor release configuration, use
[docs/mainline-release.md](../../../docs/mainline-release.md).
`A9_RELEASE=1`/`--release` now selects the common host finalizer in both integrated
builders; development remains the default. The dated row27 recipe below is the
earlier vendor-only experiment, not the current full release build.

`row27-release-20261022/NOTES.md` under A9-build/mainline is the completed image
handoff, exact coordinator command and host evidence. Build inputs are the
fencefix plain/deadline boots and exact audiofix4 vendor. No system image is
created, modified or re-signed. Dedicated signing keys remain outside Git.

The NAS export enforces0770 and rejects chmod. The requested
`$A9_DATA/keys/mainline-release-20261022` path therefore points to the private
local `~/.local/share/a9-mainline/keys/mainline-release-20261022` directory:
files0600 and directories0700. Back up that real directory securely; the NAS
symlink alone does not back up its contents. Five Android certificate/key pairs,
one boot AVB key and11unique APEX payload keys are retained there. No key or
certificate file belongs in this source tree.

`build.py` and `prepare-keys.py` take the signing directory from `--keys` or,
when the flag is absent, from the `A9_RELEASE_KEYS` environment variable. The
public-release platform key (2026-10-25, neutral subject `CN=a9-release`) lives
in the gitignored `local/release/keys/` (`releasekey.pk8`, `.x509.pem`, `.key`)
with a copy in the password manager; run `prepare-keys.py` against it to add the
APEX payload keys before a build that uses it.

`sign-vendor-apexes.py` signs both layers of each existing vendor APEX and checks
container certificate, payload signature/hashtree and unchanged filesystem
bytes. This vendor has no standalone or nested APKs. `build-row27.py` applies
secure user defaults, removes vendor test consoles/awake actions, keeps the USB
configfs2 handoff and generation-deduplicated events, and links a zero-permissive
precompiled policy against the unchanged GSI inputs. Vendor init receives only
the two additional property-type set permissions needed to override the GSI's
insecure build defaults. The precompiled policy hash selectors match the
unchanged system; three old GSI system_ext permissive declarations are omitted
from the compiled policy without editing their source partition.

The canonical boot.img is the existing fencefix deadline/logger/WDT variant,
which B-try requires. boot-plain.img is the exact plain fencefix boot. Both have
the same public kernel/module ABI. No new kernel or watchdog owner is built.

`b-try-twrp-preboot.patch` adds one optional executable hook in the existing
format position. The output contains a private b-try.sh snapshot with that small
patch; the shared runner is left untouched while agent may be using it. A hook
requires a vendor plus FORMAT=1 and replaces the generic TWRP format-data call.
All original flash, observation, return and logging control flow is retained.

`prepare-row27-twrp.py` is an inert coordinator-only hook. It reuses the existing
bounded row29 software-FBE formatter, then the exact unchanged boot mapper,
mounts that TEST filesystem privately, installs only the caller's public ADB
key and unmounts it. The helper change only adds TWRP's /sbin mkfs paths. No
stock recovery fstab or real userdata is changed. Android defaults enable
key-authenticated USB ADB before setup; debug/root privileges remain disabled.
No host private ADB key or public key is baked into the image. Native helpers,
sources, sole path-list delta and build receipts accompany the output.

Host checks do not qualify fresh-data setup or survival on the phone. agent
owns execution and any independent storage/boot review and phone acceptance.
No fake-phone suite or worker device call is used for this build.
