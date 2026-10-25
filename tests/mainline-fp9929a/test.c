/*
 * Fault tests for the REAL drivers/regulator/fp9929a.c (Hisense A9), run in
 * userspace against kshim.h. Each case asserts on the timestamped pin
 * timeline. Built and run by run.sh; the driver source is #included so its
 * static state (parameters, struct fp9929a) is reachable.
 */
#include "kshim.h"

#include "fp9929a.c"

/* =================== fake kernel runtime =================== */

static u64 t0_us;
/* Deterministic clock only in the main-thread shutdown cases. Workqueues stay
 * on real time and have no pending jobs while these direct calls run. */
static _Thread_local bool pd_clock;
static _Thread_local u64 pd_now_us;
static _Thread_local u64 pd_sleep[8];
static _Thread_local unsigned int pd_nsleeps;
static _Thread_local u64 pd_read_cost_us;
static _Thread_local unsigned int pd_fast_reads;
u64 fake_now_us(void)
{
	struct timespec ts;

	if (pd_clock)
		return pd_now_us;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (u64)ts.tv_sec * 1000000 + ts.tv_nsec / 1000 - t0_us + 1000000;
}

void fake_sleep_us(u64 us)
{
	struct timespec ts = { us / 1000000, (us % 1000000) * 1000 };

	if (pd_clock) {
		if (pd_nsleeps < 8)
			pd_sleep[pd_nsleeps] = us;
		pd_nsleeps++;
		pd_now_us += us;
		return;
	}
	while (nanosleep(&ts, &ts) && errno == EINTR)
		;
}

static int verbose;
static char logbuf[64][240];
static int nlog;
static pthread_mutex_t loglock = PTHREAD_MUTEX_INITIALIZER;

void fake_log(const char *lvl, const char *fmt, ...)
{
	char b[200];
	va_list ap;

	va_start(ap, fmt);
	vsnprintf(b, sizeof(b), fmt, ap);
	va_end(ap);
	pthread_mutex_lock(&loglock);
	if (nlog < 64)
		snprintf(logbuf[nlog++], 240, "%s: %s", lvl, b);
	pthread_mutex_unlock(&loglock);
	if (verbose)
		fprintf(stderr, "    [%8.3f ms] %s: %s", fake_now_us() / 1000.0, lvl, b);
}

static bool log_has(const char *s)
{
	bool r = false;

	pthread_mutex_lock(&loglock);
	for (int i = 0; i < nlog; i++)
		if (strstr(logbuf[i], s))
			r = true;
	pthread_mutex_unlock(&loglock);
	return r;
}

/* ---- timeline ---- */
enum ev_kind { EV_PIN, EV_I2C_W, EV_I2C_R, EV_MARK };
struct ev { u64 t; enum ev_kind k; const char *what; int v; };
static struct ev tl[512];
static int ntl;
static pthread_mutex_t tllock = PTHREAD_MUTEX_INITIALIZER;

static void tl_add(enum ev_kind k, const char *what, int v)
{
	pthread_mutex_lock(&tllock);
	if (ntl < 512)
		tl[ntl++] = (struct ev){ fake_now_us(), k, what, v };
	pthread_mutex_unlock(&tllock);
}

static void mark(const char *what) { tl_add(EV_MARK, what, 0); }

static void tl_dump(void)
{
	for (int i = 0; i < ntl; i++)
		fprintf(stderr, "      %9.3f ms  %-5s %-14s %d\n", tl[i].t / 1000.0,
			tl[i].k == EV_PIN ? "PIN" : tl[i].k == EV_I2C_W ? "I2C-W" :
			tl[i].k == EV_I2C_R ? "I2C-R" : "MARK", tl[i].what, tl[i].v);
}

/* ---- gpio ---- */
static struct gpio_desc g_pwrup = { "PWRUP" }, g_vcom = { "VCOM_EN" }, g_pg = { "PG" };
static atomic_int pg_mode;	/* 0: PG = PWRUP && VCOM_EN, 1: always 0, 2: read error */
static atomic_int pg_reads;	/* PG reads since VCOM_EN rose */
static atomic_int pg_delay2_ms;	/* the 2nd PG read is delayed (preemption) */
static atomic_int vcom_post_ms;	/* delay right after the VCOM_EN write (preemption) */
static void (*vcom_raise_hook)(void);	/* runs once just BEFORE VCOM_EN is raised */

struct gpio_desc *devm_gpiod_get(struct device *d, const char *con, enum gpiod_flags f)
{
	(void)d; (void)f;
	if (!strcmp(con, "pwrup"))
		return &g_pwrup;
	if (!strcmp(con, "vcom-en"))
		return &g_vcom;
	abort();
}

struct gpio_desc *devm_gpiod_get_optional(struct device *d, const char *con, enum gpiod_flags f)
{
	(void)d; (void)f;
	return !strcmp(con, "pg") ? &g_pg : NULL;
}

static void pin_write(struct gpio_desc *g, int v)
{
	if (g == &g_pg)
		abort();	/* PG is an input */
	if (g == &g_vcom && v && vcom_raise_hook) {
		void (*h)(void) = vcom_raise_hook;

		vcom_raise_hook = NULL;
		h();
	}
	if (g == &g_vcom && v)
		atomic_store(&pg_reads, 0);	/* count the PG wait's reads from VCOM_EN */
	pthread_mutex_lock(&tllock);
	WRITE_ONCE(g->value, v);
	if (ntl < 512)
		tl[ntl++] = (struct ev){ fake_now_us(), EV_PIN, g->name, v };
	pthread_mutex_unlock(&tllock);
	if (g == &g_vcom && v && atomic_load(&vcom_post_ms))
		fake_sleep_us((u64)atomic_load(&vcom_post_ms) * 1000);
}

void gpiod_set_value_cansleep(struct gpio_desc *g, int v) { pin_write(g, v); }
void gpiod_set_value(struct gpio_desc *g, int v) { pin_write(g, v); }
int gpiod_direction_output(struct gpio_desc *g, int v) { g->output = true; pin_write(g, v); return 0; }

int gpiod_get_value_cansleep(struct gpio_desc *g)
{
	if (g == &g_pg) {
		/* review 3: a late second good read must not pass the deadline */
		if (atomic_fetch_add(&pg_reads, 1) + 1 == 2 && atomic_load(&pg_delay2_ms))
			fake_sleep_us((u64)atomic_load(&pg_delay2_ms) * 1000);
		if (atomic_load(&pg_mode) == 2)
			return -EIO;	/* (s) [0039]: a GPIO read error */
		return atomic_load(&pg_mode) ? 0 :
		       (READ_ONCE(g_pwrup.value) && READ_ONCE(g_vcom.value));
	}
	return READ_ONCE(g->value);
}

int gpiod_get_raw_value_cansleep(struct gpio_desc *g) { return gpiod_get_value_cansleep(g); }

int gpiod_get_value(struct gpio_desc *g)
{
	if (g != &g_pg)
		return READ_ONCE(g->value);
	/* A separate fast API lets the tests detect an accidental cansleep read. */
	pd_fast_reads++;
	if (pd_clock)
		pd_now_us += pd_read_cost_us;
	if (atomic_load(&pg_mode) == 2)
		return -EIO;
	return atomic_load(&pg_mode) ? 0 :
	       (READ_ONCE(g_pwrup.value) && READ_ONCE(g_vcom.value));
}


/* "userspace TLMM fallback": write the output latch directly, stock order */
static void tlmm_fallback_off(void)
{
	mark("TLMM fallback");
	pin_write(&g_vcom, 0);
	fake_sleep_us(50000);
	pin_write(&g_pwrup, 0);
}

/* ---- of ---- */
struct device_node *of_chosen;
static struct device_node pmic_np = { "pmic" };
int of_property_read_string(struct device_node *np, const char *n, const char **o)
{ (void)np; (void)n; (void)o; return -EINVAL; }
int of_property_read_u32(struct device_node *np, const char *n, u32 *o)
{
	(void)np;
	if (!strcmp(n, "fitipower,vcom-millivolt")) {
		*o = 2730;
		return 0;
	}
	return -EINVAL;
}

/* ---- i2c ---- */
static struct i2c_client client = { .dev = { .of_node = &pmic_np } };
struct i2c_client *of_find_i2c_device_by_node(struct device_node *np) { (void)np; return &client; }

static atomic_int i2c_w_fail;		/* VCOM writes fail */
static atomic_int i2c_w_stall_ms;	/* first VCOM write stalls */
static void (*i2c_w_hook)(void);	/* runs inside the first VCOM write */
static void (*i2c_r_hook)(void);	/* runs inside the temperature read */
static int i2c_w_count;

/* FP9929A register model: 0 TMST (signed), 1 FUNC_ADJUST (bit 0 = I2C VCOM,
 * reset 1), 2 VCOM; fault knobs for the readback checks (patch 0025) */
