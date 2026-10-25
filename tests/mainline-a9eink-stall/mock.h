/*
 * mock.h - just enough of the kernel and DRM API for the REAL
 * drivers/gpu/drm/a9_eink/a9_eink_transport.c and a9_eink_guard.c to
 * compile and run in userspace (tests/mainline-a9eink-stall). Every
 * <linux/...> and <drm/...> include of those files resolves to a one-line
 * header that includes this.
 *
 * Model:
 *  - time is real (CLOCK_MONOTONIC), in ns for ktime_t;
 *  - mutexes are pthread mutexes;
 *  - an hrtimer is a pthread that sleeps until its expiry and runs the
 *    callback with no lock held (like hardirq: it does not wait for the
 *    transport lock, which a blocked commit may hold);
 *  - drm_client_modeset_commit / _dpms call test hooks (test.c) that can
 *    take a frame period, block, or fail; the scanned-out buffer is visible
 *    to the hook through t->ms->fb;
 *  - fp9929a_emergency_off is a recorder (test.c).
 */
#ifndef A9_MOCK_H
#define A9_MOCK_H

#define _GNU_SOURCE
#define U32_MAX UINT32_MAX
#define min_t(t,a,b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define max_t(t,a,b) ((t)(a) > (t)(b) ? (t)(a) : (t)(b))
#include <errno.h>
#include <limits.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdatomic.h>
/* Repo driver also carries the input prewarm fields; unused by this harness. */
struct input_handler { int unused; };
typedef atomic_long atomic_long_t;
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int64_t s64;
typedef int32_t s32;

#define READ_ONCE(x) __atomic_load_n(&(x), __ATOMIC_RELAXED)
#define WRITE_ONCE(x, v) __atomic_store_n(&(x), (v), __ATOMIC_RELAXED)
#define smp_store_release(p, v) __atomic_store_n((p), (v), __ATOMIC_RELEASE)
#define smp_load_acquire(p) __atomic_load_n((p), __ATOMIC_ACQUIRE)
#define smp_wmb() atomic_thread_fence(memory_order_release)
#ifndef container_of
#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))
#endif
#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
static inline u64 div_u64(u64 a, u32 b) { return a / b; }
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define PTR_ERR(p) ((long)(intptr_t)(p))
#define THIS_MODULE ((void *)0)
#define MODULE_PARM_DESC(a, b)

/* ---------------- time ---------------- */
typedef s64 ktime_t;	/* ns */
u64 mock_now_ns(void);
static inline ktime_t ktime_get(void) { return (ktime_t)mock_now_ns(); }
static inline u64 ktime_get_ns(void) { return mock_now_ns(); }
static inline ktime_t ns_to_ktime(u64 ns) { return (ktime_t)ns; }
static inline s64 ktime_us_delta(ktime_t a, ktime_t b) { return (a - b) / 1000; }
static inline s64 ktime_to_us(ktime_t t) { return t / 1000; }
static inline ktime_t ktime_add_ms(ktime_t t, s64 ms) { return t + ms * 1000000; }
static inline s64 ktime_ms_delta(ktime_t a, ktime_t b) { return (a - b) / 1000000; }
void mock_sleep_us(u64 us);
#define usleep_range(a, b) mock_sleep_us(a)
#define msleep(ms) mock_sleep_us((u64)(ms) * 1000)

/* ---------------- printk ---------------- */
void mock_log(const char *lvl, const char *fmt, ...);
#define dev_info(d, ...) mock_log("info", __VA_ARGS__)
#define dev_warn_once(d, ...) mock_log("warn", __VA_ARGS__)
#define dev_warn(d, ...) mock_log("warn", __VA_ARGS__)
#define dev_warn_ratelimited(d, ...) mock_log("warn", __VA_ARGS__)
#define dev_err(d, ...) mock_log("err", __VA_ARGS__)
#define dev_crit(d, ...) mock_log("crit", __VA_ARGS__)
#define pr_info(...) mock_log("info", __VA_ARGS__)
#define pr_err(...) mock_log("err", __VA_ARGS__)

