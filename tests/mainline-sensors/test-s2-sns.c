// SPDX-License-Identifier: GPL-2.0-only
/* Offline harness for s2-sns + s2-sns-probe. The SSC side is a script written by
 * test-s2-sns.py with an INDEPENDENT protobuf/QMI encoder: every frame the probe
 * sends must equal the scripted bytes exactly; replies are delivered only after
 * the matching send. Virtual clock: a recv with nothing queued advances time.
 *   test-s2-sns <scenario> <expected last marker line>
 *   test-s2-sns --units
 */
#define S2_PROBE_NO_MAIN
#include "../../scripts/mainline/sensors/s2-sns.c"
#include "../../scripts/mainline/sensors/s2-client.c"
#include "../../scripts/mainline/sensors/s2-sns-probe.c"
#include <assert.h>
#include <stdlib.h>

#define MAXSTEP 512
struct step { int send; size_t n; uint8_t *b; };
static struct step steps[MAXSTEP]; static size_t nsteps, pos;
static int64_t vclock; static int open_handles, failed;
static int queue_h[MAXSTEP]; static uint8_t *queue_b[MAXSTEP]; static size_t queue_n[MAXSTEP], qhead, qtail;

static int hexval(int c) { return c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 : -1; }
static void load(const char *path)
{
    FILE *f = fopen(path, "r"); char *line = NULL; size_t cap = 0; ssize_t len;
    assert(f);
    while ((len = getline(&line, &cap, f)) > 0) {
        if (line[len - 1] == '\n') line[--len] = 0;
        if (!len) continue;
        assert(nsteps < MAXSTEP);
        struct step *s = &steps[nsteps++];
        s->send = !strncmp(line, "SEND ", 5);
        assert(s->send || !strncmp(line, "RECV ", 5));
        const char *h = line + 5; size_t hl = strlen(h); assert(hl % 2 == 0);
        s->n = hl / 2; s->b = malloc(s->n ? s->n : 1);
        for (size_t i = 0; i < s->n; i++) { int a = hexval(h[2 * i]), b = hexval(h[2 * i + 1]); assert(a >= 0 && b >= 0); s->b[i] = (uint8_t)(a << 4 | b); }
    }
    free(line); fclose(f);
}
static int f_open(void *ctx) { (void)ctx; return open_handles < 64 ? open_handles++ : -1; }
static int f_send(void *ctx, int h, const uint8_t *b, size_t n)
{
    (void)ctx;
    if (pos >= nsteps || !steps[pos].send || steps[pos].n != n || memcmp(steps[pos].b, b, n)) {
        fprintf(stderr, "MISMATCH at step %zu (sent %zu bytes):", pos, n);
        for (size_t i = 0; i < n; i++) fprintf(stderr, "%02x", b[i]);
        fputc('\n', stderr); failed = 1; return -1;
    }
    pos++;
    while (pos < nsteps && !steps[pos].send) { queue_h[qtail] = h; queue_b[qtail] = steps[pos].b; queue_n[qtail] = steps[pos].n; qtail++; pos++; }
    return 0;
}
static int f_recv(void *ctx, int h, uint8_t *b, size_t cap, int timeout_ms)
{
    (void)ctx;
    if (qhead < qtail) {
        assert(queue_h[qhead] == h);   /* replies go to the connection that sent */
        size_t n = queue_n[qhead]; assert(n <= cap);
        memcpy(b, queue_b[qhead], n); qhead++; vclock += 1; return (int)n;
    }
    vclock += timeout_ms; return 0;
}
static void f_close(void *ctx, int h) { (void)ctx; (void)h; }
static int64_t f_now(void *ctx) { (void)ctx; return vclock; }