static atomic_int reg_tmst, reg_func, reg_vcom, vcom_stuck, rd_fail_reg = -1;
s32 i2c_smbus_write_byte_data(struct i2c_client *c, u8 reg, u8 val)
{
	(void)c;
	tl_add(EV_I2C_W, reg == 2 ? "VCOM" : "?", val);
	if (reg == 2 && !atomic_load(&vcom_stuck) && !atomic_load(&i2c_w_fail))
		atomic_store(&reg_vcom, val);
	if (i2c_w_count++ == 0) {
		if (i2c_w_hook)
			i2c_w_hook();
		if (atomic_load(&i2c_w_stall_ms))
			fake_sleep_us((u64)atomic_load(&i2c_w_stall_ms) * 1000);
	}
	return atomic_load(&i2c_w_fail) ? -ENXIO : 0;
}

static void (*i2c_vr_hook)(void);	/* runs inside a VCOM (reg 2) read */
static int i2c_r_count;
s32 i2c_smbus_read_byte_data(struct i2c_client *c, u8 reg)
{
	(void)c;
	i2c_r_count++;
	if (reg == 2 && i2c_vr_hook)
		i2c_vr_hook();
	tl_add(EV_I2C_R, reg == 0 ? "TEMP" : reg == 1 ? "FUNC" : "VCOMRB", 0);
	if (atomic_load(&rd_fail_reg) == reg)
		return -ENXIO;
	if (reg == 1)
		return atomic_load(&reg_func);
	if (reg == 2)
		return atomic_load(&reg_vcom);
	if (i2c_r_hook)
		i2c_r_hook();
	return atomic_load(&reg_tmst);
}

/* ---- regulator ---- */
static struct regulator_dev rdev_s;
struct regulator_dev *devm_regulator_register(struct device *d, const struct regulator_desc *desc,
					      const struct regulator_config *cfg)
{
	(void)d;
	rdev_s.desc = desc;
	rdev_s.drvdata = cfg->driver_data;
	return &rdev_s;
}

/* ---- notifier ---- */
int panic_notifier_list;
static struct notifier_block *panic_nb_reg;
int atomic_notifier_chain_register(int *l, struct notifier_block *nb) { (void)l; panic_nb_reg = nb; return 0; }
int atomic_notifier_chain_unregister(int *l, struct notifier_block *nb) { (void)l; (void)nb; panic_nb_reg = NULL; return 0; }

static void do_panic(void)
{
	mark("PANIC");
	panic_nb_reg->notifier_call(panic_nb_reg, 0, NULL);
}

/* ---- delayed work ---- */
static void *dwork_thread(void *arg)
{
	struct delayed_work *dw = arg;

	pthread_mutex_lock(&dw->m);
	for (;;) {
		while (!dw->stop && !dw->pending)
			pthread_cond_wait(&dw->c, &dw->m);
		if (dw->stop)
			break;
		u64 now = fake_now_us();
		if (now < dw->expires_us) {
			pthread_mutex_unlock(&dw->m);
			fake_sleep_us(dw->expires_us - now < 500 ? dw->expires_us - now : 500);
			pthread_mutex_lock(&dw->m);
			continue;
		}
		dw->pending = false;
		dw->running = true;
		pthread_mutex_unlock(&dw->m);
		mark("max_on work runs");
		dw->work.fn(&dw->work);
		pthread_mutex_lock(&dw->m);
		dw->running = false;
		dw->runs++;
		pthread_cond_broadcast(&dw->c);
	}
	pthread_mutex_unlock(&dw->m);
	return NULL;
}

void fake_init_delayed_work(struct delayed_work *dw, void (*fn)(struct work_struct *))
{
	memset(dw, 0, sizeof(*dw));
	dw->work.fn = fn;
	pthread_mutex_init(&dw->m, NULL);
	pthread_cond_init(&dw->c, NULL);
	dw->started = true;
	pthread_create(&dw->thr, NULL, dwork_thread, dw);
}

bool mod_delayed_work(struct workqueue_struct *wq, struct delayed_work *dw, unsigned long delay)
{
	bool was;

	(void)wq;
	pthread_mutex_lock(&dw->m);
	was = dw->pending;
	dw->pending = true;
	dw->expires_us = fake_now_us() + (u64)delay * 1000;
	pthread_cond_broadcast(&dw->c);
	pthread_mutex_unlock(&dw->m);
	return was;
}

bool queue_delayed_work(struct workqueue_struct *wq, struct delayed_work *dw, unsigned long delay)
{
	bool was;

	(void)wq;
	pthread_mutex_lock(&dw->m);
	was = dw->pending;
	if (!was) {
		dw->pending = true;
		dw->expires_us = fake_now_us() + (u64)delay * 1000;
		pthread_cond_broadcast(&dw->c);
	}
	pthread_mutex_unlock(&dw->m);
	return !was;
}

bool cancel_delayed_work(struct delayed_work *dw)
{
	bool was;

	pthread_mutex_lock(&dw->m);
	was = dw->pending;
	dw->pending = false;
	pthread_mutex_unlock(&dw->m);
	return was;
}

bool cancel_delayed_work_sync(struct delayed_work *dw)
{
	bool was;

	pthread_mutex_lock(&dw->m);
	was = dw->pending;
	dw->pending = false;
	while (dw->running)
		pthread_cond_wait(&dw->c, &dw->m);
	pthread_mutex_unlock(&dw->m);
	return was;
}

void fake_stop_delayed_work(struct delayed_work *dw)
{
	pthread_mutex_lock(&dw->m);
	dw->stop = true;
	pthread_cond_broadcast(&dw->c);
	pthread_mutex_unlock(&dw->m);
	pthread_join(dw->thr, NULL);
}

/* =================== test helpers =================== */

static struct fp9929a *fp;
static int failures, cur_fail;
static const char *cur_name;

#define CHECK(cond, ...) do {						\
	if (!(cond)) {							\
		fprintf(stderr, "    FAIL %s:%d: %s: ", __FILE__, __LINE__, #cond); \
		fprintf(stderr, __VA_ARGS__);				\
		fputc('\n', stderr);					\
		cur_fail++;						\
	}								\
} while (0)

static int param_write(const struct kernel_param *kp, const char *v)
{
	return kp->ops->set(v, kp);
}

static void setup(const char *name)
{
	cur_name = name;
	cur_fail = 0;
	ntl = 0;
	nlog = 0;
	i2c_w_count = 0;
	atomic_store(&i2c_w_fail, 0);
	atomic_store(&i2c_w_stall_ms, 0);
	atomic_store(&pg_mode, 0);
	atomic_store(&pg_reads, 0);
	atomic_store(&pg_delay2_ms, 0);
	atomic_store(&vcom_post_ms, 0);
	atomic_store(&reg_tmst, 25);
	atomic_store(&reg_func, 1);
	atomic_store(&reg_vcom, 0x74);	/* reset value */
	atomic_store(&vcom_stuck, 0);
	atomic_store(&rd_fail_reg, -1);
	i2c_w_hook = NULL;
	i2c_r_hook = NULL;
	i2c_vr_hook = NULL;
	i2c_r_count = 0;
	vcom_raise_hook = NULL;
#ifdef HAVE_0026
	atomic_set(&fp9929a_emerg, 0);
	atomic_set(&fp9929a_emerg_busy, 0);
#endif
	max_on_ms = 20000;
	g_pwrup.value = 0;
	g_vcom.value = 0;
	g_pg.can_sleep = false;
	/* fresh probe each case; the bootloader left the rails down */
	if (param_write(&__param_allow_power_on, "1"))
		abort();
	client.dev.driver = (void *)1;
	if (fake_i2c_driver->probe(&client))
		abort();
	fp = i2c_get_clientdata(&client);
	ntl = 0;		/* drop probe's own pin writes */
	nlog = 0;
	fprintf(stderr, "  ---- %s\n", name);
}

static void teardown(void)
{
	fake_stop_delayed_work(&fp->max_on_work);
#ifdef HAVE_0026
	fake_stop_delayed_work(&fp->emerg_work);
#endif
	if (cur_fail) {
		tl_dump();
		for (int i = 0; i < nlog; i++)
			fprintf(stderr, "      log %s", logbuf[i]);
		fprintf(stderr, "  FAIL  %s\n", cur_name);
		failures++;
	} else {
		fprintf(stderr, "  PASS  %s\n", cur_name);
	}
	/* struct fp9929a is leaked on purpose: a stale thread may still look at it */
}

/* index of the first event matching (k, what, v) at or after index from, else -1 */
static int find(int from, enum ev_kind k, const char *what, int v)
{
	for (int i = from < 0 ? 0 : from; i < ntl; i++)
		if (tl[i].k == k && !strcmp(tl[i].what, what) && (k != EV_PIN || tl[i].v == v))
			return i;
	return -1;
}

static int find_after_t(u64 t, enum ev_kind k, const char *what, int v)
{
	for (int i = 0; i < ntl; i++)
		if (tl[i].t >= t && tl[i].k == k && !strcmp(tl[i].what, what) &&
		    (k != EV_PIN || tl[i].v == v))
			return i;
	return -1;
}