/* ---------------- atomics / locks ---------------- */
typedef struct { atomic_int v; } atomic_t;
#define ATOMIC_INIT(i) { (i) }
#define atomic_read(a) atomic_load(&(a)->v)
#define atomic_set(a, i) atomic_store(&(a)->v, (i))
#define atomic_xchg(a, i) atomic_exchange(&(a)->v, (i))

struct mutex { pthread_mutex_t m; };
#define DEFINE_MUTEX(n) struct mutex n = { PTHREAD_MUTEX_INITIALIZER }
static inline void mutex_init(struct mutex *l) { pthread_mutex_init(&l->m, NULL); }
static inline void mutex_lock(struct mutex *l) { pthread_mutex_lock(&l->m); }
static inline void mutex_unlock(struct mutex *l) { pthread_mutex_unlock(&l->m); }
/* single-threaded use only (the F6 lockdep stand-in): a lock this thread holds fails trylock */
static inline int mutex_is_locked(struct mutex *l) { if (pthread_mutex_trylock(&l->m)) return 1; pthread_mutex_unlock(&l->m); return 0; }

typedef struct { int dummy; } wait_queue_head_t;
static inline void wake_up_all(wait_queue_head_t *w) { (void)w; }
#define wake_up(w) wake_up_all(w)
static inline void init_waitqueue_head(wait_queue_head_t *w) { (void)w; }
/* waits poll their condition every 50 us (the ring's wakeups are hints) */
#define HZ 1000
static inline unsigned long mock_jiffies(void) { return (unsigned long)(mock_now_ns() / 1000000); }
#define jiffies mock_jiffies()
#define msecs_to_jiffies(ms) ((unsigned long)(ms))
#define time_after_eq(a, b) ((long)((a) - (b)) >= 0)
#define time_after(a, b) ((long)((b) - (a)) < 0)
#define wait_event_interruptible(wq, cond) wait_event(wq, cond)
#define wait_event(wq, cond) do { while (!(cond)) mock_sleep_us(50); } while (0)
typedef struct { pthread_mutex_t m; } spinlock_t;
static inline void spin_lock_init(spinlock_t *l) { pthread_mutex_init(&l->m, NULL); }
static inline void spin_lock(spinlock_t *l) { pthread_mutex_lock(&l->m); }
static inline void spin_unlock(spinlock_t *l) { pthread_mutex_unlock(&l->m); }
#define spin_lock_irqsave(l, f) do { (f) = 0; spin_lock(l); } while (0)
#define spin_unlock_irqrestore(l, f) do { (void)(f); spin_unlock(l); } while (0)
struct notifier_block { int (*notifier_call)(struct notifier_block *, unsigned long, void *); };
struct list_head { struct list_head *next, *prev; };
static inline void list_del(struct list_head *l) { (void)l; }

/* ---------------- hrtimer ---------------- */
enum hrtimer_restart { HRTIMER_NORESTART, HRTIMER_RESTART };
enum hrtimer_mode { HRTIMER_MODE_REL_HARD = 9 };
struct hrtimer {
	enum hrtimer_restart (*function)(struct hrtimer *);
	pthread_mutex_t m;
	pthread_cond_t c;
	pthread_t thr;
	bool armed, running, stop, started;
	u64 expires_ns;
	int fired;
};
void hrtimer_setup(struct hrtimer *h, enum hrtimer_restart (*fn)(struct hrtimer *),
		   clockid_t clk, enum hrtimer_mode mode);
void hrtimer_start(struct hrtimer *h, ktime_t rel, enum hrtimer_mode mode);
int hrtimer_try_to_cancel(struct hrtimer *h);
int hrtimer_cancel(struct hrtimer *h);
void mock_hrtimer_stop(struct hrtimer *h);

/* ---------------- device / of ---------------- */
struct device { const char *name; };
struct device_node { const char *name; };
struct device_link { int dummy; };
#define DL_FLAG_STATELESS 1
static inline const char *dev_name(const struct device *d) { return d->name; }
struct device_link *device_link_add(struct device *c, struct device *s, u32 f);
static inline void device_link_del(struct device_link *l) { (void)l; }