static void units(void)
{
    uint8_t b[256]; struct s2_buf x = { b, 0, sizeof(b), 0 };
    s2_pb_varint(&x, 300); assert(x.n == 2 && b[0] == 0xac && b[1] == 0x02);
    struct s2_buf tiny = { b, 0, 1, 0 }; s2_pb_fixed32(&tiny, 1, 7); assert(tiny.err);
    /* malformed protobuf is refused, never over-read */
    struct s2_event_msg m;
    uint8_t trunc[] = { 0x0a, 0x10, 0x09 };               /* length beyond buffer */
    assert(s2_decode_event_msg(trunc, sizeof(trunc), &m) < 0);
    uint8_t badvar[] = { 0x08, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x01 };
    assert(s2_decode_event_msg(badvar, sizeof(badvar), &m) < 0);
    uint8_t group[] = { 0x0b };                             /* start-group wire type */
    assert(s2_decode_event_msg(group, sizeof(group), &m) < 0);
    uint8_t nosuid[] = { 0x12, 0x00 };                      /* event without suid */
    assert(s2_decode_event_msg(nosuid, sizeof(nosuid), &m) < 0);
    /* QMI framing bounds */
    struct s2_qmi_resp r;
    uint8_t short_resp[] = { 2, 1, 0, 0x20, 0, 4, 0, 0x02, 4, 0 };
    assert(s2_qmi_decode_response(short_resp, sizeof(short_resp), 1, &r) < 0);
    uint8_t ok_resp[] = { 2, 1, 0, 0x20, 0, 7, 0, 0x02, 4, 0, 0, 0, 0, 0 };
    assert(!s2_qmi_decode_response(ok_resp, sizeof(ok_resp), 1, &r) && !r.result);
    assert(s2_qmi_decode_response(ok_resp, sizeof(ok_resp), 2, &r) < 0);   /* wrong txn */
    uint64_t cid; const uint8_t *pl; size_t pll;
    uint8_t ind_bad[] = { 4, 0, 0, 0x21, 0, 0x0e, 0, 0x01, 8, 0, 1,2,3,4,5,6,7,8 };     /* missing payload TLV, length lies */
    assert(s2_qmi_decode_indication(ind_bad, sizeof(ind_bad), &cid, &pl, &pll) < 0);
    uint8_t ind_arr[] = { 4, 0, 0, 0x21, 0, 0x13, 0, 0x01, 8, 0, 1,0,0,0,0,0,0,0, 0x02, 5, 0, 4, 0, 1, 2, 3 }; /* array len 4 > TLV 5-2 */
    assert(s2_qmi_decode_indication(ind_arr, sizeof(ind_arr), &cid, &pl, &pll) < 0);
    /* payload bound: request > 1000 refused */
    static uint8_t big[1001], fr[1200];
    assert(!s2_qmi_request(fr, sizeof(fr), 1, big, sizeof(big)));
    assert(!s2_suid_req(b, sizeof(b), "", 1));
    assert(!s2_sensor_config(b, sizeof(b), 0.0f) && !s2_sensor_config(b, sizeof(b), 5000.0f));
    char s[8]; uint8_t raw[] = { 'a', '"', '\\', 0x01, 'z' }; s2_sanitize(s, sizeof(s), raw, sizeof(raw)); assert(!strcmp(s, "a???z"));
    /* F1: exactly one trailing NUL is dropped before sanitising (stock 0x14b5b8); a second one stays visible */
    {
        struct s2_attrs at;
        /* attr{id=1 vendor, value{str "sensortek\0"}} */
        uint8_t one[] = { 0x0a, 0x12, 0x08, 0x01, 0x12, 0x0e, 0x0a, 0x0c, 0x12, 0x0a,
                          's','e','n','s','o','r','t','e','k', 0 };
        memset(&at, 0, sizeof(at));
        assert(!s2_decode_attr_event(one, sizeof(one), &at) && !strcmp(at.vendor, "sensortek"));
        uint8_t two[] = { 0x0a, 0x13, 0x08, 0x01, 0x12, 0x0f, 0x0a, 0x0d, 0x12, 0x0b,
                          's','e','n','s','o','r','t','e','k', 0, 0 };
        memset(&at, 0, sizeof(at));
        assert(!s2_decode_attr_event(two, sizeof(two), &at) && !strcmp(at.vendor, "sensortek?"));
        uint8_t none[] = { 0x0a, 0x11, 0x08, 0x01, 0x12, 0x0d, 0x0a, 0x0b, 0x12, 0x09,
                           's','e','n','s','o','r','t','e','k' };
        memset(&at, 0, sizeof(at));
        assert(!s2_decode_attr_event(none, sizeof(none), &at) && !strcmp(at.vendor, "sensortek"));
        uint8_t only[] = { 0x0a, 0x09, 0x08, 0x01, 0x12, 0x05, 0x0a, 0x03, 0x12, 0x01, 0 };
        memset(&at, 0, sizeof(at));
        assert(!s2_decode_attr_event(only, sizeof(only), &at) && !strcmp(at.vendor, ""));
    }
    assert(s2_part_expected("ambient_light", "STK3B6X Ambient Light") == 1 && s2_part_expected("accel", "stk8baxx") == 1);
    assert(s2_part_expected("accel", "KXTJ3") == 1 && s2_part_expected("accel", "bmi160") == 0 && s2_part_expected("accel", "") == 0);
    assert(s2_part_expected("proximity", "stk3b6x") == 1 && s2_part_expected("gyro", "x") == -1 && !s2_part_tokens("gyro"));
    puts("S2-SNS UNITS PASS");
}
int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--units")) { units(); return 0; }
    assert(argc == 3);
    load(argv[1]);
    struct s2_link L = { f_open, f_send, f_recv, f_close, f_now, NULL };
    char *out = NULL; size_t outn = 0; FILE *log = open_memstream(&out, &outn);
    int rc = s2_probe_run(&L, log);
    fclose(log);
    fputs(out, stdout);
    /* the expected line must occur exactly; PASS also needs rc 0 and the whole script consumed */
    int seen = 0; for (char *p = strtok(out, "\n"); p; p = strtok(NULL, "\n")) if (!strcmp(p, argv[2])) seen = 1;
    int want_pass = !strcmp(argv[2], "S2-PROBE-PASS");
    if (failed || !seen || (want_pass && (rc || pos != nsteps)) || (!want_pass && !rc)) {
        fprintf(stderr, "UNEXPECTED rc=%d failed=%d seen=%d pos=%zu/%zu\n", rc, failed, seen, pos, nsteps); return 1;
    }
    return 0;
}