static double gap_ms(int a, int b) { return (double)(tl[b].t - tl[a].t) / 1000.0; }

/* every power-down in the timeline: each PWRUP 1->0 at least 50 ms after the
 * last VCOM_EN 1->0 or VCOM_EN never high since the previous PWRUP rise */
static void check_down_order(void)
{
	int up = 0, com = 0;
	u64 com_fall = 0;
	bool com_since_up = false;

	for (int i = 0; i < ntl; i++) {
		if (tl[i].k != EV_PIN)
			continue;
		if (!strcmp(tl[i].what, "VCOM_EN")) {
			if (tl[i].v)
				CHECK(up, "VCOM_EN raised while PWRUP low (event %d)", i);
			if (com && !tl[i].v)
				com_fall = tl[i].t;
			if (tl[i].v)
				com_since_up = true;
			com = tl[i].v;
		} else {
			if (up && !tl[i].v) {
				CHECK(!com, "PWRUP dropped with VCOM_EN high (event %d)", i);
				if (com_since_up)
					CHECK(tl[i].t - com_fall >= 50000,
					      "PWRUP fell %.3f ms after VCOM_EN (< 50)",
					      (tl[i].t - com_fall) / 1000.0);
			}
			if (!up && tl[i].v)
				com_since_up = false;
			up = tl[i].v;
		}
	}
}

static void check_pins_low(void)
{
	CHECK(!g_pwrup.value && !g_vcom.value, "pins not low at the end: PWRUP=%d VCOM_EN=%d",
	      g_pwrup.value, g_vcom.value);
	CHECK(!READ_ONCE(fp->energized) && !READ_ONCE(fp->on), "state not off: energized=%d on=%d",
	      fp->energized, fp->on);
}

/* ---- threads ---- */
struct enable_res { int ret; u64 t_ret; };
static void *enable_thread(void *arg)
{
	struct enable_res *r = arg;

	r->ret = rdev_s.desc->ops->enable(&rdev_s);
	r->t_ret = fake_now_us();
	mark("enable returned");
	return NULL;
}

struct write_res { const char *val; int ret; u64 t_begin, t_ret; bool fallback_first; };
static void *allow_write_thread(void *arg)
{
	struct write_res *w = arg;

	if (w->fallback_first)
		tlmm_fallback_off();
	w->t_begin = fake_now_us();
	mark("allow=0 write begins");
	w->ret = param_write(&__param_allow_power_on, w->val);
	w->t_ret = fake_now_us();
	mark("allow=0 write returned");
	return NULL;
}

static void wait_pin(struct gpio_desc *g, int v, int timeout_ms)
{
	u64 end = fake_now_us() + (u64)timeout_ms * 1000;

	while (READ_ONCE(g->value) != v && fake_now_us() < end)
		fake_sleep_us(100);
}

/* =================== cases =================== */

/* (a) normal enable/disable: order and delays */
static void case_a(void)
{
	int ret, pu, vw, tr, ve, vd, pd;

	setup("(a) normal enable/disable order and delays");
	ret = rdev_s.desc->ops->enable(&rdev_s);
	CHECK(ret == 0, "enable returned %d", ret);
	CHECK(rdev_s.desc->ops->is_enabled(&rdev_s) == 1, "not enabled");
	pu = find(0, EV_PIN, "PWRUP", 1);
	vw = find(pu, EV_I2C_W, "VCOM", 0);
	tr = find(vw, EV_I2C_R, "TEMP", 0);
	ve = find(tr, EV_PIN, "VCOM_EN", 1);
	CHECK(pu >= 0 && vw > pu && tr > vw && ve > tr, "order PWRUP, VCOM write, TEMP, VCOM_EN: %d %d %d %d",
	      pu, vw, tr, ve);
	if (pu >= 0 && vw > pu && tr > vw && ve > tr) {
		CHECK(gap_ms(pu, vw) >= 5.0, "PWRUP->VCOM write %.3f ms", gap_ms(pu, vw));
		CHECK(gap_ms(tr, ve) >= 20.0, "TEMP->VCOM_EN %.3f ms", gap_ms(tr, ve));
		CHECK(tl[vw].v == fp9929a_vcom_code(2730), "VCOM code 0x%02x", tl[vw].v);
	}
	CHECK(find(0, EV_PIN, "VCOM_EN", 1) == ve && find(ve + 1, EV_PIN, "PWRUP", 0) < 0,
	      "extra pin activity during power-up");
	CHECK(fp->energized && fp->on, "state after enable: energized=%d on=%d", fp->energized, fp->on);

	/* closing the gate with the rails fully on returns at once; they stay on */
	ret = param_write(&__param_allow_power_on, "0");
	CHECK(ret == 0, "allow=0 while on returned %d", ret);
	CHECK(g_pwrup.value && g_vcom.value, "closing the gate changed the pins");
	param_write(&__param_allow_power_on, "1");

	ret = rdev_s.desc->ops->disable(&rdev_s);
	CHECK(ret == 0, "disable returned %d", ret);
	vd = find(ve, EV_PIN, "VCOM_EN", 0);
	pd = find(vd, EV_PIN, "PWRUP", 0);
	CHECK(vd > 0 && pd > vd, "down order VCOM_EN then PWRUP: %d %d", vd, pd);
	if (vd > 0 && pd > vd)
		CHECK(gap_ms(vd, pd) >= 50.0, "VCOM_EN->PWRUP %.3f ms", gap_ms(vd, pd));
	check_pins_low();
	check_down_order();
	/* the cancelled max_on work never runs */
	fake_sleep_us(20000);
	CHECK(fp->max_on_work.runs == 0, "max_on work ran %d times", fp->max_on_work.runs);
	teardown();
}

/* (b) panic right after PWRUP rises (from inside the VCOM write) */
static void case_b(void)
{
	int ret, p, i;

	setup("(b) panic right after PWRUP: pins low, VCOM_EN never rises");
	i2c_w_hook = do_panic;
	ret = rdev_s.desc->ops->enable(&rdev_s);
	mark("enable returned");
	p = find(0, EV_MARK, "PANIC", 0);
	CHECK(p > find(0, EV_PIN, "PWRUP", 1), "panic not after PWRUP rise");
	i = find(p, EV_PIN, "VCOM_EN", 0);
	CHECK(i > p, "panic did not drop VCOM_EN");
	CHECK(find(i, EV_PIN, "PWRUP", 0) > i, "panic did not drop PWRUP after VCOM_EN");
	CHECK(find(0, EV_PIN, "VCOM_EN", 1) < 0, "VCOM_EN rose");
	CHECK(log_has("panic: E Ink rails powered down"), "no panic log line");
	/* the harness lets the enabling thread continue (on a real panic it is
	 * stopped): it must not complete the power-up */
	CHECK(ret != 0, "enable succeeded after the panic (%d)", ret);
	check_pins_low();
	check_down_order();
	teardown();

	setup("(b2) panic with the rails off: pins driven low, no log line");
	do_panic();
	CHECK(find(0, EV_PIN, "VCOM_EN", 0) >= 0 && find(0, EV_PIN, "PWRUP", 0) >= 0,
	      "panic did not drive the pins");
	CHECK(!log_has("panic:"), "logged although off");
	check_pins_low();
	teardown();

	setup("(b3) panic with the rails fully on");
	ret = rdev_s.desc->ops->enable(&rdev_s);
	CHECK(ret == 0, "enable %d", ret);
	do_panic();
	CHECK(log_has("panic: E Ink rails powered down"), "no panic log line");
	check_pins_low();
	check_down_order();
	teardown();
}

/* (c) enable stalled in I2C; TLMM fallback + allow=0 from another thread */
static void case_c(int stall_ms, bool expect_busy)
{
	pthread_t te, tw;
	struct enable_res er = { 0 };
	struct write_res wr = { "0", 0, 0, 0, true };
	int i;

	setup(expect_busy ?
	      "(c2) stall 2.6 s > quiescence limit: write -EBUSY, gate stays closed" :
	      "(c) enable stalled in I2C, fallback + allow=0: write waits, no VCOM_EN");
	atomic_store(&i2c_w_stall_ms, stall_ms);
	pthread_create(&te, NULL, enable_thread, &er);
	wait_pin(&g_pwrup, 1, 1000);
	fake_sleep_us(20000);	/* inside the stalled VCOM write */
	pthread_create(&tw, NULL, allow_write_thread, &wr);
	pthread_join(tw, NULL);
	if (expect_busy) {
		CHECK(wr.ret == -EBUSY, "write returned %d, want -EBUSY", wr.ret);
		CHECK(!READ_ONCE(allow_power_on), "gate reopened");
		CHECK(log_has("still in flight"), "no EBUSY log");
	}
	pthread_join(te, NULL);
	if (!expect_busy) {
		CHECK(wr.ret == 0, "write returned %d", wr.ret);
		CHECK(wr.t_ret >= er.t_ret, "write returned %.3f ms BEFORE the enable finished",
		      (er.t_ret - wr.t_ret) / 1000.0);
	}
	CHECK(er.ret == -EPERM, "enable returned %d, want -EPERM", er.ret);
	i = find_after_t(wr.t_begin, EV_PIN, "VCOM_EN", 1);
	CHECK(i < 0, "VCOM_EN rose after the write began");
	i = find(find(0, EV_MARK, "TLMM fallback", 0), EV_PIN, "PWRUP", 1);
	CHECK(i < 0, "PWRUP re-raised after the fallback");
	CHECK(find(0, EV_PIN, "VCOM_EN", 1) < 0, "VCOM_EN ever rose");
	CHECK(log_has("power-up aborted before VCOM-EN: allow_power_on closed"), "no abort log");
	check_pins_low();
	check_down_order();
	/* gate closed: a new enable is refused without touching the pins */
	ntl = 0;
	CHECK(rdev_s.desc->ops->enable(&rdev_s) == -EPERM, "enable with the gate closed");
	CHECK(find(0, EV_PIN, "PWRUP", 1) < 0, "PWRUP raised with the gate closed");
	teardown();
}

