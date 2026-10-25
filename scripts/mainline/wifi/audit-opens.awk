# Companion launch audit; intentionally fails closed on unknown trace syntax.
# strace -f -yy -s 256, without read/write/recv/send payload tracing.
# Private seed paths here name vendor IMAGE files, never live partitions.
function bad(s) { print "FAIL trace: " s > "/dev/stderr"; failed=1 }
BEGIN { opened=0; execed=0; jailed=0; detached=0; filtered=0 }
/S_IFBLK|\/dev\/block|\/dev\/disk|\/dev\/mem[>" ,)]|\/dev\/uio|\/mnt\/vendor\/persist|"\/persist/ { bad("block/persist capability or access") }
/(open|openat|openat2|creat|execveat)\(.*(<unfinished|resumed>)/ { bad("split relevant syscall; recollect after quiescence") }
/pivot_root\(.* = 0$/ { jailed++ }
/umount2\("\/.old", MNT_DETACH\)[[:space:]]+= 0$/ { detached++ }
/(seccomp\(SECCOMP_SET_MODE_FILTER|prctl\(PR_SET_SECCOMP, SECCOMP_MODE_FILTER).* = 0$/ { filtered++ }
/execveat\(30(<[^>]*>)?, "", .*AT_EMPTY_PATH\) = 0$/ { execed++; next }
/(open|openat|openat2|creat)\(/ {
    if ($0 !~ / = [0-9]+(<[^<>]*(<[^<>]*>)?>)?$/) {
        if ($0 ~ / = -1 EPERM / && execed == 1) next
        bad("failed/unknown open before daemon confinement"); next
    }
    if (execed) { bad("successful pathname open after restricted daemon exec"); next }
    # All production opens use a literal, unescaped fixed name. Reject openat
    # directory-FD addressing and omitted/escaped/path-truncated output.
    s=$0
    sub(/^.*(openat2|openat)\(AT_FDCWD(<\/>)?, /,"",s)
    sub(/^.*(open|creat)\(/,"",s)
    if (s !~ /^"[^"\\]*", /) { bad("nonliteral/non-AT_FDCWD open"); next }
    path=s; sub(/^"/,"",path); sub(/".*$/,"",path)
    ro=(s ~ /O_RDONLY/ && s !~ /O_(CREAT|TRUNC|APPEND|PATH)/)
    # Static Bionic startup reads these exact Android property objects before
    # main enters the jail. Never admit them after pivot or daemon exec.
    property=(path == "/dev/__properties__/property_info" ||
              path == "/dev/__properties__/properties_serial" ||
              path == "/dev/__properties__/u:object_r:debug_prop:s0" ||
              path == "/dev/__properties__/u:object_r:arm64_memtag_prop:s0" ||
              path == "/dev/__properties__/u:object_r:device_config_memory_safety_native_prop:s0" ||
              path == "/dev/__properties__/u:object_r:build_prop:s0")
    if (property && ro && !jailed) { opened++; next }
    asset=(path ~ /^\/vendor\/etc\/a9-wifi\/seeds\/(modemst1|modemst2|fsg|fsc)\.bin$/ ||
           path == "/vendor/etc/a9-wifi/assets.sha256" ||
           path ~ /^\/vendor\/bin\/(rmtfs|tqftpserv|pd-mapper|diag-router)$/ ||
           path == "/vendor/firmware/qcom/sm6115/hisense/hlte556n/wlanmdsp.mbn" ||
           path ~ /^\/sys\/class\/rmtfs\/qcom_rmtfs_mem1\/(dev|client_id|phys_addr|size)$/ ||
           path == "/dev/null" || path == "/mnt/a9-wifi/trial/rmtfs.admission")
    ram=(path ~ /^(modemst1|modemst2|fsg|fsc|mcfg\.tmp|server_check\.txt)$/)
    if (asset && ro) { opened++; next }
    if (path == "/dev/qcom_rmtfs_mem1" && s ~ /O_RDWR/ && s !~ /O_(CREAT|TRUNC|APPEND|PATH)/) { opened++; next }
    if (ram && jailed == 1 && detached == 1 && s ~ /O_RDWR/ && s ~ /O_CREAT/ && s ~ /O_EXCL/ && s !~ /O_(TRUNC|APPEND|PATH)/) { opened++; next }
    bad("open outside fixed preparation allowlist")
}
END {
    if (!opened || execed != 1 || jailed != 1 || detached != 1 || filtered != 1)
        bad("missing complete preparation/confinement/exec evidence")
    if (failed) exit 1
    print "A9_WIFI_OPEN_AUDIT_OK (traced helper and daemon only; not MPSS/TZ)"
}
