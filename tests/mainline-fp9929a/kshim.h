/*
 * kshim.h - just enough of the kernel API for drivers/regulator/fp9929a.c to
 * compile and run in userspace (tests/mainline-fp9929a). Every <linux/...>
 * include of the driver resolves to a one-line header that includes this.
 *
 * Model:
 *  - time is real (CLOCK_MONOTONIC); jiffies are milliseconds (HZ=1000);
 *  - mutexes are pthread mutexes, a delayed work is a thread with a timer,
 *    wait_var_event_timeout polls every 200 us;
 *  - GPIO writes are appended to a timestamped timeline (fake_*), I2C calls
 *    go through test hooks that can stall or fail them;
 *  - module_param_cb records the ops so a test can "write" the parameter;
 *    the panic notifier is stored so a test can "panic".
 */
#ifndef KSHIM_H
#define KSHIM_H

#define _GNU_SOURCE
#include <errno.h>
#include <limits.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

typedef uint8_t u8;
typedef int8_t s8;
#define BIT(n) (1UL << (n))
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
typedef uint32_t u32;
typedef uint64_t u64;
typedef int64_t s64;
typedef int32_t s32;
typedef unsigned short umode_t;

#define EPROBE_DEFER 517
#define INT_MAX_ INT_MAX

#define __maybe_unused __attribute__((unused))
#define READ_ONCE(x) (*(const volatile __typeof__(x) *)&(x))
#define WRITE_ONCE(x, v) do { *(volatile __typeof__(x) *)&(x) = (v); } while (0)
#ifndef container_of
#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))
#endif

#define IS_REACHABLE(x) (x)
#define CONFIG_REGULATOR_FP9929A 1
#define CONFIG_HWMON 0

/* ---------------- time ---------------- */
u64 fake_now_us(void);
#define HZ 1000
#define jiffies ((unsigned long)(fake_now_us() / 1000))
#define MAX_JIFFY_OFFSET ((LONG_MAX >> 1) - 1)
static inline unsigned long msecs_to_jiffies(unsigned int ms) { return ms; }
#define time_after(a, b) ((long)((b) - (a)) < 0)
#define time_after_eq(a, b) ((long)((a) - (b)) >= 0)
void fake_sleep_us(u64 us);
#define usleep_range(a, b) fake_sleep_us(a)
#define msleep(ms) fake_sleep_us((u64)(ms) * 1000)
#define mdelay(ms) fake_sleep_us((u64)(ms) * 1000)
#define udelay(us) fake_sleep_us(us)

#define NSEC_PER_USEC 1000
typedef s64 ktime_t;	/* ns */
static inline s64 ktime_to_ns(ktime_t t) { return t; }
static inline s64 ktime_to_us(ktime_t t) { return t / 1000; }
static inline ktime_t ktime_sub(ktime_t a, ktime_t b) { return a - b; }
static inline ktime_t ktime_get(void) { return (ktime_t)fake_now_us() * 1000; }
static inline ktime_t ktime_add_ms(ktime_t t, u64 ms) { return t + (ktime_t)ms * 1000000; }
static inline s64 ktime_us_delta(ktime_t a, ktime_t b) { return (a - b) / 1000; }
static inline s64 ktime_ms_delta(ktime_t a, ktime_t b) { return (a - b) / 1000000; }
static inline bool ktime_after(ktime_t a, ktime_t b) { return a > b; }

