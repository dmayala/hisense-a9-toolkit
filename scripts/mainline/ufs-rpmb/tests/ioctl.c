/* SPDX-License-Identifier: GPL-2.0-only */
/* Run the production handler against stock byte layouts and a fake UFS query. */
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../driver/a9-ufs-query-abi.h"

_Static_assert(UFS_IOCTL_QUERY == 0x5388, "stock ioctl");
_Static_assert(sizeof(struct ufs_ioctl_query_data) == 8, "stock header");
_Static_assert(offsetof(struct ufs_ioctl_query_data, opcode) == 0, "opcode");
_Static_assert(offsetof(struct ufs_ioctl_query_data, idn) == 4, "idn");
_Static_assert(offsetof(struct ufs_ioctl_query_data, buf_size) == 6, "length");
_Static_assert(offsetof(struct ufs_ioctl_query_data, buffer) == 8, "payload");

#define __user
#define ENOIOCTLCMD 515
#define GFP_KERNEL 0
#define min_t(type, a, b) ((type)(a) < (type)(b) ? (type)(a) : (type)(b))
typedef uint8_t u8;
enum {
	UPIU_QUERY_OPCODE_READ_DESC = 1,
	QUERY_DESC_IDN_DEVICE = 0,
	QUERY_DESC_IDN_UNIT = 2,
	QUERY_DESC_IDN_GEOMETRY = 7,
	QUERY_DESC_MIN_SIZE = 2,
	QUERY_DESC_MAX_SIZE = 255,
	UFS_UPIU_RPMB_WLUN = 0xc4,
};
struct scsi_device { void *host; unsigned int lun; int sdev_gendev; };
struct ufs_dev_info { unsigned int max_lu_supported; };
struct ufs_hba {
	struct ufs_dev_info dev_info;
	struct scsi_device *ufs_device_wlun;
};
#define shost_priv(host) ((struct ufs_hba *)(host))
static u8 ufshcd_scsi_to_upiu_lun(unsigned int lun)
{
	return (lun & 0xff00) == 0xc100 ? (lun & 0x7f) | 0x80 : lun & 0x7f;
}
static int ufs_is_valid_unit_desc_lun(struct ufs_dev_info *d, u8 lun)
{
	return d->max_lu_supported && (lun == 0xc4 || lun < d->max_lu_supported);
}
static int from_fault, to_fault, to_calls, oom, pm_ret, gets, pm_puts, queries;
static int query_ret, reply_len = -1, expected_len = 32, expected_idn, expected_index;
static int logged_rc, logged_valid, log_calls;
static unsigned int expected_rpmb;
static void log_public(const char *fmt, unsigned int idn, unsigned int rpmb_lun,
		       int rc, unsigned int valid)
{
	assert(!strcmp(fmt, "A9_RPMB_QUERY idn=%u rpmb_lun=%u rc=%d length_valid=%u\n"));
	assert(idn == (unsigned int)expected_idn);
	assert(rpmb_lun == expected_rpmb);
	logged_rc=rc; logged_valid=valid; ++log_calls;
}
#define pr_info_ratelimited log_public
static int allocations;
static size_t copy_from_user(void *d, const void *s, size_t n)
{
	if (from_fault) return n;
	memcpy(d, s, n);
	return 0;
}
static size_t copy_to_user(void *d, const void *s, size_t n)
{
	if (++to_calls == to_fault) return n;
	memcpy(d, s, n);
	return 0;
}
static void *kzalloc(size_t n, int flags)
{
	(void)flags;
	if (oom) return NULL;
	++allocations;
	return calloc(1, n);
}
static void kfree(void *p) { if (p) --allocations; free(p); }
static int pm_runtime_resume_and_get(int *dev) { (void)dev; ++gets; return pm_ret; }
static int ufshcd_rpm_put_sync(struct ufs_hba *h) { (void)h; ++pm_puts; return 0; }
static int ufshcd_query_descriptor_retry(struct ufs_hba *h, int op, int idn,
				       u8 index, u8 selector, u8 *desc, int *len)
{
	(void)h;
	++queries;
	assert(op == 1 && idn == expected_idn && index == expected_index && selector == 0);
	assert(*len == expected_len);
	for (int i = 0; i < *len; ++i) assert(desc[i] == 0);
	memset(desc, 0x5a, *len);
	if (reply_len >= 0) *len = reply_len;
	return query_ret;
}
#include "../driver/a9-ufs-query.h"