/* (d) allow=0 between PWRUP and VCOM_EN, no stall */
static pthread_t d_tw;
static struct write_res d_wr = { "0" };
static void d_hook(void) { pthread_create(&d_tw, NULL, allow_write_thread, &d_wr); fake_sleep_us(1000); }

static void case_d(void)
{
	struct enable_res er = { 0 };
	pthread_t te;

	setup("(d) allow=0 between PWRUP and VCOM_EN (no stall)");
	d_wr = (struct write_res){ "0" };
	i2c_r_hook = d_hook;	/* in the temperature read: before the 20 ms wait */
	pthread_create(&te, NULL, enable_thread, &er);
	pthread_join(te, NULL);
	pthread_join(d_tw, NULL);
	CHECK(er.ret == -EPERM, "enable returned %d", er.ret);
	CHECK(d_wr.ret == 0, "write returned %d", d_wr.ret);
	CHECK(d_wr.t_ret >= er.t_ret, "write returned before the enable");
	CHECK(find(0, EV_PIN, "VCOM_EN", 1) < 0, "VCOM_EN rose");
	CHECK(find(0, EV_PIN, "PWRUP", 0) > find(0, EV_PIN, "PWRUP", 1), "PWRUP not dropped");
	check_pins_low();
	check_down_order();
	teardown();
}

/* (e) max_on fires during a stalled startup */
static void case_e(void)
{
	struct enable_res er = { 0 };
	pthread_t te;
	int pu, pd, ret;

	setup("(e) max_on fires during a stalled startup: power-down, fault latched");
	max_on_ms = 300;
	atomic_store(&i2c_w_stall_ms, 1000);
	pthread_create(&te, NULL, enable_thread, &er);
	pthread_join(te, NULL);
	pu = find(0, EV_PIN, "PWRUP", 1);
	pd = find(pu, EV_PIN, "PWRUP", 0);
	CHECK(pd > pu, "PWRUP never dropped");
	if (pd > pu) {
		CHECK(gap_ms(pu, pd) >= 300.0 && gap_ms(pu, pd) < 600.0,
		      "PWRUP dropped %.3f ms after rising (want ~300+50, during the 1000 ms stall)",
		      gap_ms(pu, pd));
		CHECK(tl[pd].t < er.t_ret, "drop not before the enable returned");
	}
	CHECK(er.ret == -EIO, "enable returned %d, want -EIO", er.ret);
	CHECK(find(0, EV_PIN, "VCOM_EN", 1) < 0, "VCOM_EN rose");
	CHECK(log_has("max_on_ms 300 expired with the lock held"), "no work log");
	CHECK(fp->forced_off, "fault not latched");
	check_pins_low();
	check_down_order();
	/* latched: refused without pin activity until cleared */
	ntl = 0;
	atomic_store(&i2c_w_stall_ms, 0);
	CHECK(rdev_s.desc->ops->enable(&rdev_s) == -EIO, "enable with fault latched");
	CHECK(find(0, EV_PIN, "PWRUP", 1) < 0, "PWRUP raised with fault latched");
	/* cleared: a normal power-up works and is not hit by a stale work run */
	__param_clear_fault.ops->set("1", &__param_clear_fault);
	max_on_ms = 20000;
	ret = rdev_s.desc->ops->enable(&rdev_s);
	CHECK(ret == 0, "enable after clear_fault %d", ret);
	fake_sleep_us(100000);
	CHECK(g_pwrup.value && g_vcom.value, "rails dropped after clear (stale work?)");
	rdev_s.desc->ops->disable(&rdev_s);
	check_pins_low();
	check_down_order();
	teardown();

	setup("(e2) max_on fires with the rails fully on");
	max_on_ms = 200;
	ret = rdev_s.desc->ops->enable(&rdev_s);
	CHECK(ret == 0, "enable %d", ret);
	pu = find(0, EV_PIN, "PWRUP", 1);
	fake_sleep_us(400000);
	pd = find(pu, EV_PIN, "PWRUP", 0);
	CHECK(pd > pu, "no forced power-down");
	if (pd > pu)
		CHECK(gap_ms(pu, pd) >= 200.0 && gap_ms(pu, pd) < 350.0,
		      "forced down %.3f ms after PWRUP", gap_ms(pu, pd));
	CHECK(log_has("max_on_ms 200 expired: E Ink rails forced down"), "no log");
	CHECK(fp->forced_off, "fault not latched");
	check_pins_low();
	check_down_order();
	rdev_s.desc->ops->disable(&rdev_s);
	CHECK(!fp->forced_off, "disable did not clear the fault");
	teardown();
}

/* (e3) a stale max_on run (from an earlier power-up) must not touch this one */
static void *stale_work_thread(void *arg)
{
	(void)arg;
	fp9929a_max_on_work(&fp->max_on_work.work);
	return NULL;
}

static void case_e3(void)
{
	pthread_t tw;
	int ret, n;

	setup("(e3) stale max_on run, lock free and lock held: rails stay on");
	ret = rdev_s.desc->ops->enable(&rdev_s);
	CHECK(ret == 0, "enable %d", ret);
	n = ntl;
	fp9929a_max_on_work(&fp->max_on_work.work);	/* lock free */
	mutex_lock(&fp->lock);				/* e.g. a status read */
	pthread_create(&tw, NULL, stale_work_thread, NULL);
	fake_sleep_us(100000);
	mutex_unlock(&fp->lock);
	pthread_join(tw, NULL);
	CHECK(ntl == n, "pin activity from a stale run");
	CHECK(g_pwrup.value && g_vcom.value && fp->on && !fp->forced_off, "rails dropped / fault");
	rdev_s.desc->ops->disable(&rdev_s);
	check_pins_low();
	teardown();
}

/* (f) VCOM write failure */
static void case_f(void)
{
	int ret;

	setup("(f) VCOM write fails: error, energized cleared, pins low");
	max_on_ms = 150;
	atomic_store(&i2c_w_fail, 1);
	ret = rdev_s.desc->ops->enable(&rdev_s);
	CHECK(ret == -ENXIO, "enable returned %d", ret);
	CHECK(log_has("VCOM write failed"), "no log");
	CHECK(find(0, EV_PIN, "VCOM_EN", 1) < 0, "VCOM_EN rose");
	CHECK(find(0, EV_PIN, "PWRUP", 0) > find(0, EV_PIN, "PWRUP", 1), "PWRUP not dropped");
	check_pins_low();
	check_down_order();
	/* the max_on work armed at energize time was cancelled */
	fake_sleep_us(300000);
	CHECK(fp->max_on_work.runs == 0, "max_on work ran after the failed power-up");
	CHECK(!fp->forced_off, "fault latched by a failed power-up");
	teardown();
}

#ifdef HAVE_PG_WAIT
/* (g) PG never good: -ETIMEDOUT, pins low */
static void case_g(void)
{
	int ret;

	setup("(g) [0022] PG never good: -ETIMEDOUT, rails down");
	atomic_store(&pg_mode, 1);
	ret = rdev_s.desc->ops->enable(&rdev_s);
	CHECK(ret == -ETIMEDOUT, "enable returned %d", ret);
	CHECK(log_has("PG not asserted"), "no log");
	check_pins_low();
	check_down_order();
	teardown();
}

/* (h) allow=0 during the PG wait */
static void case_h(void)
{
	struct enable_res er = { 0 };
	struct write_res wr = { "0" };
	pthread_t te, tw;

	setup("(h) [0022] allow=0 during the PG wait: power-down, -EPERM, write waits");
	atomic_store(&pg_mode, 1);	/* PG stays low: enable sits in the wait */
	pthread_create(&te, NULL, enable_thread, &er);
	wait_pin(&g_vcom, 1, 1000);
	fake_sleep_us(10000);
	pthread_create(&tw, NULL, allow_write_thread, &wr);
	fake_sleep_us(5000);
	atomic_store(&pg_mode, 0);	/* PG now good: the gate must still win */
	pthread_join(te, NULL);
	pthread_join(tw, NULL);
	CHECK(er.ret == -EPERM, "enable returned %d", er.ret);
	CHECK(wr.ret == 0 && wr.t_ret >= er.t_ret, "write %d returned before the enable", wr.ret);
	CHECK(find_after_t(wr.t_begin, EV_PIN, "VCOM_EN", 1) < 0, "VCOM_EN rose after the write");
	check_pins_low();
	check_down_order();
	teardown();
}

