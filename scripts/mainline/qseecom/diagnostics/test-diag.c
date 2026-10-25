/* Host synthetic replay harness; all parsers/queue/transport are production C. */
#include "diag.h"
#include "transport.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

static int parse(const char *body)
{
    char raw[1024], output[DIAG_TEXT];
    int n = snprintf(raw, sizeof(raw), "6,18,300003,-;%s\n", body);
    return diag_parse(raw, (size_t)n, output);
}
static void parser(void)
{
    static const char *good[] = {
        "A9_RPMB_REGISTER request=8192 mapped=8192 buf_page_aligned=1 len_page_aligned=1 cmd=6 arginfo=0x83",
        "A9_RPMB_REGISTER check=scm_return cmd=1 rc=-22",
        "A9_RPMB_REGISTER phase=6 rc=0",
        "A9_RPMB_SCM check=unknown_convention rc=-22",
        "A9_RPMB_SCM check=ce_clock rc=0", "A9_RPMB_SCM check=bandwidth rc=0",
        "A9_RPMB_SCM check=transport_return rc=-5",
        "A9_RPMB_SCM check=missing_ext_pool nargs=3 rc=-22",
        "A9_RPMB_SCM check=invoke fn=0x72000206 arginfo=0x83 nargs=3 size=8192 buf_page_aligned=1 len_page_aligned=1",
        "A9_RPMB_SCM check=smc_return transport=0 status_valid=1 secure_status=4294967294 mapped_rc=-22",
        "A9_RPMB_SCM check=smc_return transport=0 status_valid=1 secure_status=-9223372036854775808 mapped_rc=-22",
        "A9_RPMB_GUARD mode=1 allowed=0 operation=0 reason=6",
        "A9_RPMB_GUARD mode=2 allowed=1 operation=5 reason=0",
        "A9_RPMB_GUARD mode=2 allowed=1 operation=6 reason=0",
        "A9_KMTB_WRITE phase=1 allowed=1 operation=1 reason=0 changed=1",
        "A9_KMTB_WRITE phase=2 allowed=0 operation=0 reason=3 changed=0",
        "A9_KMTB_WRITE phase=3 allowed=1 operation=2 reason=0 changed=1",
        "A9_KMTB_READ phase=2 valid=1",
        "A9_RPMB_BOOTSTRAP phase=5 rc=0",
        "A9_RPMB_MAP phase=1 valid=0", "A9_RPMB_MAP phase=2 valid=1",
        "A9_RPMB_REQUEST_TRACE command=3 shape=1 reason=0 request=3 frames=34 input_frames=34 offset=24 first=2050 count=34 map_valid=0 context=10 table_candidate=0 table_valid=0",
        "A9_RPMB_WRITE_TRACE allowed=0 map_valid=0 groups_valid=1 partition=0 first=2050 offset=0 frames=34 context=10",
        "A9_RPMB_REJECT reason=3 opcode=2 direction=2 bytes_valid=1 length_match=1 frames=24 frame_valid=1 request=3",
        "A9_RPMB_REJECT reason=3 opcode=2 direction=3 bytes_valid=0 length_match=0 frames=0 frame_valid=0 request=0",
        "A9_RPMB_RESULT command=2 status_valid=1 operation_rc=-2147483648",
        "A9_RPMB_RESULT command=3 status_valid=0 operation_rc=0",
        "A9_RPMB_CALLBACK phase=1 command=2 size_valid=1",
        "A9_RPMB_CALLBACK phase=2 command=2 rc=0 status_valid=1 qsee_result=0",
        "A9_RPMB_QUERY idn=7 rpmb_lun=1 rc=-22 length_valid=0",
        "A9_QSEE_LOG phase=3 rc=0", "A9_QSEE_CAPTURE phase=1 rc=-2",
        "qseecom: Android transport ready (native arm64, whitelist 1)"
    };
    char appended[1024];
    for (size_t i = 0; i < sizeof(good)/sizeof(good[0]); ++i) {
        assert(parse(good[i]) == 1);
        const char *suffix[] = {" secret=key", " ptr=0xffff888012345678", " sector=123", " token=abc", " ", "\nTA=content"};
        for (size_t j = 0; j < sizeof(suffix)/sizeof(suffix[0]); ++j) {
            snprintf(appended, sizeof(appended), "%s%s", good[i], suffix[j]);
            assert(parse(appended) != 1);
        }
    }
    const char *bad[] = {
        "A9_RPMB_REGISTER phase=7 rc=0", "A9_RPMB_REGISTER phase=1 rc=2147483648",
        "A9_RPMB_SCM check=smc_return transport=0 status_valid=1 secure_status=9223372036854775808 mapped_rc=-22",
        "A9_RPMB_SCM check=invoke fn=0xffff888012345678 arginfo=0x83 nargs=3 size=8192 buf_page_aligned=1 len_page_aligned=1",
        "A9_RPMB_REGISTER phase=1 rc=0x1234", "A9_RPMB_REGISTER phase=01 rc=0",
        "A9_RPMB_GUARD mode=2 allowed=1 operation=7 reason=0", "A9_QSEE_LOG phase=0 rc=0",
        "A9_RPMB_GUARD mode=3 allowed=1 operation=5 reason=0",
        "A9_KMTB_WRITE phase=0 allowed=1 operation=1 reason=0 changed=1",
        "A9_KMTB_WRITE phase=1 allowed=1 operation=4 reason=0 changed=1",
        "A9_KMTB_READ phase=2 valid=2",
        "A9_RPMB_BOOTSTRAP phase=7 rc=0",
        "A9_RPMB_BOOTSTRAP phase=5 rc=0 secret=x",
        "A9_RPMB_MAP phase=0 valid=0", "A9_RPMB_MAP phase=2 valid=2",
        "A9_RPMB_WRITE_TRACE allowed=0 map_valid=0 groups_valid=1 partition=0 first=65536 offset=0 frames=34 context=10",
        "A9_RPMB_WRITE_TRACE allowed=0 map_valid=0 groups_valid=1 partition=0 first=2050 offset=0 frames=50 context=10",
        "A9_RPMB_WRITE_TRACE allowed=0 map_valid=0 groups_valid=1 partition=0 first=2050 offset=0 frames=34 context=11",
        "A9_RPMB_REQUEST_TRACE command=3 shape=1 reason=0 request=3 frames=34 input_frames=34 offset=1048577 first=2050 count=34 map_valid=0 context=10 table_candidate=0 table_valid=0",
        "A9_RPMB_REJECT reason=0 opcode=2 direction=2 bytes_valid=1 length_match=1 frames=24 frame_valid=1 request=3",
        "A9_RPMB_REJECT reason=3 opcode=3 direction=2 bytes_valid=1 length_match=1 frames=24 frame_valid=1 request=3",
        "A9_RPMB_REJECT reason=3 opcode=2 direction=2 bytes_valid=1 length_match=1 frames=4294967296 frame_valid=1 request=3",
        "A9_RPMB_REJECT reason=3 opcode=2 direction=2 bytes_valid=1 length_match=1 frames=24 frame_valid=1 request=6",
        "A9_RPMB_RESULT command=1 status_valid=1 operation_rc=0",
        "A9_RPMB_RESULT command=3 status_valid=1 operation_rc=2147483648",
        "A9_RPMB_CALLBACK phase=1 command=258 size_valid=1",
        "A9_RPMB_CALLBACK phase=2 command=2 rc=0 status_valid=1 qsee_result=9223372036854775808",
        "A9_RPMB_QUERY idn=3 rpmb_lun=1 rc=0 length_valid=1",
        "A9_QSEE_INIT ready=1 whitelist=1", "qseecom: LOAD keymaster64 secret", "kernel arbitrary 0x12345678"
    };
    for (size_t i = 0; i < sizeof(bad)/sizeof(bad[0]); ++i) assert(parse(bad[i]) != 1);
    char nul[] = "6,18,300003,-;A9_RPMB_REGISTER phase=1 rc=0\0secret\n";
    char output[DIAG_TEXT]; assert(!diag_parse(nul, sizeof(nul)-1, output));
    assert(!diag_parse("bad;A9_RPMB_REGISTER phase=1 rc=0\n", 36, output));
    puts("parser PASS");
}
struct stub { int ready, fails, accepted; char saved[3][DIAG_TEXT]; };
static int submit(const char *text, void *ctx)
{
    struct stub *s = ctx;
    if (!s->ready || s->fails-- > 0) return 0;
    assert(s->accepted < 3); strcpy(s->saved[s->accepted++], text); return 1;
}
static void queue(void)
{
    struct diag_state s = {0}; struct stub t = {0};
    const char *one = "6,1,1,-;A9_RPMB_REGISTER phase=1 rc=0\n";
    const char *two = "6,2,2,-;A9_RPMB_REGISTER phase=2 rc=-22\n";
    diag_record(&s, one, strlen(one)); diag_record(&s, two, strlen(two));
    diag_flush(&s, submit, &t); assert(s.count == 2 && !s.sent);
    t.ready = 1; t.fails = 1; diag_flush(&s, submit, &t); assert(s.count == 2);
    diag_flush(&s, submit, &t); diag_flush(&s, submit, &t);
    assert(t.accepted == 2 && !s.count && s.sent == 2);
    assert(strstr(t.saved[0], "phase=1") && strstr(t.saved[1], "phase=2"));
    for (unsigned i = 0; i < DIAG_QUEUE+3; ++i) diag_record(&s, one, strlen(one));
    assert(s.count == DIAG_QUEUE && s.dropped == 3);
    char oversized[8192]; memset(oversized, 'X', sizeof(oversized));
    diag_record(&s, oversized, sizeof(oversized)); assert(s.truncated == 1);
    s.overrun = UINT32_MAX; diag_increment(&s.overrun); assert(s.overrun == UINT32_MAX);
    puts("queue PASS");
}
static void replay(const char *input, const char *socket_path)
{
    FILE *f = fopen(input, "r"); assert(f);
    struct diag_state s = {0}; char raw[8192];
    while (fgets(raw, sizeof(raw), f)) diag_record(&s, raw, strlen(raw));
    fclose(f); assert(s.count == DIAG_QUEUE);
    struct diag_transport t = {-1, socket_path};
    unsigned failed = 0;
    for (unsigned i = 0; i < 100 && s.count; ++i) {
        unsigned before = s.count; diag_flush(&s, diag_send, &t);
        if (s.count == before) ++failed;
        usleep(100000);
    }
    assert(!s.count && s.sent == DIAG_QUEUE && failed > 0);
    if (t.fd >= 0) close(t.fd);
    printf("replay PASS submitted=%u failed_attempts=%u\n", s.sent, failed);
}
int main(int argc, char **argv)
{
    if (argc == 1) { parser(); queue(); return 0; }
    if (argc == 2 && !strcmp(argv[1], "--filter")) {
        char raw[8192], output[DIAG_TEXT];
        while (fgets(raw, sizeof(raw), stdin)) {
            if (diag_parse(raw, strlen(raw), output) == 1) puts(output);
            else puts("REJECT");
        }
        return 0;
    }
    assert(argc == 3); replay(argv[1], argv[2]); return 0;
}