static unsigned char buf[320];
static struct scsi_device sdev;
static struct ufs_hba hba = { { 8 }, &sdev };
static void reset(int idn, unsigned int lun, unsigned int len)
{
	/* Literal stock librpmb 40-byte request: u32=1, u8=idn, pad, u16=32. */
	const unsigned char stock[40] = { 1, 0, 0, 0, 0, 0, 32, 0 };
	memset(buf, 0xcc, sizeof(buf));
	memcpy(buf, stock, sizeof(stock));
	buf[4] = idn; buf[6] = len; buf[7] = len >> 8;
	sdev.host = &hba; sdev.lun = lun;
	expected_rpmb = lun == 0xc144;
	logged_rc=logged_valid=log_calls=0;
	from_fault = to_fault = to_calls = oom = pm_ret = gets = pm_puts = queries = 0;
	query_ret = 0; reply_len = -1; expected_len = len > 255 ? 255 : len;
	expected_idn = idn; expected_index = idn == 2 ? (lun & 0x7f) | 0x80 : 0;
	assert(allocations == 0);
}
static int run(void)
{
	int ret = a9_ufs_query_ioctl(&sdev, 0x5388, buf);
	assert(log_calls <= 1);
	if (log_calls) assert(logged_rc == ret);
	assert(allocations == 0);
	return ret;
}
int main(void)
{
	/* 1: stock layout fixtures for all three descriptors, padding round trip. */
	for (int i = 0; i < 3; ++i) {
		struct ufs_ioctl_query_data q;
		int idn = (int[]){ 0, 7, 2 }[i];
		reset(idn, 0xc144, 32); memcpy(&q, buf, 8);
		assert(q.opcode == 1 && q.idn == idn && q.buf_size == 32);
		buf[5] = 0xa5;
		assert(run() == 0 && buf[5] == 0xa5);
		assert(log_calls == 1 && logged_valid == 1);
		assert(gets == 1 && pm_puts == 1 && queries == 1);
		assert(buf[8] == 0x5a && buf[39] == 0x5a && buf[40] == 0xcc);
	}
	/* 2: shorter descriptor and native/compat pointer-free shared layout. */
	reset(2, 0xc144, 32); reply_len = 30;
	assert(run() == 0 && buf[6] == 30 && buf[7] == 0 && buf[38] == 0);
	reset(2, 3, 32); expected_index = 3; assert(run() == 0);
	/* 3: no write, attribute, flag or unlisted descriptor reaches the query. */
	for (int op = 0; op < 10; ++op) {
		if (op == 1) continue;
		reset(0, 0xc144, 32); buf[0] = op;
		assert(run() == -EINVAL && queries == 0 && gets == 0);
	}
	reset(1, 0xc144, 32); assert(run() == -EINVAL && gets == 0);
	reset(2, 0xc150, 32); assert(run() == -EINVAL && gets == 0);
	reset(0, 0xc144, 1); assert(run() == -EINVAL && gets == 0);
	assert(a9_ufs_query_ioctl(&sdev, 0x5389, NULL) == -ENOIOCTLCMD);
	assert(a9_ufs_query_ioctl(&sdev, 0x5388, NULL) == -EINVAL);
	/* 4: stock 255-byte cap, minimum size, failed/oversized device reply. */
	reset(7, 0xc144, 65535); assert(run() == 0 && buf[6] == 255 && buf[7] == 0);
	reset(0, 0xc144, 2); assert(run() == 0);
	reset(0, 0xc144, 32); query_ret = -ETIMEDOUT;
	assert(run() == -ETIMEDOUT && pm_puts == 1 && to_calls == 0);
	assert(log_calls == 1 && logged_valid == 0);
	reset(0, 0xc144, 32); query_ret = 1;
	assert(run() == -EIO && pm_puts == 1 && to_calls == 0);
	reset(0, 0xc144, 32); reply_len = 33;
	assert(run() == -EIO && pm_puts == 1 && to_calls == 0);
	assert(log_calls == 1 && logged_valid == 0);
	/* 5: user faults, allocation and PM failure, exactly balanced releases. */
	reset(0, 0xc144, 32); from_fault = 1;
	assert(run() == -EFAULT && gets == 0);
	reset(0, 0xc144, 32); oom = 1;
	assert(run() == -ENOMEM && gets == 0);
	reset(0, 0xc144, 32); pm_ret = -EIO;
	assert(run() == -EIO && gets == 1 && pm_puts == 0 && queries == 0);
	for (int i = 1; i <= 2; ++i) {
		reset(0, 0xc144, 32); to_fault = i;
		assert(run() == -EFAULT && gets == 1 && pm_puts == 1 && queries == 1);
		assert(log_calls == 1 && logged_valid == 1);
	}
	puts("PASS: 5 groups: stock ABI fixtures, descriptors, read-only scope, bounds, faults/PM");
	return 0;
}
