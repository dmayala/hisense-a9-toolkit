/* SPDX-License-Identifier: GPL-2.0-only */
/* Mainline equivalent of stock drivers/firmware/qcom/tz_log.c qsee_log.
 * Stock ABI: 32KiB coherent SHM, owner50/service1/cmd6, arginfo0x22,
 * little-endian u16 wrap/u16 offset followed by text. No RPMB command here.
 */
#include <linux/debugfs.h>
#include "a9-qsee-log-ring.h"
#define A9_QSEE_LOG_SIZE SZ_32K
#define A9_QSEE_LOG_CAPACITY (A9_QSEE_LOG_SIZE - 4)
static struct qsee_buf *a9_qsee_log;

static int a9_qsee_log_open(struct inode *inode, struct file *file)
{
	file->private_data = kzalloc_obj(struct a9_qsee_log_cursor);
	return file->private_data ? nonseekable_open(inode, file) : -ENOMEM;
}

static ssize_t a9_qsee_log_read(struct file *file, char __user *buf,
	size_t count, loff_t *pos)
{
	struct a9_qsee_log_cursor *cursor = file->private_data, end;
	struct a9_qsee_log_span span;
	u8 *shared = a9_qsee_log->cpu, *copy;
	u32 before, after;
	int ret = -EAGAIN;
	unsigned int retry;
	if (!count)
		return 0;
	count = min_t(size_t, count, PAGE_SIZE);
	copy = kmalloc(count, GFP_KERNEL);
	if (!copy)
		return -ENOMEM;
	for (retry = 0; retry < 3; retry++) {
		before = READ_ONCE(*(u32 *)shared);
		dma_rmb();
		end.wrap = get_unaligned_le16(&before);
		end.offset = get_unaligned_le16((u8 *)&before + 2);
		ret = a9_qsee_log_span(*cursor, end, A9_QSEE_LOG_CAPACITY,
			count, &span);
		if (ret < 0) { ret = -EIO; break; }
		if (!ret) { ret = -EAGAIN; break; }
		memcpy(copy, shared + 4 + span.start, span.length);
		dma_rmb();
		after = READ_ONCE(*(u32 *)shared);
		if (before != after) { ret = -EAGAIN; continue; }
		if (copy_to_user(buf, copy, span.length)) { ret = -EFAULT; break; }
		*cursor = span.next;
		ret = span.length;
		break;
	}
	memzero_explicit(copy, count);
	kfree(copy);
	return ret;
}

static int a9_qsee_log_release(struct inode *inode, struct file *file)
{
	kfree(file->private_data);
	return 0;
}

static const struct file_operations a9_qsee_log_fops = {
	.owner = THIS_MODULE, .open = a9_qsee_log_open,
	.read = a9_qsee_log_read, .release = a9_qsee_log_release,
};

static void a9_qsee_log_init(void)
{
	struct qcom_scm_res response;
	struct dentry *dir, *entry;
	u64 args[2];
	int ret;
	a9_qsee_log = qsee_shared_alloc(A9_QSEE_LOG_SIZE);
	if (IS_ERR(a9_qsee_log)) {
		pr_info("A9_QSEE_LOG phase=1 rc=%ld\n", PTR_ERR(a9_qsee_log));
		a9_qsee_log = NULL;
		return;
	}
	args[0] = a9_qsee_log->dma;
	args[1] = A9_QSEE_LOG_SIZE;
	ret = scm(QSEE_OWNER_OS, QSEE_APP_MGR, 6,
		QCOM_SCM_ARGS(2, QCOM_SCM_RW, QCOM_SCM_VAL), args, &response);
	if (!ret && response.result[0])
		ret = -EIO;
	pr_info("A9_QSEE_LOG phase=2 rc=%d\n", ret);
	/* TZ may retain the buffer even on an ambiguous error. Never free/reuse
	 * it, retry registration or alter the secure producer's cursor. */
	if (ret)
		return;
	dir = debugfs_create_dir("tzdbg", NULL);
	if (IS_ERR(dir)) {
		pr_info("A9_QSEE_LOG phase=3 rc=%ld\n", PTR_ERR(dir));
		return;
	}
	entry = debugfs_create_file("qsee_log", 0400, dir, NULL, &a9_qsee_log_fops);
	pr_info("A9_QSEE_LOG phase=3 rc=%ld\n", IS_ERR(entry) ? PTR_ERR(entry) : 0L);
}