/* (i) review 3: the second good PG read comes back after pg_timeout_ms
 * (descheduled): -ETIMEDOUT, normal power-down, never marked on */
static void case_i(void)
{
	int ret;

	setup("(i) [0022] 2nd good PG read 150 ms late: -ETIMEDOUT, rails down, not on");
	atomic_store(&pg_delay2_ms, 150);
	ret = rdev_s.desc->ops->enable(&rdev_s);
	CHECK(ret == -ETIMEDOUT, "enable returned %d", ret);
	CHECK(!log_has("PG 1 after"), "an 'on:' line was logged");
	CHECK(log_has("PG not asserted"), "no timeout log");
	CHECK(!fp->on && !fp->energized, "on %d energized %d", fp->on, fp->energized);
	check_pins_low();
	check_down_order();
	teardown();
}

/* (j) a late but in-time second read still succeeds */
static void case_j(void)
{
	int ret;

	setup("(j) [0022] 2nd good PG read 30 ms late: success within the deadline");
	atomic_store(&pg_delay2_ms, 30);
	ret = rdev_s.desc->ops->enable(&rdev_s);
	CHECK(ret == 0, "enable returned %d", ret);
	CHECK(fp->on && log_has("PG 1 after"), "not on");
	rdev_s.desc->ops->disable(&rdev_s);
	check_pins_low();
	check_down_order();
	teardown();
}
#endif

#ifdef HAVE_MAX_ON_PARAM
static int mo_write(const char *v) { return __param_max_on_ms.ops->set(v, &__param_max_on_ms); }

/* (k) review 4: max_on_ms is a validated per-session cap */
static void case_k(void)
{
	struct enable_res er = { 0 };
	pthread_t te;
	int pu, pd;

	setup("(k) [0024] max_on_ms: 0 and >20000 refused, refused while energized, next power-up only");
	CHECK(mo_write("0") == -EINVAL, "0 accepted");
	CHECK(mo_write("20001") == -EINVAL, "20001 accepted");
	CHECK(mo_write("20000") == 0 && max_on_ms == 20000, "20000 refused");
	CHECK(mo_write("300") == 0 && max_on_ms == 300, "300 refused");
	/* raising it during a (stalled) power-up must fail; the armed 300 holds */
	atomic_store(&i2c_w_stall_ms, 1000);
	pthread_create(&te, NULL, enable_thread, &er);
	wait_pin(&g_pwrup, 1, 1000);
	CHECK(mo_write("20000") == -EBUSY, "changed while energized");
	CHECK(max_on_ms == 300, "value changed while energized: %u", max_on_ms);
	pthread_join(te, NULL);
	pu = find(0, EV_PIN, "PWRUP", 1);
	pd = find(pu, EV_PIN, "PWRUP", 0);
	CHECK(pd > pu && gap_ms(pu, pd) < 600.0, "300 ms cap not enforced (%.1f ms)", pd > pu ? gap_ms(pu, pd) : -1.0);
	check_pins_low();
	check_down_order();
	/* off again: the write is accepted and applies to the next power-up */
	__param_clear_fault.ops->set("1", &__param_clear_fault);
	atomic_store(&i2c_w_stall_ms, 0);
	CHECK(mo_write("200") == 0, "write refused with the rails off");
	ntl = 0;
	CHECK(rdev_s.desc->ops->enable(&rdev_s) == 0, "enable");
	fake_sleep_us(400000);
	pu = find(0, EV_PIN, "PWRUP", 1);
	pd = find(pu, EV_PIN, "PWRUP", 0);
	CHECK(pd > pu && gap_ms(pu, pd) >= 200.0 && gap_ms(pu, pd) < 350.0,
	      "next power-up not capped at 200 ms (%.1f ms)", pd > pu ? gap_ms(pu, pd) : -1.0);
	rdev_s.desc->ops->disable(&rdev_s);
	check_pins_low();
	teardown();
}

/* (l) review 4: preemption right after the VCOM_EN write must not give a
 * fresh PG window (the origin is taken before the write) */
static void case_l(void)
{
	int ret;

	setup("(l) [0024] 150 ms stall right after the VCOM_EN write: -ETIMEDOUT, not on");
	atomic_store(&vcom_post_ms, 150);
	ret = rdev_s.desc->ops->enable(&rdev_s);
	CHECK(ret == -ETIMEDOUT, "enable returned %d", ret);
	CHECK(!log_has("PG 1 after"), "an 'on:' line was logged");
	CHECK(!fp->on && !fp->energized, "on %d energized %d", fp->on, fp->energized);
	check_pins_low();
	check_down_order();
	teardown();
}

/* (m) review 4: the cap drops the pins while another thread holds the
 * regulator lock (a status reader, a stuck consumer path) */
static void case_m(void)
{
	int pu, pd;

	setup("(m) [0024] cap fires while the regulator lock is held elsewhere: pins dropped at the cap");
	CHECK(mo_write("300") == 0, "write");
	CHECK(rdev_s.desc->ops->enable(&rdev_s) == 0, "enable");
	mutex_lock(&fp->lock);		/* held past the cap */
	fake_sleep_us(600000);
	pu = find(0, EV_PIN, "PWRUP", 1);
	pd = find(pu, EV_PIN, "PWRUP", 0);
	CHECK(pd > pu && gap_ms(pu, pd) >= 300.0 && gap_ms(pu, pd) < 450.0,
	      "pins not dropped at the cap with the lock held (%.1f ms)", pd > pu ? gap_ms(pu, pd) : -1.0);
	CHECK(!g_vcom.value && !g_pwrup.value, "pins high while the lock is held");
#ifdef HAVE_PDTRACE
	CHECK(fp->pdtrace.seq == 0, "cap lock-bypass fabricated a normal shutdown trace");
#endif
	mutex_unlock(&fp->lock);
	fake_sleep_us(100000);
	check_pins_low();
	check_down_order();
	rdev_s.desc->ops->disable(&rdev_s);
	teardown();
}
#endif

#ifdef HAVE_0025
/* (n) TMST is a signed byte */
static void case_n(void)
{
	long v = 0;

	setup("(n) [0025] TMST 0xf6 / 0xff read as -10 / -1 C (signed)");
	atomic_store(&reg_tmst, 0xf6);
	CHECK(rdev_s.desc->ops->enable(&rdev_s) == 0, "enable");
	CHECK(fp->have_temp && fp->temp_c == -10, "temp %d", fp->temp_c);
	CHECK(log_has("temp -10 C"), "on line without -10");
	rdev_s.desc->ops->disable(&rdev_s);
	atomic_store(&reg_tmst, 0xff);
	CHECK(rdev_s.desc->ops->enable(&rdev_s) == 0, "enable 2");
	CHECK(fp->temp_c == -1, "temp %d", fp->temp_c);
	(void)v;
	rdev_s.desc->ops->disable(&rdev_s);
	check_pins_low();
	teardown();
}

static int mf_write(const char *v) { return __param_min_off_ms.ops->set(v, &__param_min_off_ms); }

#ifdef MINOFF_500	/* candidate 0049b: datasheet-minimum default */
#define MO_DEF 500
#define MO_LOW 500
#else
#define MO_DEF 1000
#define MO_LOW 500
#endif
#define STR_(x) #x
#define STR(x) STR_(x)

/* (o) minimum off time from the last completed power-down */
static void case_o(void)
{
	struct enable_res er = { 0 };
	struct write_res wr = { "0" };
	pthread_t te, tw;
	int d, pu;

	setup("(o) [0025] min_off_ms: 0/<low/>10000 refused; re-energize waits >= the default; gate close ends the wait");
	CHECK(mf_write("0") == -EINVAL, "0 accepted");
	{ char below[16]; snprintf(below, sizeof(below), "%d", MO_LOW - 1);
	  CHECK(mf_write(below) == -EINVAL && mf_write("10001") == -EINVAL, "out-of-range value accepted"); }
	CHECK(mf_write(STR(MO_DEF)) == 0, "default refused");
	CHECK(rdev_s.desc->ops->enable(&rdev_s) == 0, "enable");
	rdev_s.desc->ops->disable(&rdev_s);
	d = find(0, EV_PIN, "PWRUP", 0);
	CHECK(rdev_s.desc->ops->enable(&rdev_s) == 0, "re-enable");
	pu = find(d, EV_PIN, "PWRUP", 1);
	CHECK(pu > d && gap_ms(d, pu) >= (double)MO_DEF && gap_ms(d, pu) < MO_DEF + 200.0,
	      "PWRUP rose %.1f ms after the last power-down (want >= MO_DEF)", pu > d ? gap_ms(d, pu) : -1.0);
	CHECK(log_has("power-up waits"), "no wait log");
	rdev_s.desc->ops->disable(&rdev_s);
	/* gate closed during the wait: no PWRUP at all, the write waits for it */
	ntl = 0;
	pthread_create(&te, NULL, enable_thread, &er);
	fake_sleep_us(MO_DEF * 200)	/* a fifth of the wait */;
	pthread_create(&tw, NULL, allow_write_thread, &wr);
	pthread_join(te, NULL);
	pthread_join(tw, NULL);
	CHECK(er.ret == -EPERM, "enable during the wait returned %d", er.ret);
	CHECK(find(0, EV_PIN, "PWRUP", 1) < 0, "PWRUP rose");
	CHECK(wr.ret == 0, "allow=0 write %d", wr.ret);
	check_pins_low();
	teardown();
}