/* ---------------- iosys_map ---------------- */
struct iosys_map { void *vaddr; bool is_iomem; };
static inline void iosys_map_memset(struct iosys_map *m, size_t off, int v, size_t len)
{ memset((u8 *)m->vaddr + off, v, len); }
static inline void iosys_map_memcpy_to(struct iosys_map *m, size_t off, const void *src, size_t len)
{ memcpy((u8 *)m->vaddr + off, src, len); }

/* ---------------- drm ---------------- */
struct drm_driver { const char *name; };
struct drm_device {
	bool registered;
	const struct drm_driver *driver;
	struct device *dev;
	struct mutex clientlist_mutex;
	void *dev_private;
};
struct drm_display_mode { int hdisplay, vdisplay, htotal, vtotal, clock; };
struct drm_plane_state;
struct drm_crtc_state {
	bool active, enable;
	u32 plane_mask, connector_mask, encoder_mask;
	struct drm_display_mode mode, adjusted_mode;
};
struct drm_plane { struct drm_plane_state *state; unsigned int index; };
static inline u32 drm_plane_mask(const struct drm_plane *p) { return 1u << p->index; }
struct drm_crtc { struct drm_plane *primary; struct drm_crtc_state *state; };
#define DRM_MODE_ROTATE_0 1u
#define DRM_MODE_ROTATE_90 2u
struct drm_plane_state {
	struct drm_framebuffer *fb;
	struct drm_crtc *crtc;
	u32 src_x, src_y, src_w, src_h;
	int crtc_x, crtc_y;
	u32 crtc_w, crtc_h, rotation;
};
struct drm_encoder { int dummy; };
struct drm_connector { unsigned int index; u32 possible_encoders; };
static inline u32 drm_connector_mask(const struct drm_connector *c) { return 1u << c->index; }
static inline bool drm_mode_equal(const struct drm_display_mode *a, const struct drm_display_mode *b)
{ return !memcmp(a, b, sizeof(*a)); }
struct drm_framebuffer { unsigned int pitches[4]; void *mem; u32 width, height; };
struct drm_mode_set {
	struct drm_framebuffer *fb;
	struct drm_crtc *crtc;
	struct drm_display_mode *mode;
	int num_connectors;
	int x, y;
	struct drm_connector **connectors;
};
struct drm_client_dev;
struct drm_client_funcs {
	void *owner;
	void (*unregister)(struct drm_client_dev *);
	int (*suspend)(struct drm_client_dev *);
};
struct drm_client_dev {
	struct drm_device *dev;
	struct list_head list;
	struct mutex modeset_mutex;
	struct drm_mode_set *modesets;	/* terminated by crtc == NULL */
	const struct drm_client_funcs *funcs;
};
#define drm_client_for_each_modeset(ms, client) \
	for ((ms) = (client)->modesets; (ms)->crtc; (ms)++)
struct drm_client_buffer { struct drm_framebuffer *fb; };
struct drm_bridge { struct drm_device *dev; };
#define DRM_MODE_DPMS_OFF 3

struct drm_bridge *of_drm_find_and_get_bridge(struct device_node *np);
static inline void drm_bridge_put(struct drm_bridge *b) { (void)b; }
static inline void drm_dev_get(struct drm_device *d) { (void)d; }
static inline void drm_dev_put(struct drm_device *d) { (void)d; }
int drm_client_init(struct drm_device *d, struct drm_client_dev *c, const char *name,
		    const struct drm_client_funcs *f);
static inline void drm_client_release(struct drm_client_dev *c) { (void)c; }
static inline void drm_client_register(struct drm_client_dev *c) { (void)c; }
int drm_client_modeset_probe(struct drm_client_dev *c, unsigned int w, unsigned int h);
struct drm_client_buffer *drm_client_buffer_create_dumb(struct drm_client_dev *c, u32 w, u32 h, u32 f);
int drm_client_buffer_vmap(struct drm_client_buffer *b, struct iosys_map *m);
static inline void drm_client_buffer_vunmap(struct drm_client_buffer *b) { (void)b; }
void drm_client_buffer_delete(struct drm_client_buffer *b);
int drm_client_modeset_commit(struct drm_client_dev *c);
int drm_client_modeset_dpms(struct drm_client_dev *c, int mode);
static inline int drm_crtc_vblank_get(struct drm_crtc *c) { (void)c; return 0; }
static inline void drm_crtc_vblank_put(struct drm_crtc *c) { (void)c; }
u64 drm_crtc_vblank_count(struct drm_crtc *c);
u64 drm_crtc_vblank_count_and_time(struct drm_crtc *c, ktime_t *t);

