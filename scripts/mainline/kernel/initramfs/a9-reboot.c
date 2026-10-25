/*
 * a9-reboot: reboot(2) with a reboot-mode argument, which busybox's reboot
 * cannot pass. "a9-reboot bootloader" -> PM6125 PON reboot-mode 0x2 ->
 * the ABL enters fastboot (mode-bootloader in pm6125.dtsi; MEASURED in the
 * A9 ABL: reason 2 sets ToFastboot, LinuxLoader 0x1608 jump table). Also:
 * recovery (reason 1), poweroff, or no argument for a plain restart, which
 * writes no reason (the qcom_pon beacon, 2, then decides).
 * Freestanding (no libc): built with -nostdlib -static.
 */
#define SYS_sync	81
#define SYS_reboot	142
#define SYS_write	64
#define MAGIC1		0xfee1deadL
#define MAGIC2		672274793L
#define CMD_RESTART	0x01234567L
#define CMD_RESTART2	0xa1b2c3d4L
#define CMD_POWER_OFF	0x4321fedcL

static long sc(long n, long a, long b, long c, long d)
{
	register long x8 __asm__("x8") = n;
	register long x0 __asm__("x0") = a;
	register long x1 __asm__("x1") = b;
	register long x2 __asm__("x2") = c;
	register long x3 __asm__("x3") = d;

	__asm__ volatile("svc 0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2), "r"(x3)
			 : "memory");
	return x0;
}

static int eq(const char *a, const char *b)
{
	while (*a && *a == *b)
		a++, b++;
	return *a == *b;
}

static void say(const char *s)
{
	long n = 0;

	while (s[n])
		n++;
	sc(SYS_write, 2, (long)s, n, 0);
}

__attribute__((used)) void cmain(long *sp)
{
	long argc = sp[0];
	char **argv = (char **)(sp + 1);
	const char *m = argc > 1 ? argv[1] : "";

	sc(SYS_sync, 0, 0, 0, 0);
	if (eq(m, "poweroff"))
		sc(SYS_reboot, MAGIC1, MAGIC2, CMD_POWER_OFF, 0);
	else if (*m)
		sc(SYS_reboot, MAGIC1, MAGIC2, CMD_RESTART2, (long)m);
	else
		sc(SYS_reboot, MAGIC1, MAGIC2, CMD_RESTART, 0);
	say("a9-reboot: reboot(2) failed\n");
	for (;;)
		sc(93 /* exit */, 1, 0, 0, 0);
}

__asm__(".globl _start\n_start:\n\tmov x0, sp\n\tbl cmain\n");