#ifdef MINOFF_500
/* (o2) candidate 0049b: the shorter off time never skips the PG check, the
 * ordered shutdown, or the fault path; a re-energize after it still waits for PG */
static void case_o2(void)
{
	int d, ret, pu, vc;

	setup("(o2) [0049b] min_off 500 ms: re-energize waits >= 500 ms (not 1000), then still needs PG (else -ETIMEDOUT, ordered down)");
	CHECK(rdev_s.desc->ops->enable(&rdev_s) == 0, "enable");
	rdev_s.desc->ops->disable(&rdev_s);
	check_down_order();
	d = find(0, EV_PIN, "PWRUP", 0);
	atomic_store(&pg_mode, 1);	/* PG never good on the second power-up */
	ret = rdev_s.desc->ops->enable(&rdev_s);
	pu = find(d, EV_PIN, "PWRUP", 1);
	vc = find(d, EV_PIN, "VCOM_EN", 1);
	CHECK(pu > d && gap_ms(d, pu) >= 500.0 && gap_ms(d, pu) < 700.0, "PWRUP %.1f ms after power-down", pu > d ? gap_ms(d, pu) : -1.0);
	CHECK(vc > pu, "VCOM_EN did not follow PWRUP");
	CHECK(ret == -ETIMEDOUT, "enable returned %d with PG low", ret);
	CHECK(log_has("PG not asserted"), "no PG log");
	check_pins_low();
	check_down_order();
	/* the floor stays at the datasheet minimum */
	CHECK(mf_write("499") == -EINVAL && mf_write("0") == -EINVAL, "below 500 accepted");
	teardown();
}
#endif

/* (p) VCOM readback and register 1 bit 0 */
static void case_p(void)
{
	int ret;

	setup("(p) [0025] VCOM readback mismatch -> -EIO, ordered down, no VCOM_EN");
	atomic_store(&vcom_stuck, 1);	/* the write is acked but not taken */
	ret = rdev_s.desc->ops->enable(&rdev_s);
	CHECK(ret == -EIO, "enable %d", ret);
	CHECK(log_has("VCOM readback"), "no log");
	CHECK(find(0, EV_PIN, "VCOM_EN", 1) < 0, "VCOM_EN rose");
	CHECK(!fp->on && !fp->energized, "state");
	check_pins_low();
	check_down_order();
	teardown();

	setup("(p2) [0025] register 1 bit 0 = 0 (VCOM not from I2C) -> -EIO, never written");
	atomic_store(&reg_func, 0);
	ret = rdev_s.desc->ops->enable(&rdev_s);
	CHECK(ret == -EIO, "enable %d", ret);
	CHECK(log_has("register 1 = 0"), "no log");
	CHECK(find(0, EV_PIN, "VCOM_EN", 1) < 0, "VCOM_EN rose");
	for (int k = 0; k < ntl; k++)
		CHECK(!(tl[k].k == EV_I2C_W && strcmp(tl[k].what, "VCOM")), "a register other than VCOM was written");
	check_pins_low();
	check_down_order();
	teardown();

	setup("(p3) [0025] readback I2C error (5 tries) -> -EIO, ordered down");
	atomic_store(&rd_fail_reg, 2);
	ret = rdev_s.desc->ops->enable(&rdev_s);
	CHECK(ret == -EIO, "enable %d", ret);
	CHECK(find(0, EV_PIN, "VCOM_EN", 1) < 0, "VCOM_EN rose");
	check_pins_low();
	check_down_order();
	teardown();
}
#endif

#ifdef HAVE_0026
static int cf_write(const char *v) { return __param_clear_fault.ops->set(v, &__param_clear_fault); }

static void emerg(const char *why) { mark("EMERGENCY"); fp9929a_emergency_off(why); }

static void *lock_holder(void *arg)
{
	int ms = *(int *)arg;

	mutex_lock(&fp->lock);
	mark("lock taken");
	fake_sleep_us((u64)ms * 1000);
	mutex_unlock(&fp->lock);
	return NULL;
}

/* after an emergency drop: VCOM_EN fell within 2 ms of the call, PWRUP fell
 * >= 50 ms after the LAST VCOM_EN fall (and < 160 ms after the call) */
static void check_emerg_timeline(void)
{
	int e = find(0, EV_MARK, "EMERGENCY", 0), vd, pd, last_vd = -1;

	CHECK(e >= 0, "no EMERGENCY mark");
	if (e < 0)
		return;
	vd = find(e, EV_PIN, "VCOM_EN", 0);
	CHECK(vd > e && gap_ms(e, vd) < 2.0, "VCOM_EN not dropped at once (%.3f ms)",
	      vd > e ? gap_ms(e, vd) : -1.0);
	pd = find(e, EV_PIN, "PWRUP", 0);
	CHECK(pd > e, "PWRUP never dropped");
	if (pd <= e)
		return;
	/* real 1->0 transitions only (a write of 0 to a low pin is no fall) */
	for (int i = 0, lvl = 0; i < pd; i++)
		if (tl[i].k == EV_PIN && !strcmp(tl[i].what, "VCOM_EN")) {
			if (lvl && !tl[i].v)
				last_vd = i;
			lvl = tl[i].v;
		}
	CHECK(last_vd < 0 || gap_ms(last_vd, pd) >= 50.0, "PWRUP %.3f ms after the last VCOM_EN fall (< 50)",
	      last_vd >= 0 ? gap_ms(last_vd, pd) : -1.0);
	CHECK(gap_ms(e, pd) < 160.0, "PWRUP %.3f ms after the call", gap_ms(e, pd));
}

/* (q) rails fully on, another thread holds fp->lock (a stuck consumer) */
static void case_q(void)
{
	struct fp9929a_status st = { 0 };
	pthread_t th;
	int hold = 400, ret;

	setup("(q) [0026] emergency_off, rails on, fp->lock held elsewhere: VCOM_EN now, PWRUP >= 50 ms, -EIO until clear_fault");
	CHECK(rdev_s.desc->ops->enable(&rdev_s) == 0, "enable");
	pthread_create(&th, NULL, lock_holder, &hold);
	fake_sleep_us(20000);
	emerg("test: rails on, lock held");
	CHECK(!g_vcom.value, "VCOM_EN still high right after the call");
	CHECK(g_pwrup.value, "PWRUP dropped together with VCOM_EN (want the 50 ms order)");
	CHECK(!READ_ONCE(allow_power_on), "gate still open");
	CHECK(cf_write("1") == -EBUSY, "clear_fault accepted while the drop is pending");
	fake_sleep_us(150000);		/* lock still held (400 ms) */
	CHECK(!g_vcom.value && !g_pwrup.value, "pins not low 150 ms later with the lock held");
	check_emerg_timeline();
#ifdef HAVE_PDTRACE
	CHECK(fp->pdtrace.seq == 0, "emergency lock-bypass fabricated a normal shutdown trace");
#endif
	CHECK(log_has("emergency off (test: rails on, lock held)"), "no log line");
	pthread_join(th, NULL);
	check_down_order();
	/* latched: -EIO, even with the gate reopened, with no pin activity */
	CHECK(fp9929a_get_status(&pmic_np, &st) == 0 && st.emergency && !st.on, "status: emergency %d on %d",
	      st.emergency, st.on);
	ntl = 0;
	CHECK(rdev_s.desc->ops->enable(&rdev_s) == -EIO, "enable with the gate closed, latched");
	param_write(&__param_allow_power_on, "1");
	CHECK(rdev_s.desc->ops->enable(&rdev_s) == -EIO, "enable with the gate open, latched");
	CHECK(find(0, EV_PIN, "PWRUP", 1) < 0, "PWRUP raised while latched");
	/* a normal disable does not clear it */
	rdev_s.desc->ops->disable(&rdev_s);
	CHECK(rdev_s.desc->ops->enable(&rdev_s) == -EIO, "disable cleared the latch");
	CHECK(cf_write("1") == 0, "clear_fault refused after the drop");
	CHECK(fp9929a_get_status(&pmic_np, &st) == 0 && !st.emergency, "status still emergency");
	ret = rdev_s.desc->ops->enable(&rdev_s);
	CHECK(ret == 0, "enable after clear_fault %d", ret);
	fake_sleep_us(100000);
	CHECK(g_pwrup.value && g_vcom.value, "rails dropped after the clear (stale work?)");
	rdev_s.desc->ops->disable(&rdev_s);
	check_pins_low();
	check_down_order();
	teardown();
}