/* ---------------- blocking FB-only atomic flip ---------------- */
struct drm_atomic_commit_profile;
struct drm_modeset_acquire_ctx { int dummy; };
struct drm_atomic_commit {
	struct drm_device *dev;
	struct drm_modeset_acquire_ctx *acquire_ctx;
	struct drm_atomic_commit_profile *profile;
	bool allow_modeset;
	struct drm_plane_state plane_state;
};
bool drm_master_internal_acquire(struct drm_device *dev);
void drm_master_internal_release(struct drm_device *dev);
void drm_modeset_acquire_init(struct drm_modeset_acquire_ctx *ctx, unsigned int flags);
void drm_modeset_acquire_fini(struct drm_modeset_acquire_ctx *ctx);
void drm_modeset_drop_locks(struct drm_modeset_acquire_ctx *ctx);
int drm_modeset_backoff(struct drm_modeset_acquire_ctx *ctx);
struct drm_atomic_commit *drm_atomic_commit_alloc(struct drm_device *dev);
void drm_atomic_commit_put(struct drm_atomic_commit *state);
void drm_atomic_commit_clear(struct drm_atomic_commit *state);
struct drm_crtc_state *drm_atomic_get_crtc_state(struct drm_atomic_commit *state, struct drm_crtc *crtc);
struct drm_plane_state *drm_atomic_get_plane_state(struct drm_atomic_commit *state, struct drm_plane *plane);
int drm_atomic_set_crtc_for_plane(struct drm_plane_state *state, struct drm_crtc *crtc);
void drm_atomic_set_fb_for_plane(struct drm_plane_state *state, struct drm_framebuffer *fb);
int drm_atomic_commit(struct drm_atomic_commit *state);

/* ---------------- fp9929a ---------------- */
void fp9929a_emergency_off(const char *why);
void fp9929a_starvation_off(const char *why);

/* ---------------- module params ---------------- */
struct kernel_param;
struct kernel_param_ops {
	unsigned int flags;
	int (*set)(const char *val, const struct kernel_param *kp);
	int (*get)(char *buf, const struct kernel_param *kp);
};
struct kernel_param { const char *name; const struct kernel_param_ops *ops; void *arg; };
#define module_param_cb(name, ops, arg, perm) \
	static const struct kernel_param __param_##name __attribute__((unused)) = { #name, ops, arg }
static inline int param_get_int(char *buf, const struct kernel_param *kp)
{ return sprintf(buf, "%d\n", *(int *)kp->arg); }
static inline int param_get_uint(char *buf, const struct kernel_param *kp)
{ return sprintf(buf, "%u\n", *(unsigned int *)kp->arg); }
static inline int kstrtouint(const char *s, unsigned int base, unsigned int *res)
{
	char *end;
	unsigned long value;

	if (!s || *s == '-')
		return -EINVAL;
	errno = 0;
	value = strtoul(s, &end, base);
	if (end == s || (*end && *end != '\n') || errno == ERANGE || value > UINT_MAX)
		return -EINVAL;
	*res = value;
	return 0;
}
static inline int kstrtoint(const char *s, unsigned int base, int *res)
{
	char *e; long v = strtol(s, &e, base);

	if (e == s || (*e && *e != '\n') || v < INT_MIN || v > INT_MAX)
		return -EINVAL;
	*res = (int)v;
	return 0;
}
static inline int kstrtobool(const char *s, bool *res)
{
	if (!s)
		return -EINVAL;
	switch (s[0]) {
	case 'y': case 'Y': case '1': *res = true; return 0;
	case 'n': case 'N': case '0': *res = false; return 0;
	}
	return -EINVAL;
}

/* ---------------- a9_eink.h leftovers ---------------- */
struct iio_channel;
struct regulator;
struct task_struct;
struct dentry;

#endif
