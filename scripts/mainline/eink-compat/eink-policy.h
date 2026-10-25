/* Stock-like E Ink refresh policy for a9-eink-compat: pure logic, explicit clock.
 * No I/O, no libc beyond <stdint.h>; the daemon owns every sysfs write.
 *
 * 1. Dynamic refresh (ep_dyn): rest in the user's mode (stock only leaves Clear);
 *    after `need` vertical-scroll events inside `window_ms` switch to `fast`
 *    (Balanced by default, Smooth if configured); restore the rest mode
 *    `restore_ms` after the LAST scroll event. Events while fast only push the
 *    deadline (coalesced, no write). An explicit user mode change becomes the new
 *    rest mode and cancels a pending restore, so it is never overwritten.
 * 2. Activity-change clear (ep_ac): a burst of events coalesces into one clear
 *    `delay_ms` after the last (bounded by 4 x delay from the first); the clear
 *    is allowed only when the re-arm timer (6 s in stock) has expired, else the
 *    request is dropped (stock skips it, it does not queue it).
 *
 * Signals the daemon needs (socket lines): "sv" = one vertical scroll event,
 * "ac" = one activity/window change. docs/mainline-eink-quality.md section 5.
 */
#ifndef EINK_POLICY_H
#define EINK_POLICY_H
#include <stdint.h>

#define EP_MIN_RESTORE_MS 100
#define EP_MIN_REARM_MS 6000 /* stock re-arm; no configuration goes below it */
#define EP_MIN_DELAY_MS 100

enum ep_action { EP_NONE = 0, EP_SET_FAST = 1, EP_SET_REST = 2 };

struct ep_dyn {
    int enabled, only_clear, rest, fast, active;
    unsigned need, count;
    int64_t window_ms, restore_ms, window_start, deadline;
};

static inline void ep_dyn_init(struct ep_dyn *d, int enabled, int rest, int fast, unsigned need,
                               int64_t window_ms, int64_t restore_ms, int only_clear)
{
    /* hard floors: a zero restore would flip fast/rest on consecutive ticks */
    if (restore_ms < EP_MIN_RESTORE_MS) restore_ms = EP_MIN_RESTORE_MS;
    if (window_ms < 1) window_ms = 1;
    *d = (struct ep_dyn){.enabled = enabled, .only_clear = only_clear, .rest = rest, .fast = fast,
                         .need = need ? need : 1, .window_ms = window_ms, .restore_ms = restore_ms};
}

static inline enum ep_action ep_dyn_event(struct ep_dyn *d, int64_t now)
{
    if (!d->enabled) return EP_NONE;
    if (d->active) { d->deadline = now + d->restore_ms; return EP_NONE; }
    /* N events must all fall inside ONE window that starts at its first event
     * (not a gap between neighbours: a slow steady stream never qualifies). */
    if (!d->count || now - d->window_start > d->window_ms) { d->count = 0; d->window_start = now; }
    d->count++;
    if (d->count < d->need || d->fast == d->rest || (d->only_clear && d->rest != 515)) return EP_NONE;
    d->active = 1;
    d->deadline = now + d->restore_ms;
    return EP_SET_FAST;
}

static inline enum ep_action ep_dyn_tick(struct ep_dyn *d, int64_t now)
{
    if (!d->active || now < d->deadline) return EP_NONE;
    d->active = 0;
    d->count = 0;
    return EP_SET_REST;
}

/* An explicit mode selection: it is the new rest mode; nothing to restore. */
static inline void ep_dyn_user_mode(struct ep_dyn *d, int mode)
{
    d->rest = mode;
    d->active = 0;
    d->count = 0;
}

/* ms until the next tick needs to run, -1 = none */
static inline int64_t ep_dyn_timeout(const struct ep_dyn *d, int64_t now)
{
    if (!d->active) return -1;
    return d->deadline > now ? d->deadline - now : 0;
}

struct ep_ac {
    int enabled, pending;
    int64_t delay_ms, rearm_ms, first_at, fire_at, rearm_at;
    int ever;
};

static inline void ep_ac_init(struct ep_ac *a, int enabled, int64_t delay_ms, int64_t rearm_ms)
{
    if (delay_ms < EP_MIN_DELAY_MS) delay_ms = EP_MIN_DELAY_MS;
    if (rearm_ms < EP_MIN_REARM_MS) rearm_ms = EP_MIN_REARM_MS;	/* hard minimum */
    *a = (struct ep_ac){.enabled = enabled, .delay_ms = delay_ms, .rearm_ms = rearm_ms};
}

static inline void ep_ac_event(struct ep_ac *a, int64_t now)
{
    if (!a->enabled) return;
    if (a->ever && now < a->rearm_at) return;	/* cooldown: dropped on arrival, as stock */
    if (!a->pending) { a->pending = 1; a->first_at = now; }
    a->fire_at = now + a->delay_ms;
    if (a->fire_at > a->first_at + 4 * a->delay_ms) a->fire_at = a->first_at + 4 * a->delay_ms;
}

/* returns 1 = perform one clear now */
static inline int ep_ac_tick(struct ep_ac *a, int64_t now)
{
    if (!a->pending || now < a->fire_at) return 0;
    a->pending = 0;
    if (a->ever && now < a->rearm_at) return 0;
    a->ever = 1;
    a->rearm_at = now + a->rearm_ms;
    return 1;
}

static inline int64_t ep_ac_timeout(const struct ep_ac *a, int64_t now)
{
    if (!a->pending) return -1;
    return a->fire_at > now ? a->fire_at - now : 0;
}

/* Global automatic full-clear limiter, independent of any configuration:
 * every clear written by the daemon (explicit or automatic) restarts it, and it
 * starts at daemon start, so a restart cannot bypass it. Explicit clears are
 * never blocked, automatic ones need EP_MIN_REARM_MS since the last clear. */
struct ep_gate { int64_t last; };
static inline void ep_gate_init(struct ep_gate *g, int64_t now) { g->last = now; }
static inline void ep_gate_note(struct ep_gate *g, int64_t now) { g->last = now; }
static inline int ep_gate_auto_ok(const struct ep_gate *g, int64_t now)
{
    return now - g->last >= EP_MIN_REARM_MS;
}
#endif