/* ---------------- printk ---------------- */
void fake_log(const char *lvl, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
struct device;
#define dev_info(d, ...) fake_log("info", __VA_ARGS__)
#define dev_notice(d, ...) fake_log("notice", __VA_ARGS__)
#define dev_warn(d, ...) fake_log("warn", __VA_ARGS__)
#define dev_err(d, ...) fake_log("err", __VA_ARGS__)
#define dev_crit(d, ...) fake_log("crit", __VA_ARGS__)
#define dev_err_ratelimited(d, ...) fake_log("err", __VA_ARGS__)
#define pr_err(...) fake_log("err", __VA_ARGS__)
#define pr_emerg(...) fake_log("emerg", __VA_ARGS__)
#define pr_crit(...) fake_log("crit", __VA_ARGS__)
#define pr_crit_ratelimited(...) fake_log("crit", __VA_ARGS__)
#define pr_info(...) fake_log("info", __VA_ARGS__)
#define pr_warn(...) fake_log("warn", __VA_ARGS__)
#define dev_err_probe(d, e, ...) ({ fake_log("err", __VA_ARGS__); (int)(e); })

/* ---------------- atomics ---------------- */
typedef struct { atomic_int v; } atomic_t;
#define ATOMIC_INIT(i) { (i) }
#define atomic_read(a) atomic_load(&(a)->v)
#define atomic_set(a, i) atomic_store(&(a)->v, (i))
#define atomic_inc(a) atomic_fetch_add(&(a)->v, 1)
#define atomic_dec(a) atomic_fetch_sub(&(a)->v, 1)
#define atomic_dec_and_test(a) (atomic_fetch_sub(&(a)->v, 1) == 1)
#define atomic_xchg(a, i) atomic_exchange(&(a)->v, (i))

/* ---------------- mutex ---------------- */
struct mutex { pthread_mutex_t m; };
#define DEFINE_MUTEX(n) struct mutex n = { PTHREAD_MUTEX_INITIALIZER }
static inline void mutex_init(struct mutex *l) { pthread_mutex_init(&l->m, NULL); }
static inline void mutex_lock(struct mutex *l) { pthread_mutex_lock(&l->m); }
static inline void mutex_unlock(struct mutex *l) { pthread_mutex_unlock(&l->m); }
static inline int mutex_trylock(struct mutex *l) { return pthread_mutex_trylock(&l->m) == 0; }

/* ---------------- wait_var ---------------- */
static inline void wake_up_var(void *v) { (void)v; }
#define wait_var_event_timeout(var, cond, timeout)			\
({									\
	long __left = (long)(timeout);					\
	u64 __end = fake_now_us() + (u64)__left * 1000;			\
	(void)(var);							\
	while (!(cond)) {						\
		u64 __n = fake_now_us();				\
		if (__n >= __end) { __left = 0; break; }		\
		fake_sleep_us(200);					\
	}								\
	if (__left) {							\
		u64 __n = fake_now_us();				\
		__left = __n >= __end ? 1 : (long)((__end - __n) / 1000) + 1; \
	}								\
	__left;								\
})

/* ---------------- workqueue ---------------- */
struct work_struct { void (*fn)(struct work_struct *); };
struct delayed_work {
	struct work_struct work;
	pthread_mutex_t m;
	pthread_cond_t c;
	pthread_t thr;
	bool started, pending, running, stop;
	u64 expires_us;
	int runs;
};
struct workqueue_struct;
#define system_wq ((struct workqueue_struct *)0)
#define system_highpri_wq ((struct workqueue_struct *)0)
#define to_delayed_work(w) container_of(w, struct delayed_work, work)
void fake_init_delayed_work(struct delayed_work *dw, void (*fn)(struct work_struct *));
bool mod_delayed_work(struct workqueue_struct *wq, struct delayed_work *dw, unsigned long delay);
/* false (nothing changed) if already pending, as in the kernel */
bool queue_delayed_work(struct workqueue_struct *wq, struct delayed_work *dw, unsigned long delay);
bool cancel_delayed_work(struct delayed_work *dw);
bool cancel_delayed_work_sync(struct delayed_work *dw);
void fake_stop_delayed_work(struct delayed_work *dw);

/* ---------------- device / of ---------------- */
struct device_node { const char *name; };
struct device {
	struct device_node *of_node;
	void *driver_data;
	void *driver;
};
extern struct device_node *of_chosen;
int of_property_read_string(struct device_node *np, const char *name, const char **out);
int of_property_read_u32(struct device_node *np, const char *name, u32 *out);
static inline void *dev_get_drvdata(struct device *d) { return d->driver_data; }
static inline void put_device(struct device *d) { (void)d; }
#define devm_kzalloc(d, sz, f) calloc(1, (sz))
#define GFP_KERNEL 0
static inline int devm_add_action_or_reset(struct device *d, void (*fn)(void *), void *data)
{ (void)d; (void)fn; (void)data; return 0; }
static inline int devm_delayed_work_autocancel(struct device *d, struct delayed_work *w,
					       void (*fn)(struct work_struct *))
{ (void)d; fake_init_delayed_work(w, fn); return 0; }
struct of_device_id { const char *compatible; };
#define MODULE_DEVICE_TABLE(a, b)
#define MODULE_DESCRIPTION(x)
#define MODULE_LICENSE(x)
#define MODULE_PARM_DESC(a, b)
#define EXPORT_SYMBOL_GPL(x)
#define THIS_MODULE ((void *)0)
#define IS_ERR(p) ((uintptr_t)(p) >= (uintptr_t)-4095)
#define PTR_ERR(p) ((long)(intptr_t)(p))

/* ---------------- kstrtobool ---------------- */
static inline int kstrtobool(const char *s, bool *res)
{
	if (!s)
		return -EINVAL;
	switch (s[0]) {
	case 'y': case 'Y': case '1': *res = true; return 0;
	case 'n': case 'N': case '0': *res = false; return 0;
	case 'o': case 'O':
		if (s[1] == 'n' || s[1] == 'N') { *res = true; return 0; }
		if (s[1] == 'f' || s[1] == 'F') { *res = false; return 0; }
	}
	return -EINVAL;
}

/* ---------------- module params ---------------- */
struct kernel_param;
struct kernel_param_ops {
	unsigned int flags;
	int (*set)(const char *val, const struct kernel_param *kp);
	int (*get)(char *buf, const struct kernel_param *kp);
};
struct kernel_param { const char *name; const struct kernel_param_ops *ops; void *arg; };
#define KERNEL_PARAM_OPS_FL_NOARG 1
static inline int param_get_bool(char *buf, const struct kernel_param *kp)
{ return sprintf(buf, "%c\n", *(bool *)kp->arg ? 'Y' : 'N'); }
static inline int param_get_uint(char *buf, const struct kernel_param *kp)
{ return sprintf(buf, "%u\n", *(unsigned int *)kp->arg); }
static inline int kstrtouint(const char *s, unsigned int base, unsigned int *res)
{
	char *e; unsigned long v = strtoul(s, &e, base);
	if (e == s || (*e && *e != '\n') || v > 0xffffffffUL) return -EINVAL;
	*res = (unsigned int)v; return 0;
}
#define module_param(name, type, perm)
#define module_param_cb(name, ops, arg, perm) \
	static const struct kernel_param __param_##name __attribute__((unused)) = { #name, ops, arg }

/* ---------------- notifier ---------------- */
struct notifier_block {
	int (*notifier_call)(struct notifier_block *, unsigned long, void *);
	int priority;
};
#define NOTIFY_DONE 0
extern int panic_notifier_list;
int atomic_notifier_chain_register(int *list, struct notifier_block *nb);
int atomic_notifier_chain_unregister(int *list, struct notifier_block *nb);

/* ---------------- gpio ---------------- */
struct gpio_desc { const char *name; int value; bool output; bool can_sleep; };
enum gpiod_flags { GPIOD_ASIS, GPIOD_IN, GPIOD_OUT_LOW };
struct gpio_desc *devm_gpiod_get(struct device *d, const char *con, enum gpiod_flags f);
struct gpio_desc *devm_gpiod_get_optional(struct device *d, const char *con, enum gpiod_flags f);
void gpiod_set_value_cansleep(struct gpio_desc *g, int v);
void gpiod_set_value(struct gpio_desc *g, int v);
int gpiod_direction_output(struct gpio_desc *g, int v);
int gpiod_get_value_cansleep(struct gpio_desc *g);
int gpiod_get_raw_value_cansleep(struct gpio_desc *g);
int gpiod_get_value(struct gpio_desc *g);
static inline int gpiod_cansleep(struct gpio_desc *g) { return g->can_sleep; }

/* ---------------- i2c ---------------- */
struct i2c_client { struct device dev; };
struct i2c_driver {
	struct { const char *name; const struct of_device_id *of_match_table; } driver;
	int (*probe)(struct i2c_client *);
	void (*remove)(struct i2c_client *);
	void (*shutdown)(struct i2c_client *);
};
#define module_i2c_driver(d) struct i2c_driver *fake_i2c_driver = &(d)
static inline void i2c_set_clientdata(struct i2c_client *c, void *p) { c->dev.driver_data = p; }
static inline void *i2c_get_clientdata(struct i2c_client *c) { return c->dev.driver_data; }
struct i2c_client *of_find_i2c_device_by_node(struct device_node *np);
s32 i2c_smbus_write_byte_data(struct i2c_client *c, u8 reg, u8 val);
s32 i2c_smbus_read_byte_data(struct i2c_client *c, u8 reg);

/* ---------------- regulator ---------------- */
struct regulator_dev;
struct regulator_ops {
	int (*enable)(struct regulator_dev *);
	int (*disable)(struct regulator_dev *);
	int (*is_enabled)(struct regulator_dev *);
};
enum regulator_type { REGULATOR_VOLTAGE };
struct regulator_desc {
	const char *name;
	enum regulator_type type;
	void *owner;
	const struct regulator_ops *ops;
	int n_voltages;
};
struct regulator_init_data { int dummy; };
struct regulator_config {
	struct device *dev;
	void *driver_data;
	struct device_node *of_node;
	struct regulator_init_data *init_data;
};
struct regulator_dev { const struct regulator_desc *desc; void *drvdata; };
static inline void *rdev_get_drvdata(struct regulator_dev *r) { return r->drvdata; }
static inline struct regulator_init_data *
of_get_regulator_init_data(struct device *d, struct device_node *n, const struct regulator_desc *desc)
{ (void)d; (void)n; (void)desc; return NULL; }
struct regulator_dev *devm_regulator_register(struct device *d, const struct regulator_desc *desc,
					      const struct regulator_config *cfg);

/* ---------------- hwmon ---------------- */
enum hwmon_sensor_types { hwmon_temp };
#define HWMON_T_INPUT 1
struct hwmon_channel_info { int dummy; };
#define HWMON_CHANNEL_INFO(t, ...) NULL
struct hwmon_ops {
	umode_t (*is_visible)(const void *, enum hwmon_sensor_types, u32, int);
	int (*read)(struct device *, enum hwmon_sensor_types, u32, int, long *);
};
struct hwmon_chip_info {
	const struct hwmon_ops *ops;
	const struct hwmon_channel_info *const *info;
};
static inline struct device *
devm_hwmon_device_register_with_info(struct device *d, const char *n, void *p,
				     const struct hwmon_chip_info *ci, void *g)
{ (void)n; (void)p; (void)ci; (void)g; return d; }

#endif