/* (r) emergency during a power-up stalled in the VCOM I2C write */
static void case_r(void)
{
	struct enable_res er = { 0 };
	pthread_t te;

	setup("(r) [0026] emergency_off during a stalled enable (I2C): -EIO, VCOM_EN never rises, pins low");
	atomic_store(&i2c_w_stall_ms, 300);
	pthread_create(&te, NULL, enable_thread, &er);
	wait_pin(&g_pwrup, 1, 1000);
	fake_sleep_us(20000);
	emerg("test: stalled enable");
	/* 0027: PWRUP falls 50 ms (queue) + 50 ms after the work's own VCOM_EN write */
	fake_sleep_us(150000);
	CHECK(!g_pwrup.value && !g_vcom.value, "pins high 150 ms after the call, enable still stalled");
	param_write(&__param_allow_power_on, "1");	/* reopening must not help */
	pthread_join(te, NULL);
	CHECK(er.ret == -EIO, "enable returned %d, want -EIO", er.ret);
	CHECK(find(0, EV_PIN, "VCOM_EN", 1) < 0, "VCOM_EN rose");
	check_emerg_timeline();
	check_pins_low();
	check_down_order();
	teardown();

	setup("(r2) [0026] emergency_off during the PG wait (VCOM_EN up): -EIO, ordered drop, pins low");
	atomic_store(&pg_mode, 1);
	pthread_create(&te, NULL, enable_thread, &er);
	wait_pin(&g_vcom, 1, 1000);
	fake_sleep_us(10000);
	emerg("test: PG wait");
	atomic_store(&pg_mode, 0);	/* PG good now: the latch must still win */
	pthread_join(te, NULL);
	fake_sleep_us(100000);
	CHECK(er.ret == -EIO, "enable returned %d, want -EIO", er.ret);
	CHECK(!fp->on, "marked on");
	check_emerg_timeline();
	check_pins_low();
	check_down_order();
	teardown();
}

/* (r3) the race the work covers: emergency between the power-up's gate check
 * and its VCOM_EN write (it raises VCOM_EN after the first drop) */
static void race_hook(void) { emerg("test: race before VCOM_EN"); }

/* (r4) review 8's combined case: a first emergency after the power-up's gate
 * check, the authorized VCOM_EN assertion delayed 40 ms, then a SECOND
 * emergency 4 ms after that assertion (it used to overwrite the epoch the
 * work compared against, so PWRUP fell ~9 ms after the last VCOM_EN fall) */
static void *second_emerg_thread(void *arg)
{
	(void)arg;
	for (int i = 0; i < 2000 && !READ_ONCE(g_vcom.value); i++)
		fake_sleep_us(100);
	fake_sleep_us(4000);
	emerg("test: second emergency after the racing assertion");
	return NULL;
}
static pthread_t r4_t;
static void race_hook4(void)
{
	emerg("test: first emergency after the gate check");
	pthread_create(&r4_t, NULL, second_emerg_thread, NULL);
	fake_sleep_us(40000);	/* the already-authorized assertion is delayed */
}

static void case_r3(void)
{
	struct enable_res er = { 0 };
	pthread_t te;
	int up;

	setup("(r3) [0026] emergency just before the power-up raises VCOM_EN: work drops it again, PWRUP >= 50 ms after");
	atomic_store(&pg_mode, 1);
	vcom_raise_hook = race_hook;
	pthread_create(&te, NULL, enable_thread, &er);
	pthread_join(te, NULL);
	fake_sleep_us(200000);
	up = find(0, EV_PIN, "VCOM_EN", 1);
	CHECK(up > find(0, EV_MARK, "EMERGENCY", 0), "VCOM_EN did not rise after the call (race not exercised)");
	CHECK(er.ret == -EIO, "enable returned %d, want -EIO", er.ret);
	check_emerg_timeline();
	check_pins_low();
	check_down_order();
	teardown();
}
#endif

#ifdef HAVE_0026
static void case_r4(void)
{
	struct enable_res er = { 0 };
	pthread_t te;

	setup("(r4) [0027] repeated emergency around a delayed VCOM_EN assertion: PWRUP >= 50 ms after the last VCOM_EN fall");
	vcom_raise_hook = race_hook4;
	pthread_create(&te, NULL, enable_thread, &er);
	pthread_join(te, NULL);
	pthread_join(r4_t, NULL);
	fake_sleep_us(250000);
	CHECK(find(0, EV_PIN, "VCOM_EN", 1) > find(0, EV_MARK, "EMERGENCY", 0), "race not exercised");
	CHECK(er.ret == -EIO, "enable returned %d, want -EIO", er.ret);
	check_emerg_timeline();
	check_pins_low();
	check_down_order();
	teardown();
}
#endif

#ifdef HAVE_0039
/* (s) patch 0039: the status reports the ONE raw PG read pg_good came from,
 * and the energized state, so a9_eink can say which conjunct failed */
static void case_s(void)
{
	struct fp9929a_status st;
	int n;

	setup("(s) [0039] status: pg_raw is the single read behind pg_good (1, 0, -errno); energized reported");
	memset(&st, 0x5a, sizeof(st));
	CHECK(fp9929a_get_status(&pmic_np, &st) == 0 && !st.on && !st.energized &&
	      st.pg_valid && st.pg_raw == 0 && !st.pg_good,
	      "off: on %d energized %d pg_raw %d pg_good %d", st.on, st.energized, st.pg_raw, st.pg_good);
	CHECK(rdev_s.desc->ops->enable(&rdev_s) == 0, "enable");
	n = atomic_load(&pg_reads);
	CHECK(fp9929a_get_status(&pmic_np, &st) == 0 && st.on && st.energized &&
	      st.pg_raw == 1 && st.pg_good, "on: on %d energized %d pg_raw %d pg_good %d",
	      st.on, st.energized, st.pg_raw, st.pg_good);
	CHECK(atomic_load(&pg_reads) - n == 1, "%d PG reads for one status", atomic_load(&pg_reads) - n);
	atomic_store(&pg_mode, 1);	/* PG drops; the software state stays on */
	CHECK(fp9929a_get_status(&pmic_np, &st) == 0 && st.on && st.pg_raw == 0 && !st.pg_good,
	      "PG low: on %d pg_raw %d pg_good %d", st.on, st.pg_raw, st.pg_good);
	atomic_store(&pg_mode, 2);	/* GPIO read error: reported, never good */
	CHECK(fp9929a_get_status(&pmic_np, &st) == 0 && st.on && st.pg_raw == -EIO && !st.pg_good,
	      "PG error: pg_raw %d pg_good %d", st.pg_raw, st.pg_good);
	atomic_store(&pg_mode, 0);
	rdev_s.desc->ops->disable(&rdev_s);
	CHECK(fp9929a_get_status(&pmic_np, &st) == 0 && !st.on && !st.energized,
	      "after disable: on %d energized %d", st.on, st.energized);
	check_pins_low();
	teardown();
}
#endif


#ifdef HAVE_PDTRACE
/* PG observation cost is subtracted from the existing 1/50 ms deadlines.
 * This deterministic pin timeline distinguishes compensation from simply
 * adding three reads around the original relative sleeps. */
