/* Host test of eink-policy.h with a fake clock. */
#include "eink-policy.h"
#include <assert.h>
#include <stdio.h>

int main(void)
{
    struct ep_dyn d;
    struct ep_ac a;
    int64_t t = 1000;

    /* disabled: never acts */
    ep_dyn_init(&d, 0, 515, 513, 4, 500, 1000, 1);
    for (int i = 0; i < 10; i++) assert(ep_dyn_event(&d, t += 10) == EP_NONE);
    assert(ep_dyn_timeout(&d, t) == -1);

    /* rest Clear: 3 events nothing, 4th switches to fast, coalesced afterwards */
    ep_dyn_init(&d, 1, 515, 513, 4, 500, 1000, 1);
    for (int i = 0; i < 3; i++) assert(ep_dyn_event(&d, t += 100) == EP_NONE);
    assert(ep_dyn_event(&d, t += 100) == EP_SET_FAST);
    for (int i = 0; i < 20; i++) assert(ep_dyn_event(&d, t += 100) == EP_NONE);	/* no more writes */
    assert(ep_dyn_tick(&d, t + 999) == EP_NONE);	/* restore only 1000 ms after the LAST event */
    assert(ep_dyn_timeout(&d, t) == 1000 && ep_dyn_timeout(&d, t + 400) == 600);
    assert(ep_dyn_tick(&d, t + 1000) == EP_SET_REST);
    assert(ep_dyn_tick(&d, t + 5000) == EP_NONE && !d.active);	/* exactly once */

    /* slow, sparse events (gap > window) never accumulate */
    for (int i = 0; i < 50; i++) assert(ep_dyn_event(&d, t += 600) == EP_NONE);

    /* window starts at the FIRST event: 1000,1400,1800,2200 (gaps <= window) must not qualify N=4 */
    ep_dyn_init(&d, 1, 515, 513, 4, 500, 1000, 1);
    assert(ep_dyn_event(&d, 1000) == EP_NONE && ep_dyn_event(&d, 1400) == EP_NONE);
    assert(ep_dyn_event(&d, 1800) == EP_NONE && ep_dyn_event(&d, 2200) == EP_NONE && !d.active);
    for (int i = 0; i < 200; i++) assert(ep_dyn_event(&d, 3000 + i * 400) == EP_NONE);	/* slow stream */
    /* boundary: 4 events spanning exactly the window qualify, window+1 does not */
    ep_dyn_init(&d, 1, 515, 513, 4, 500, 1000, 1);
    ep_dyn_event(&d, 100); ep_dyn_event(&d, 300); ep_dyn_event(&d, 500);
    assert(ep_dyn_event(&d, 600) == EP_SET_FAST);
    ep_dyn_init(&d, 1, 515, 513, 4, 500, 1000, 1);
    ep_dyn_event(&d, 100); ep_dyn_event(&d, 300); ep_dyn_event(&d, 500);
    assert(ep_dyn_event(&d, 601) == EP_NONE);
    /* a new scroll after the restore starts a new cycle */
    t += 2000;
    for (int i = 0; i < 3; i++) assert(ep_dyn_event(&d, t += 50) == EP_NONE);
    assert(ep_dyn_event(&d, t += 50) == EP_SET_FAST);

    /* explicit user change while fast: new rest, no restore overwrite */
    ep_dyn_user_mode(&d, 518);
    assert(d.rest == 518 && !d.active);
    assert(ep_dyn_tick(&d, t + 100000) == EP_NONE);
    /* rest Smooth with only_clear: scrolling does nothing (stock only leaves Clear) */
    for (int i = 0; i < 20; i++) assert(ep_dyn_event(&d, t += 50) == EP_NONE);
    /* user picks Clear: scroll works again, restores to the CHOSEN mode */
    ep_dyn_user_mode(&d, 515);
    for (int i = 0; i < 3; i++) assert(ep_dyn_event(&d, t += 50) == EP_NONE);
    assert(ep_dyn_event(&d, t += 50) == EP_SET_FAST);
    assert(ep_dyn_tick(&d, t + 1000) == EP_SET_REST && d.rest == 515);

    /* any_rest + Smooth fast from Balanced rest; fast == rest is a no-op */
    ep_dyn_init(&d, 1, 513, 518, 2, 500, 200, 0);
    assert(ep_dyn_event(&d, t += 10) == EP_NONE && ep_dyn_event(&d, t += 10) == EP_SET_FAST);
    assert(ep_dyn_tick(&d, t + 200) == EP_SET_REST);
    ep_dyn_init(&d, 1, 518, 518, 1, 500, 200, 0);
    assert(ep_dyn_event(&d, t += 10) == EP_NONE && !d.active);

    /* activity clear: burst coalesces into one clear after the delay */
    ep_ac_init(&a, 1, 300, 6000);
    t = 100000;
    ep_ac_event(&a, t); ep_ac_event(&a, t + 100); ep_ac_event(&a, t + 250);
    assert(ep_ac_tick(&a, t + 549) == 0);
    assert(ep_ac_timeout(&a, t + 250) == 300);
    assert(ep_ac_tick(&a, t + 550) == 1);
    assert(ep_ac_tick(&a, t + 10000) == 0 && ep_ac_timeout(&a, t) == -1);
    /* inside the 6 s re-arm window: dropped, not queued */
    ep_ac_event(&a, t + 2000);
    assert(ep_ac_tick(&a, t + 2300) == 0 && ep_ac_timeout(&a, t + 2300) == -1);
    assert(ep_ac_tick(&a, t + 20000) == 0);
    /* re-armed at t+550+6000 */
    ep_ac_event(&a, t + 6549);	/* still cooling down: dropped on arrival */
    assert(ep_ac_timeout(&a, t + 6549) == -1);
    ep_ac_event(&a, t + 6551);
    assert(ep_ac_tick(&a, t + 6600) == 0);	/* delay not elapsed */
    assert(ep_ac_tick(&a, t + 6851) == 1);
    /* endless burst cannot starve the clear: fire time is capped at first + 4 x delay */
    ep_ac_init(&a, 1, 300, 6000);
    for (int i = 0; i < 100; i++) ep_ac_event(&a, t + i * 100);
    assert(a.fire_at == t + 1200 && ep_ac_tick(&a, t + 1200) == 1);
    /* hard minimums: zero/small configuration cannot remove the 6 s re-arm (review 68) */
    ep_ac_init(&a, 1, 0, 0);
    assert(a.rearm_ms == EP_MIN_REARM_MS && a.delay_ms == EP_MIN_DELAY_MS);
    {
        int fired = 0;
        for (int i = 0; i < 100000; i++) {	/* 100 s at one event per ms */
            ep_ac_event(&a, t + i);
            fired += ep_ac_tick(&a, t + i);
        }
        assert(fired >= 1 && fired <= 100 / 6 + 1);
    }
    ep_ac_init(&a, 1, 300, 10);
    assert(a.rearm_ms == 6000);
    ep_dyn_init(&d, 1, 515, 513, 2, 500, 0, 1);
    assert(d.restore_ms == EP_MIN_RESTORE_MS);
    /* global gate: starts at daemon start, explicit clears restart it */
    {
        struct ep_gate g;
        ep_gate_init(&g, 1000);
        assert(!ep_gate_auto_ok(&g, 1000) && !ep_gate_auto_ok(&g, 6999) && ep_gate_auto_ok(&g, 7000));
        ep_gate_note(&g, 7000);
        assert(!ep_gate_auto_ok(&g, 12999) && ep_gate_auto_ok(&g, 13000));
    }
    /* disabled */
    ep_ac_init(&a, 0, 300, 6000);
    ep_ac_event(&a, t);
    assert(ep_ac_timeout(&a, t) == -1 && ep_ac_tick(&a, t + 99999) == 0);
    puts("PASS eink-policy");
    return 0;
}