static void case_pdtrace(bool sleeping, bool absent, int mode)
{
	struct fp9929a_status st;
	const u64 start = 10000000;
	u64 prior_seq;
	int vd, pd, reads, old_i2c, old_logs, old_ntl;
	unsigned int fast;

	setup(absent ? "(pd4) absent PG records ENOENT without IO" :
	      sleeping ? "(pd3) sleeping PG controller is never sampled during shutdown" :
	      mode == 2 ? "(pd2) GPIO errno preserved at all three shutdown positions" :
	      "(pd1) PG timeline + absolute dwell compensation + status timestamp");
	g_pg.can_sleep = sleeping;
	fp->pg = absent ? NULL : &g_pg;
	atomic_store(&pg_mode, mode);
	/* Model fully-on pins without arming any real workqueue timer. */
	g_pwrup.value = g_vcom.value = 1;
	fp->on = fp->energized = true;
	old_i2c = i2c_w_count;
	old_logs = nlog;
	old_ntl = ntl;
	reads = atomic_load(&pg_reads);
	prior_seq = fp->pdtrace.seq;
	pd_now_us = start;
	pd_read_cost_us = 200;
	pd_fast_reads = pd_nsleeps = 0;
	pd_clock = true;
	mutex_lock(&fp->lock);
	fp9929a_power_down(fp);
	mutex_unlock(&fp->lock);
	fast = sleeping || absent ? 0 : 3;
	vd = find(0, EV_PIN, "VCOM_EN", 0);
	pd = find(0, EV_PIN, "PWRUP", 0);
	CHECK(vd >= 0 && pd > vd, "missing/incorrect pin order");
	if (vd >= 0 && pd > vd) {
		CHECK(tl[vd].t == start + 1000, "VCOM low at %llu (wanted +1000)",
		      (unsigned long long)(tl[vd].t - start));
		CHECK(tl[pd].t - tl[vd].t == 50000, "VCOM->PWRUP %llu (wanted 50000)",
		      (unsigned long long)(tl[pd].t - tl[vd].t));
	}
	CHECK(pd_nsleeps == 2 && pd_sleep[0] == (fast ? 800 : 1000) &&
	      pd_sleep[1] == (fast ? 49800 : 50000), "wrong residual waits %u/%llu/%llu",
	      pd_nsleeps, (unsigned long long)pd_sleep[0], (unsigned long long)pd_sleep[1]);
	CHECK(pd_fast_reads == fast && atomic_load(&pg_reads) == reads,
	      "shutdown API reads: fast %u sleeping %d", pd_fast_reads, atomic_load(&pg_reads) - reads);
	CHECK(fp->pdtrace.seq == prior_seq + 1, "seq did not advance exactly once");
	CHECK(fp->pdtrace.entry.at_us == (s64)(start + (fast ? 200 : 0)) &&
	      fp->pdtrace.vcom_low.at_us == (s64)(start + 1000 + (fast ? 200 : 0)) &&
	      fp->pdtrace.pwrup_low.at_us == (s64)(start + 51000 + (fast ? 200 : 0)),
	      "sample timestamps do not match their physical order");
	int expected = absent ? -ENOENT : sleeping ? -EOPNOTSUPP : mode == 2 ? -EIO : 1;
	CHECK(fp->pdtrace.entry.pg_raw == expected &&
	      fp->pdtrace.vcom_low.pg_raw == (expected == 1 ? 0 : expected) &&
	      fp->pdtrace.pwrup_low.pg_raw == (expected == 1 ? 0 : expected),
	      "raw PG points %d/%d/%d", fp->pdtrace.entry.pg_raw,
	      fp->pdtrace.vcom_low.pg_raw, fp->pdtrace.pwrup_low.pg_raw);
	CHECK(ktime_to_us(fp->off_at) == (s64)(start + 51000),
	      "off timestamp slipped past the final PG observation");
	CHECK(!fp->on && !fp->energized && i2c_w_count == old_i2c && nlog == old_logs,
	      "diagnostics changed power state, performed I2C or logged");
	for (int i = old_ntl; i < ntl; i++)
		CHECK(tl[i].k != EV_I2C_R && tl[i].k != EV_I2C_W, "I2C during shutdown trace");
	int reads_before = atomic_load(&pg_reads);
	CHECK(fp9929a_get_status(&pmic_np, &st) == 0 && st.pdtrace.seq == fp->pdtrace.seq &&
	      !memcmp(&st.pdtrace, &fp->pdtrace, sizeof(st.pdtrace)), "status lost trace");
	CHECK(st.pg_at_us == (s64)pd_now_us && st.pg_good == (st.pg_raw == 1),
	      "status timestamp/raw mismatch");
	CHECK(atomic_load(&pg_reads) - reads_before == (absent ? 0 : 1),
	      "status duplicated its original PG read");
	/* A fractional-microsecond residual must be rounded UP. */
	pd_nsleeps = 0;
	fp9929a_wait_until(ktime_get() + 1501);
	CHECK(pd_nsleeps == 1 && pd_sleep[0] == 2, "fractional residual rounded down");
	pd_clock = false;
	teardown();
}
#endif

#ifdef HAVE_READ_VCOM
/* (v) review 89d F6: fp9929a_read_vcom() is read-only: one VCOM read under fp->lock, no write, no gate lock,
 * -EAGAIN while the rails are off */
static int vr_lock_held, vr_gate_free, vr_calls;
static void vr_check_locks(void)
{
	vr_calls++;
	/* fp->lock is a plain (non-recursive) mutex held by this thread: trylock must fail */
	vr_lock_held = !mutex_trylock(&fp->lock);
	if (!vr_lock_held)
		mutex_unlock(&fp->lock);
	vr_gate_free = mutex_trylock(&fp9929a_gate_lock);
	if (vr_gate_free)
		mutex_unlock(&fp9929a_gate_lock);
}

static void case_v(void)
{
	u8 now = 0x5a, prog = 0x5a;
	int w0, r0, nt;

	setup("(v) [F6] fp9929a_read_vcom: rails off -EAGAIN, no I2C at all; on: one read of reg 2 under fp->lock, 0 writes");
	r0 = i2c_r_count;
	w0 = i2c_w_count;
	CHECK(fp9929a_read_vcom(&pmic_np, &now, &prog) == -EAGAIN && now == 0x5a && prog == 0x5a, "off: not -EAGAIN");
	CHECK(i2c_r_count == r0 && i2c_w_count == w0, "off: I2C used (r %d w %d)", i2c_r_count - r0, i2c_w_count - w0);
	CHECK(rdev_s.desc->ops->enable(&rdev_s) == 0, "enable");
	CHECK(atomic_load(&reg_vcom) == fp->vcom_code, "power-up did not program VCOM");
	r0 = i2c_r_count;
	w0 = i2c_w_count;
	nt = ntl;
	i2c_vr_hook = vr_check_locks;
	CHECK(fp9929a_read_vcom(&pmic_np, &now, &prog) == 0, "read failed");
	CHECK(now == fp->vcom_code && prog == fp->vcom_code, "now 0x%02x prog 0x%02x code 0x%02x", now, prog, fp->vcom_code);
	CHECK(i2c_r_count - r0 == 1 && vr_calls == 1, "%d reads (%d of reg 2), want exactly one", i2c_r_count - r0, vr_calls);
	CHECK(i2c_w_count == w0, "%d I2C writes during the readback", i2c_w_count - w0);
	for (int i = nt; i < ntl; i++)
		CHECK(tl[i].k != EV_I2C_W && tl[i].k != EV_PIN, "timeline event %d during the readback", tl[i].k);
	CHECK(vr_lock_held, "fp->lock not held during the I2C read");
	CHECK(vr_gate_free, "fp9929a_gate_lock taken during the readback");
	/* a chip that lost its code: reported as read, programmed code unchanged, still no write */
	atomic_store(&reg_vcom, 0x74);
	w0 = i2c_w_count;
	CHECK(fp9929a_read_vcom(&pmic_np, &now, &prog) == 0 && now == 0x74 && prog == fp->vcom_code && i2c_w_count == w0,
	      "mismatch: now 0x%02x prog 0x%02x writes %d", now, prog, i2c_w_count - w0);
	/* a failed read: the errno, no retry, outputs untouched */
	atomic_store(&rd_fail_reg, 2);
	now = prog = 0x11;
	r0 = i2c_r_count;
	CHECK(fp9929a_read_vcom(&pmic_np, &now, &prog) == -ENXIO && now == 0x11 && prog == 0x11 && i2c_r_count - r0 == 1,
	      "read error: retried or wrote outputs (%d reads)", i2c_r_count - r0);
	atomic_store(&rd_fail_reg, -1);
	i2c_vr_hook = NULL;
	rdev_s.desc->ops->disable(&rdev_s);
	CHECK(fp9929a_read_vcom(&pmic_np, &now, &prog) == -EAGAIN, "after disable: not -EAGAIN");
	check_pins_low();
	teardown();
}
#endif

int main(int argc, char **argv)
{
	struct timespec ts;

	verbose = argc > 1 && !strcmp(argv[1], "-v");
	clock_gettime(CLOCK_MONOTONIC, &ts);
	t0_us = (u64)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;

	case_a();
	case_b();
	case_c(300, false);
	case_c(2600, true);
	case_d();
	case_e();
	case_e3();
	case_f();
#ifdef HAVE_PG_WAIT
	case_g();
	case_h();
	case_i();
	case_j();
#endif
#ifdef HAVE_MAX_ON_PARAM
	case_k();
	case_l();
	case_m();
#endif
#ifdef HAVE_0025
	case_n();
	case_o();
#ifdef MINOFF_500
	case_o2();
#endif
	case_p();
#endif
#ifdef HAVE_0026
	case_q();
	case_r();
	case_r3();
	case_r4();
#endif
#ifdef HAVE_0039
	case_s();
#endif
#ifdef HAVE_READ_VCOM
	case_v();
#endif
#ifdef HAVE_PDTRACE
	case_pdtrace(false, false, 0);
	case_pdtrace(false, false, 2);
	case_pdtrace(true, false, 0);
	case_pdtrace(false, true, 0);
#endif
	if (failures)
		fprintf(stderr, "FAILED: %d case(s)\n", failures);
	else
		fprintf(stderr, "all cases passed\n");
	return failures ? 1 : 0;
}
