// SPDX-License-Identifier: GPL-2.0-only
/*
 * Android QSEECom transport for SM6115.
 * ABI and SCM protocol: Qualcomm msm-4.19 qseecom.c (Linux Foundation,
 * 2012-2020). Userspace layouts are the unmodified downstream UAPI.
 * This is a deliberately small native ARM64 transport, not an ION framework.
 * /
 */
#include <linux/dma-buf.h>
#include <linux/dma-mapping.h>
#include <linux/file.h>
#include <linux/fs.h>
#include <linux/iosys-map.h>
#include <linux/kref.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/random.h>
#include <linux/scatterlist.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/wait.h>
#include <linux/unaligned.h>
#include <linux/sizes.h>
#include <linux/firmware/qcom/qcom_scm.h>
#include <linux/firmware/qcom/qcom_tzmem.h>
#include <scsi/scsi_cmnd.h>
#include "qcom_scm.h"
#include "qseecom-uapi.h"
#include "ion-uapi.h"
#include "a9-qseecom-bounds.h"
#include "a9-rpmb-command.h"
#include "a9-rpmb-gk-policy.h"
#include "a9-rpmb-kmtb-policy.h"
#include "a9-rpmb-observe.h"
#include "a9-rpmb-bootstrap.h"

#define QSEE_MAX_ALLOC SZ_64M
#define QSEE_OWNER_OS 50
#define QSEE_OWNER_APP 48
#define QSEE_APP_MGR 1
#define QSEE_LISTENER 2
#define QSEE_INCOMPLETE 1
#define QSEE_BLOCKED 2
#define QSEE_APP_ID 0xee01

/*
 * Implemented beside qcom_scm_call so it uses the upstream SCM convention,
 * extended arguments, retry behavior, and QSEECom call mutex.
 */

struct qsee_buf {
	void *cpu;
	dma_addr_t dma;
	size_t len;
	u64 bridge;
};

static struct device *qsee_dev;
static DEFINE_MUTEX(transaction_lock);
static DEFINE_MUTEX(listeners_lock);
static LIST_HEAD(listeners);
static LIST_HEAD(apps);
static bool whitelist;
/* Protected by transaction_lock, like downstream's smcinvoke_support. */
static bool listener_smcinvoke;

/* GK/KM extents are learned from stock PTBL reads (observed or read-only
 * bootstrap through the stock listener).
 * Allocation metadata is never writable, so the map cannot be moved by this
 * driver. This is accidental-write protection under the stock TA/listener
 * assumption, not normal-world RPMB MAC verification.
 */
static DEFINE_SPINLOCK(rpmb_map_lock);
static struct a9_rpmb_gk_map rpmb_gk_map;
static struct a9_kmtb_state rpmb_kmtb;
static unsigned int rpmb_public_context;
/* Only finish_call, under transaction_lock; failures consume the boot attempt.
 * The published map stays cached. Existing explicit revocation still wins.
 */
static bool rpmb_bootstrap_attempted;

bool a9_qsee_rpmb_data_write_allowed(struct scsi_cmnd *cmd);
static bool rpmb_control_bytes(void *ctx, unsigned int index, unsigned char *out)
{
	struct scsi_cmnd *cmd = ctx;

	return sg_pcopy_to_buffer(scsi_sglist(cmd), scsi_sg_count(cmd), out, 12,
				 (size_t)index * 512 + 500) == 12;
}

/* Called from queuecommand: no sleeping/allocation, no mutation of SG bytes. */
bool a9_qsee_rpmb_data_write_allowed(struct scsi_cmnd *cmd)
{
	struct a9_rpmb_gk_map map, rr = { 0 };
	unsigned char frames[1024], control[12];
	unsigned long flags;
	unsigned int bytes = scsi_bufflen(cmd), i, operation = 0, changed = 0, reason = 4;
	unsigned int first = 0, offset = 0, partition = 0, context;
	struct a9_rpmb_gk_map all = { .first = 0, .end = 65536 };
	bool groups;
	bool allowed = false, candidate = false, copied = false;

	if (!bytes || bytes % 512 || bytes / 512 > A9_RPMB_GK_MAX_FRAMES)
		return false;
	if (bytes <= sizeof(frames))
		copied = sg_pcopy_to_buffer(scsi_sglist(cmd), scsi_sg_count(cmd),
					  frames, bytes, 0) == bytes;
	spin_lock_irqsave(&rpmb_map_lock, flags);
	map = rpmb_gk_map;
	context = rpmb_public_context;
	groups = a9_rpmb_gk_groups(&all, bytes / 512, rpmb_control_bytes, cmd);
	if (groups && rpmb_control_bytes(cmd, 0, control)) {
		first = a9_rpmb_be16(control + 4);
		partition = a9_rpmb_observe_partition(&map, first, bytes / 512, &offset);
	}
	if (a9_rpmb_gk_groups(&map, bytes / 512, rpmb_control_bytes, cmd)) {
		allowed = true;
		goto unlock;
	}
	for (i = 0; i < bytes / 512; i++) {
		if (!rpmb_control_bytes(cmd, i, control))
			break;
		if (a9_rpmb_be16(control + 10) == 3 &&
		    a9_kmtb_in_partition(&map, a9_rpmb_be16(control + 4),
					 a9_rpmb_be16(control + 6)))
			candidate = true;
	}
	if (candidate) {
		rr.first = map.km_first + 32;
		rr.end = map.km_first + 80;
		allowed = copied && a9_kmtb_map_ok(&map) &&
			a9_rpmb_gk_groups(&rr, bytes / 512, rpmb_control_bytes, cmd) &&
			a9_kmtb_allow(&rpmb_kmtb, frames, bytes);
		operation = rpmb_kmtb.operation;
		changed = rpmb_kmtb.changed;
		reason = allowed ? 0 : (rpmb_kmtb.reason ? rpmb_kmtb.reason : 6);
	}
unlock:
	spin_unlock_irqrestore(&rpmb_map_lock, flags);
	memzero_explicit(frames, sizeof(frames));
	pr_info("A9_RPMB_WRITE_TRACE allowed=%u map_valid=%u groups_valid=%u partition=%u first=%u offset=%u frames=%u context=%u\n",
		allowed, !!map.end, groups, partition, first, offset, bytes / 512, context);
	if (candidate)
		pr_info("A9_KMTB_WRITE phase=2 allowed=%u operation=%u reason=%u changed=%u\n",
			allowed, operation, reason, changed);
	return allowed;
}

static void rpmb_set_map(struct a9_rpmb_gk_map map, unsigned int phase)
{
	unsigned long flags;

	spin_lock_irqsave(&rpmb_map_lock, flags);
	rpmb_gk_map = map;
	/* A new/failed PTBL read is an epoch boundary. Preserve only the active
	 * public command classification, never an old table or pending write.
	 */
	{
		unsigned int context = rpmb_kmtb.context;
		bool read_this_call = rpmb_kmtb.read_this_call;

		memzero_explicit(&rpmb_kmtb, sizeof(rpmb_kmtb));
		rpmb_kmtb.context = context;
		rpmb_kmtb.read_this_call = read_this_call;
	}
	spin_unlock_irqrestore(&rpmb_map_lock, flags);
	pr_info_ratelimited("A9_RPMB_MAP phase=%u valid=%u\n", phase, !!map.end);
}

struct qsee_sginfo {
	u32 index_flags, size;
};
struct qsee_file {
	struct mutex lock;
	struct kref ref;
	struct list_head node;
	wait_queue_head_t wait;
	spinlock_t state_lock;
	bool registered, abort, pending, delivered, replied;
	u32 listener_id, app_id, app_arch;
	struct dma_buf *dbuf;
	struct qsee_buf *buf;
	unsigned long user_base;
	u32 size;
	struct qsee_sginfo *sginfo;
	dma_addr_t sg_dma;
	struct dma_buf *reply_bufs[MAX_ION_FD];
	u32 reply_offsets[MAX_ION_FD];
	struct qsee_buf *sgbuf;
	bool reply_wide;
};
struct qsee_app {
	struct list_head node;
	u32 id, arch, refs;
	bool keymaster;
};


static struct qsee_buf *qsee_shared_alloc(size_t len)
{
	struct qsee_buf *b;
	int ret;

	b = kzalloc_obj(*b);
	if (!b)
		return ERR_PTR(-ENOMEM);
	b->len = PAGE_ALIGN(len);
	b->cpu = dma_alloc_coherent(qsee_dev, b->len, &b->dma,
				    GFP_KERNEL | __GFP_ZERO);
	if (!b->cpu) {
		kfree(b);
		return ERR_PTR(-ENOMEM);
	}
	ret = qcom_tzmem_shm_bridge_create(b->dma, b->len, &b->bridge);
	if (ret) {
		pr_err_ratelimited("qseecom: SHM bridge create failed: %d\n", ret);
		dma_free_coherent(qsee_dev, b->len, b->cpu, b->dma);
		kfree(b);
		return ERR_PTR(ret);
	}
	return b;
}

static void qsee_shared_free(struct qsee_buf *b)
{
	/* Never return pages while secure world may retain their bridge. */
	if (b->bridge && qcom_scm_shm_bridge_delete(b->bridge)) {
		pr_err("qseecom: SHM bridge teardown failed; allocation quarantined\n");
		return;
	}
	memzero_explicit(b->cpu, b->len);
	dma_free_coherent(qsee_dev, b->len, b->cpu, b->dma);
	kfree(b);
}

static struct sg_table *qsee_map(struct dma_buf_attachment *a,
				 enum dma_data_direction dir)
{
	struct qsee_buf *b = a->dmabuf->priv;
	struct sg_table *t = kzalloc_obj(*t);
	int ret;

	if (!t)
		return ERR_PTR(-ENOMEM);
	ret = dma_get_sgtable(qsee_dev, t, b->cpu, b->dma, b->len);
	if (ret)
		goto fail;
	ret = dma_map_sgtable(a->dev, t, dir, 0);
	if (ret) {
		sg_free_table(t);
		goto fail;
	}
	return t;
fail:
	kfree(t);
	return ERR_PTR(ret);
}
static void qsee_unmap(struct dma_buf_attachment *a, struct sg_table *t,
		       enum dma_data_direction dir)
{
	dma_unmap_sgtable(a->dev, t, dir, 0);
	sg_free_table(t);
	kfree(t);
}
static int qsee_mmap(struct dma_buf *d, struct vm_area_struct *vma)
{
	struct qsee_buf *b = d->priv;

	return dma_mmap_coherent(qsee_dev, vma, b->cpu, b->dma, b->len);
}
static int qsee_vmap(struct dma_buf *d, struct iosys_map *map)
{
	struct qsee_buf *b = d->priv;

	iosys_map_set_vaddr(map, b->cpu);
	return 0;
}
static void qsee_vunmap(struct dma_buf *d, struct iosys_map *map)
{
}
static int qsee_cpu_access(struct dma_buf *d, enum dma_data_direction dir)
{
	/* Both CPU mappings use dma_alloc_coherent/dma_mmap_coherent. */
	mb();
	return 0;
}
static void qsee_buf_free(struct dma_buf *d)
{
	qsee_shared_free(d->priv);
}
static const struct dma_buf_ops qsee_dma_ops = {
	.map_dma_buf = qsee_map,
	.unmap_dma_buf = qsee_unmap,
	.mmap = qsee_mmap,
	.vmap = qsee_vmap,
	.vunmap = qsee_vunmap,
	.begin_cpu_access = qsee_cpu_access,
	.end_cpu_access = qsee_cpu_access,
	.release = qsee_buf_free,
};
static struct dma_buf *qsee_get_buf(int fd)
{
	struct dma_buf *d = dma_buf_get(fd);

	if (IS_ERR(d))
		return d;
	/*
	 * These are the physically contiguous, non-secure QSEE heaps only.
	 * Never treat an arbitrary imported scatterlist as one physical range.
	 */
	if (d->ops != &qsee_dma_ops) {
		dma_buf_put(d);
		return ERR_PTR(-EINVAL);
	}
	return d;
}
static long ion_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
	void __user *up = (void __user *)arg;
	struct ion_allocation_data req;
	struct qsee_buf *b;
	struct dma_buf *d;
	DEFINE_DMA_BUF_EXPORT_INFO(exp);
	int fd;

	if (cmd == ION_IOC_HEAP_QUERY) {
		struct ion_heap_query q;
		struct ion_heap_data heaps[2] = {
			{ .name = "qseecom_ta",
			  .type = ION_HEAP_TYPE_DMA,
			  .heap_id = 19 },
			{ .name = "qseecom",
			  .type = ION_HEAP_TYPE_DMA,
			  .heap_id = 27 },
		};
		if (copy_from_user(&q, up, sizeof(q)))
			return -EFAULT;
		if (q.reserved0 || q.reserved1 || q.reserved2)
			return -EINVAL;
		if (q.heaps && !q.cnt)
			return -EINVAL;
		if (q.heaps &&
		    copy_to_user(u64_to_user_ptr(q.heaps), heaps,
				 min_t(u32, q.cnt, 2) * sizeof(heaps[0])))
			return -EFAULT;
		q.cnt = q.heaps ? min_t(u32, q.cnt, 2) : 2;
		return copy_to_user(up, &q, sizeof(q)) ? -EFAULT : 0;
	}
	/*
	 * Stock libion probes ION_IOC_FREE(0), 0xc0044901. ENOTTY selects
	 * its direct dma-buf ABI;
	 * EINVAL would incorrectly select legacy handles.
	 */
	if (cmd != ION_IOC_ALLOC)
		return -ENOTTY;
	if (copy_from_user(&req, up, sizeof(req)))
		return -EFAULT;
	if (!req.len || req.len > QSEE_MAX_ALLOC || req.unused ||
	    (req.flags & ~ION_FLAG_CACHED) ||
	    !(req.heap_id_mask & (BIT(19) | BIT(27))) ||
	    (req.heap_id_mask & ~(BIT(19) | BIT(27))))
		return -EINVAL;
	b = qsee_shared_alloc(req.len);
	if (IS_ERR(b))
		return PTR_ERR(b);
	exp.ops = &qsee_dma_ops;
	exp.size = b->len;
	exp.flags = O_RDWR;
	exp.priv = b;
	d = dma_buf_export(&exp);
	if (IS_ERR(d)) {
		qsee_shared_free(b);
		return PTR_ERR(d);
	}
	fd = get_unused_fd_flags(O_CLOEXEC);
	if (fd < 0) {
		dma_buf_put(d);
		return fd;
	}
	req.fd = fd;
	if (copy_to_user(up, &req, sizeof(req))) {
		put_unused_fd(fd);
		dma_buf_put(d);
		return -EFAULT;
	}
	fd_install(fd, d->file);
	return 0;
}
static const struct file_operations ion_fops = {
	.owner = THIS_MODULE,
	.unlocked_ioctl = ion_ioctl,
#ifdef CONFIG_COMPAT
	.compat_ioctl = compat_ptr_ioctl,
#endif
	.llseek = noop_llseek,
};
static struct miscdevice ion_misc = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "ion",
	.fops = &ion_fops,
	.mode = 0600,
};

static int scm(u32 owner, u32 svc, u32 cmd, u32 info, const u64 *args,
	       struct qcom_scm_res *res)
{
	struct qcom_scm_desc d = {
		.owner = owner, .svc = svc, .cmd = cmd, .arginfo = info
	};
	unsigned int n = info & 15;

	if (n > ARRAY_SIZE(d.args))
		return -EINVAL;
	memcpy(d.args, args, n * sizeof(*args));
	memset(res, 0, sizeof(*res));
	/* Order coherent shared-buffer writes before entering secure world. */
	dma_wmb();
	return qcom_scm_qseecom_android_call(&d, res);
}

static int qsee_register_listener_scm(struct qsee_file *f, u32 listener_id,
				      struct qcom_scm_res *r)
{
	/* Stock __qseecom_set_sb_memory registers the mapped ION SG span,
	 * not the userspace listener payload length. For RPMB these are
	 * 0x7000 and 0x6400 respectively. The entire allocation is zeroed and
	 * SHM-bridged; user/reply range checks keep the original f->size bound.
	 */
	u64 args[] = { listener_id, f->buf->dma, f->buf->len };
	bool smcinvoke = true;
	int ret;

	/* Stock SM6115 tries 0x32000206, falling back only on unsupported (-EIO).
	 * Forcing the old 0x32000201 call fails at transport with -EINVAL here.
	 * This selects the listener ABI; transactions remain fully serialized.
	 */
	if (listener_id == 0x2000)
		pr_info_ratelimited("A9_RPMB_REGISTER request=%u mapped=%zu buf_page_aligned=%u len_page_aligned=%u cmd=6 arginfo=0x83\n",
			f->size, f->buf->len, IS_ALIGNED(f->buf->dma, PAGE_SIZE),
			IS_ALIGNED(f->buf->len, PAGE_SIZE));
	ret = scm(QSEE_OWNER_OS, QSEE_LISTENER, 6,
		  QCOM_SCM_ARGS(3, QCOM_SCM_VAL, QCOM_SCM_RW, QCOM_SCM_VAL),
		  args, r);
	if (listener_id == 0x2000)
		pr_info_ratelimited("A9_RPMB_REGISTER check=scm_return cmd=6 rc=%d\n", ret);
	if (ret == -EIO) {
		smcinvoke = false;
		ret = scm(QSEE_OWNER_OS, QSEE_LISTENER, 1,
			  QCOM_SCM_ARGS(3, QCOM_SCM_VAL, QCOM_SCM_RW,
					QCOM_SCM_VAL), args, r);
		if (listener_id == 0x2000)
			pr_info_ratelimited("A9_RPMB_REGISTER check=scm_return cmd=1 rc=%d\n", ret);
	}
	if (!ret)
		listener_smcinvoke = smcinvoke;
	return ret;
}

static int qsee_continue_blocked(struct qcom_scm_res *r, u32 app_id)
{
	u64 args[1] = { listener_smcinvoke ? r->result[1] : app_id };
	int ret;

	/* New listener ABI resumes resp_type's session, not the app ID.
	 * Match stock's one legacy continuation fallback on transport failure.
	 */
	ret = scm(QSEE_OWNER_OS, QSEE_LISTENER, listener_smcinvoke ? 7 : 4,
		  QCOM_SCM_ARGS(1), args, r);
	if (ret && listener_smcinvoke) {
		args[0] = app_id;
		ret = scm(QSEE_OWNER_OS, QSEE_LISTENER, 4,
			  QCOM_SCM_ARGS(1), args, r);
	}
	return ret;
}
#include "a9-qsee-log.h"
static void reply_cleanup(struct qsee_file *f)
{
	int i;

	for (i = 0; i < MAX_ION_FD; i++) {
		if (!f->reply_bufs[i])
			continue;
		if (f->reply_wide)
			put_unaligned_le64(0,
					   f->buf->cpu + f->reply_offsets[i]);
		else
			put_unaligned_le32(0,
					   f->buf->cpu + f->reply_offsets[i]);
		dma_buf_put(f->reply_bufs[i]);
		f->reply_bufs[i] = NULL;
	}
	memset(f->sginfo, 0, MAX_ION_FD * sizeof(*f->sginfo));
}
static void qsee_file_free(struct kref *ref)
{
	struct qsee_file *f = container_of(ref, struct qsee_file, ref);

	if (f->dbuf)
		dma_buf_put(f->dbuf);
	qsee_shared_free(f->sgbuf);
	kfree(f);
}

/* Same receive/reply path as a secure callback, but no response SCM: secure
 * world is paused on the ORIGINAL write until it has been restored/delivered.
 * Must drain a delivered read or abort, never restore over an in-flight user
 * buffer after a timeout. No sleeping or nested I/O in UFS queuecommand.
 */
static int rpmb_bootstrap_exchange(void *ctx)
{
	struct qsee_file *f = ctx;
	unsigned long flags;
	int ret = 0, i;

	dma_wmb();
	spin_lock_irqsave(&f->state_lock, flags);
	if (f->abort) {
		spin_unlock_irqrestore(&f->state_lock, flags);
		return -ENODEV;
	}
	f->pending = true;
	f->delivered = false;
	f->replied = false;
	spin_unlock_irqrestore(&f->state_lock, flags);
	wake_up_all(&f->wait);
	wait_event(f->wait, READ_ONCE(f->replied) || READ_ONCE(f->abort));
	dma_rmb();
	if (READ_ONCE(f->abort))
		ret = -ENODEV;
	for (i = 0; i < MAX_ION_FD; i++)
		if (f->reply_bufs[i])
			ret = -EPROTO; /* this read needs no modified-fd response */
	spin_lock_irqsave(&f->state_lock, flags);
	f->pending = false;
	f->replied = false;
	spin_unlock_irqrestore(&f->state_lock, flags);
	reply_cleanup(f);
	wake_up_all(&f->wait);
	return ret;
}
static void rpmb_bootstrap_nonce(void *ctx, unsigned char *out, unsigned int bytes)
{
	get_random_bytes(out, bytes);
}
static void rpmb_bootstrap_trace(void *ctx, unsigned int phase, int rc)
{
	pr_info("A9_RPMB_BOOTSTRAP phase=%u rc=%d\n", phase, rc);
}
static const struct a9_rpmb_bootstrap_ops rpmb_bootstrap_ops = {
	.exchange = rpmb_bootstrap_exchange,
	.nonce = rpmb_bootstrap_nonce,
	.trace = rpmb_bootstrap_trace,
};

static int rpmb_before_write(struct qsee_file *f)
{
	struct a9_rpmb_gk_map map;
	unsigned char *scratch, *frames;
	unsigned long flags;
	unsigned int context, i;
	bool valid, need_map;
	int ret = 0;

	lockdep_assert_held(&transaction_lock);
	if (READ_ONCE(f->abort))
		return -ENODEV;
	spin_lock_irqsave(&rpmb_map_lock, flags);
	map = rpmb_gk_map;
	context = rpmb_kmtb.context;
	valid = rpmb_kmtb.valid;
	spin_unlock_irqrestore(&rpmb_map_lock, flags);
	need_map = !map.end;
	if ((need_map && rpmb_bootstrap_attempted) ||
	    (!need_map && !a9_rpmb_bootstrap_needs_rr(&map, context, valid, f->buf->cpu, f->size)))
		return 0;
	/* Snapshot the whole mapped allocation, not only the payload extent.
	 * It includes opaque signed bytes and must be restored/erased on all exits.
	 */
	if (f->size < 24 + 512 || f->size > f->buf->len || f->buf->len > SZ_64K) {
		rpmb_bootstrap_trace(f, 6, -EINVAL);
		return 0;
	}
	scratch = kzalloc(f->buf->len + A9_RPMB_GK_MAX_FRAMES * 512, GFP_KERNEL);
	if (!scratch) {
		rpmb_bootstrap_trace(f, 6, -ENOMEM);
		return 0;
	}
	memcpy(scratch, f->buf->cpu, f->buf->len);
	frames = scratch + f->buf->len;
	if (need_map) {
		rpmb_bootstrap_trace(f, 1, 0);
		ret = a9_rpmb_bootstrap_map(&rpmb_bootstrap_attempted, &map,
			f->buf->cpu, f->size, frames, &rpmb_bootstrap_ops, f);
		rpmb_set_map(map, 2);
		rpmb_bootstrap_trace(f, 5, ret);
		if (ret)
			goto restore;
		valid = false; /* publication starts a fresh map/mirror epoch */
	}
	if (a9_rpmb_bootstrap_needs_rr(&map, context, valid, scratch, f->size)) {
		ret = a9_rpmb_bootstrap_read(f->buf->cpu, f->size, map.km_first + 32,
			A9_KMTB_FRAMES, frames, &rpmb_bootstrap_ops, f);
		spin_lock_irqsave(&rpmb_map_lock, flags);
		rpmb_kmtb.valid = !ret;
		rpmb_kmtb.read_pending = false;
		rpmb_kmtb.read_this_call = true;
		if (!ret)
			for (i = 0; i < A9_KMTB_FRAMES; i++)
				memcpy(rpmb_kmtb.table + i * 256,
					frames + i * 512 + 228, 256);
		spin_unlock_irqrestore(&rpmb_map_lock, flags);
		rpmb_bootstrap_trace(f, 4, ret);
		pr_info("A9_KMTB_READ phase=3 valid=%u\n", !ret);
	}
restore:
	/* On abort an unreplied listener may still own this mapping. Do not
	 * restore over it or deliver the original write; fail the secure call.
	 * On normal read errors the reply has drained and restoration is safe.
	 */
	if (!READ_ONCE(f->abort)) {
		memcpy(f->buf->cpu, scratch, f->buf->len);
		dma_wmb();
	}
	kfree_sensitive(scratch);
	return READ_ONCE(f->abort) ? -ENODEV : 0;
}

/*
 * Called with transaction_lock held from initial SCM through final response.
 * Listener receive/reply never take that mutex. The active listener's kref
 * and dma_buf refs survive unregister, close, and a reply's userspace close.
 */
static int finish_call(struct qcom_scm_res *r, u32 app_id)
{
	int ret, error = 0;

	while (r->result[0] == QSEE_INCOMPLETE ||
	       r->result[0] == QSEE_BLOCKED) {
		struct qsee_file *f, *found = NULL;
		u64 args[4] = { r->result[2], 0, 0, 0 };
		unsigned int rpmb_command = 0;
		struct a9_rpmb_table_read table_read = { 0 };
		struct a9_rpmb_gk_map pending_map = { 0 };
		struct a9_kmtb_read km_read = { 0 };
		bool km_write_ok = false, km_candidate = false;
		unsigned int km_operation = 0, km_changed = 0, km_reason = 0;
		unsigned long flags;

		mutex_lock(&listeners_lock);
		list_for_each_entry(f, &listeners, node) {
			if (f->listener_id == args[0]) {
				kref_get(&f->ref);
				found = f;
				break;
			}
		}
		mutex_unlock(&listeners_lock);
		f = found;
		if (r->result[0] == QSEE_BLOCKED) {
			ret = qsee_continue_blocked(r, app_id);
			if (f)
				kref_put(&f->ref, qsee_file_free);
			if (ret) {
				rpmb_set_map((struct a9_rpmb_gk_map){ 0 }, 1);
				return ret;
			}
			continue;
		}
		if (f) {
			dma_rmb();
			if (f->listener_id == 0x2000)
				rpmb_command = a9_rpmb_command(f->buf->cpu, f->size);
			if (rpmb_command == 2 || rpmb_command == 3) {
				struct a9_rpmb_observation o = a9_rpmb_observe_request(f->buf->cpu, f->size);
				unsigned int context, map_valid;

				spin_lock_irqsave(&rpmb_map_lock, flags);
				context = rpmb_public_context;
				map_valid = !!rpmb_gk_map.end;
				spin_unlock_irqrestore(&rpmb_map_lock, flags);
				pr_info("A9_RPMB_REQUEST_TRACE command=%u shape=%u reason=%u request=%u frames=%u input_frames=%u offset=%u first=%u count=%u map_valid=%u context=%u table_candidate=%u table_valid=%u\n",
					rpmb_command, o.shape, o.reason, o.request, o.frames, o.input_frames,
					o.offset, o.first, o.count, map_valid, context,
					o.table_candidate, o.table_valid);
			}
			if (rpmb_command == 2) {
				table_read = a9_rpmb_table_request(f->buf->cpu, f->size);
				if (table_read.candidate)
					rpmb_set_map((struct a9_rpmb_gk_map){ 0 }, 1);
				spin_lock_irqsave(&rpmb_map_lock, flags);
				rpmb_kmtb.read_this_call = true;
				km_read = a9_kmtb_read_request(f->buf->cpu, f->size, &rpmb_gk_map);
				if (km_read.candidate)
					rpmb_kmtb.valid = false;
				spin_unlock_irqrestore(&rpmb_map_lock, flags);
			} else if (rpmb_command == 3) {
				/* Before userspace receives the pending signed DATA_WRITE. */
				if (rpmb_before_write(f)) {
					args[1] = U32_MAX;
					error = -ENODEV;
					goto listener_response;
				}
				spin_lock_irqsave(&rpmb_map_lock, flags);
				a9_kmtb_prepare(&rpmb_kmtb, &rpmb_gk_map, f->buf->cpu, f->size);
				km_candidate = rpmb_kmtb.candidate;
				km_operation = rpmb_kmtb.operation;
				km_changed = rpmb_kmtb.changed;
				km_reason = rpmb_kmtb.reason;
				spin_unlock_irqrestore(&rpmb_map_lock, flags);
				if (km_candidate)
					pr_info("A9_KMTB_WRITE phase=1 allowed=%u operation=%u reason=%u changed=%u\n",
						!km_reason, km_operation, km_reason, km_changed);
			}
			spin_lock_irqsave(&f->state_lock, flags);
			f->pending = true;
			f->delivered = false;
			f->replied = false;
			spin_unlock_irqrestore(&f->state_lock, flags);
			wake_up_all(&f->wait);
			/*
			 * Like downstream, drain the secure call despite client signals.
			 * Explicit listener unregister/close always wakes this wait.
			 */
			wait_event(f->wait, READ_ONCE(f->replied) ||
						    READ_ONCE(f->abort));
			if (READ_ONCE(f->abort)) {
				args[1] = U32_MAX;
				error = -ENODEV;
			} else if (f->listener_id == 0x2000 &&
				   (rpmb_command == 2 || rpmb_command == 3)) {
				int operation_rc;
				bool status_valid;

				dma_rmb();
				status_valid = a9_rpmb_operation_result(f->buf->cpu,
							 f->size, &operation_rc);
				pr_info_ratelimited("A9_RPMB_RESULT command=%u status_valid=%u operation_rc=%d\n",
					rpmb_command, status_valid, operation_rc);
				if (table_read.candidate && status_valid && !operation_rc)
					a9_rpmb_table_reply(f->buf->cpu, f->size,
							    table_read, &pending_map);
				spin_lock_irqsave(&rpmb_map_lock, flags);
				if (rpmb_command == 2) {
					if (!status_valid || operation_rc)
						rpmb_kmtb.valid = false;
					a9_kmtb_read_reply(&rpmb_kmtb, f->buf->cpu, f->size, km_read);
				} else if (km_candidate) {
					km_write_ok = status_valid && !operation_rc &&
						a9_kmtb_write_reply(&rpmb_kmtb, f->buf->cpu, f->size);
				}
				spin_unlock_irqrestore(&rpmb_map_lock, flags);
			}
listener_response:
			args[2] = f->sg_dma;
			args[3] = MAX_ION_FD * sizeof(*f->sginfo);
		} else {
			args[1] = U32_MAX;
			error = -ENODEV;
		}
		ret = scm(QSEE_OWNER_OS, QSEE_LISTENER, whitelist && f ? 5 : 3,
			  whitelist && f ?
				  QCOM_SCM_ARGS(4, QCOM_SCM_VAL, QCOM_SCM_VAL,
						QCOM_SCM_RW, QCOM_SCM_VAL) :
				  QCOM_SCM_ARGS(2),
			  args, r);
		if (table_read.candidate) {
			if (!a9_rpmb_map_scm_ok(ret, r->result[0]))
				pending_map = (struct a9_rpmb_gk_map){ 0 };
			rpmb_set_map(pending_map, 2);
		}
		spin_lock_irqsave(&rpmb_map_lock, flags);
		if (km_read.candidate) {
			rpmb_kmtb.valid = rpmb_kmtb.read_pending &&
				a9_rpmb_map_scm_ok(ret, r->result[0]);
			rpmb_kmtb.read_pending = false;
		}
		km_write_ok = km_write_ok && a9_rpmb_map_scm_ok(ret, r->result[0]);
		if (km_candidate)
			a9_kmtb_write_complete(&rpmb_kmtb, &rpmb_gk_map, km_write_ok);
		spin_unlock_irqrestore(&rpmb_map_lock, flags);
		if (km_read.candidate)
			pr_info("A9_KMTB_READ phase=2 valid=%u\n",
				READ_ONCE(rpmb_kmtb.valid));
		if (km_candidate)
			pr_info("A9_KMTB_WRITE phase=3 allowed=%u operation=%u reason=%u changed=%u\n",
				km_write_ok, km_operation, km_write_ok ? 0 : 6, km_changed);
		if (f && f->listener_id == 0x2000)
			pr_info_ratelimited("A9_RPMB_CALLBACK phase=2 command=%u rc=%d status_valid=%u qsee_result=%lld\n",
				rpmb_command, ret, !ret, ret ? 0LL : (long long)r->result[0]);
		if (f) {
			spin_lock_irqsave(&f->state_lock, flags);
			f->pending = false;
			f->replied = false;
			spin_unlock_irqrestore(&f->state_lock, flags);
			reply_cleanup(f);
			wake_up_all(&f->wait);
			kref_put(&f->ref, qsee_file_free);
		}
		if (ret) {
			rpmb_set_map((struct a9_rpmb_gk_map){ 0 }, 1);
			return ret;
		}
	}
	dma_rmb();
	if (r->result[0] || error) {
		rpmb_set_map((struct a9_rpmb_gk_map){ 0 }, 1);
		if (r->result[0])
			return -EIO;
	}
	return error;
}
static int qsee_bind(struct qsee_file *f, int fd, unsigned long base, u32 len)
{
	struct dma_buf *d;

	if (!base || !len || base > ULONG_MAX - len || f->dbuf)
		return -EINVAL;
	d = qsee_get_buf(fd);
	if (IS_ERR(d))
		return PTR_ERR(d);
	if (len > d->size || !access_ok((void __user *)base, len)) {
		dma_buf_put(d);
		return -EINVAL;
	}
	f->dbuf = d;
	f->buf = d->priv;
	f->user_base = base;
	f->size = len;
	return 0;
}
static bool qsee_range(struct qsee_file *f, const void *ptr, u32 len, u32 *off)
{
	return f->buf &&
	       a9q_range(f->user_base, f->size, (unsigned long)ptr, len, off);
}
static struct qsee_app *app_find(u32 id)
{
	struct qsee_app *a;

	list_for_each_entry(a, &apps, node)
		if (a->id == id)
			return a;
	return NULL;
}
static int app_attach(struct qsee_file *f, u32 id, u32 arch, const char *name)
{
	struct qsee_app *a = app_find(id);

	if (f->app_id || !id)
		return -EINVAL;
	if (!a) {
		a = kzalloc_obj(*a);
		if (!a)
			return -ENOMEM;
		a->id = id;
		a->arch = arch;
		a->keymaster = !strcmp(name, "keymaster64");
		list_add_tail(&a->node, &apps);
	}
	if (a->refs == U32_MAX)
		return -EOVERFLOW;
	a->refs++;
	f->app_id = id;
	f->app_arch = a->arch;
	return 0;
}
static int app_unload(struct qsee_file *f)
{
	struct qsee_app *a;
	struct qcom_scm_res r;
	u64 args[] = { f->app_id };
	int ret = 0;

	if (!f->app_id)
		return -EINVAL;
	a = app_find(f->app_id);
	if (!a)
		return -ENOENT;
	if (a->refs == 1) {
		if (a->keymaster) {
			unsigned long flags;

			spin_lock_irqsave(&rpmb_map_lock, flags);
			memzero_explicit(&rpmb_kmtb, sizeof(rpmb_kmtb));
			spin_unlock_irqrestore(&rpmb_map_lock, flags);
		}
		ret = scm(QSEE_OWNER_OS, QSEE_APP_MGR, 2, QCOM_SCM_ARGS(1),
			  args, &r);
		if (!ret)
			ret = finish_call(&r, f->app_id);
		if (ret)
			return ret;
		list_del(&a->node);
		kfree(a);
	} else {
		a->refs--;
	}
	f->app_id = 0;
	return 0;
}

static int qsee_unregister(struct qsee_file *f)
{
	struct qcom_scm_res r;
	u64 args[] = { f->listener_id };
	int ret;

	if (!READ_ONCE(f->registered))
		return -EINVAL;
	WRITE_ONCE(f->abort, true);
	wake_up_all(&f->wait);
	mutex_lock(&transaction_lock);
	ret = scm(QSEE_OWNER_OS, QSEE_LISTENER, 2, QCOM_SCM_ARGS(1), args, &r);
	if (!ret)
		ret = finish_call(&r, 0);
	/*
	 * Retain registration and buffer on failed deregister: secure world may
	 * still reference it. close retries and quarantines on repeated failure.
	 */
	if (!ret) {
		mutex_lock(&listeners_lock);
		list_del_init(&f->node);
		WRITE_ONCE(f->registered, false);
		mutex_unlock(&listeners_lock);
	}
	mutex_unlock(&transaction_lock);
	return ret;
}

static int qsee_receive(struct qsee_file *f)
{
	unsigned long flags;
	int ret;

	if (!READ_ONCE(f->registered))
		return -EINVAL;
	ret = wait_event_interruptible(
		f->wait, READ_ONCE(f->abort) || (READ_ONCE(f->pending) &&
						 !READ_ONCE(f->delivered)));
	if (ret)
		return ret;
	spin_lock_irqsave(&f->state_lock, flags);
	if (f->abort)
		ret = -ENODEV;
	else if (!f->pending || f->delivered)
		ret = -EAGAIN;
	else
		f->delivered = true;
	spin_unlock_irqrestore(&f->state_lock, flags);
	if (!ret && f->listener_id == 0x2000) {
		dma_rmb();
		pr_info_ratelimited("A9_RPMB_CALLBACK phase=1 command=%u size_valid=%u\n",
			a9_rpmb_command(f->buf->cpu, f->size), f->size >= 4);
	}
	return ret;
}

/* Keep all referenced buffers alive until TZ consumes the command/reply. */
static int patch_fds(struct qsee_file *f, const struct qseecom_ion_fd_info *fds,
		     u32 base, u32 len, bool wide, struct dma_buf **held,
		     u32 *offsets)
{
	int i;

	for (i = 0; i < MAX_ION_FD; i++) {
		struct qsee_buf *b;
		u32 off = fds[i].cmd_buf_offset;

		if (fds[i].fd <= 0)
			continue;
		if (!a9q_fd_offset(f->size, base, len, off, wide, &offsets[i]))
			return -EINVAL;
		held[i] = qsee_get_buf(fds[i].fd);
		if (IS_ERR(held[i])) {
			int ret = PTR_ERR(held[i]);

			held[i] = NULL;
			return ret;
		}
		b = held[i]->priv;
		if (!wide && upper_32_bits(b->dma)) {
			dma_buf_put(held[i]);
			held[i] = NULL;
			return -EOVERFLOW;
		}
		if (wide)
			put_unaligned_le64(b->dma, f->buf->cpu + base + off);
		else
			put_unaligned_le32(b->dma, f->buf->cpu + base + off);
		f->sginfo[i].index_flags = BIT(31) | (wide ? BIT(30) : 0) | off;
		f->sginfo[i].size = b->len;
	}
	return 0;
}

static int qsee_reply(struct qsee_file *f, unsigned int cmd, void __user *up)
{
	struct qseecom_send_modfd_listener_resp req;
	unsigned long flags;
	u32 off;
	int ret = 0, i;

	mutex_lock(&f->lock);
	spin_lock_irqsave(&f->state_lock, flags);
	if (!f->registered || f->abort || !f->pending || !f->delivered ||
	    f->replied)
		ret = -EINVAL;
	spin_unlock_irqrestore(&f->state_lock, flags);
	if (ret)
		goto out;
	if (cmd != QSEECOM_IOCTL_SEND_RESP_REQ) {
		if (copy_from_user(&req, up, sizeof(req))) {
			ret = -EFAULT;
			goto out;
		}
		if (!qsee_range(f, req.resp_buf_ptr, req.resp_len, &off)) {
			ret = -EINVAL;
			goto out;
		}
		f->reply_wide = cmd == QSEECOM_IOCTL_SEND_MODFD_RESP_64;
		ret = patch_fds(f, req.ifd_data, off, req.resp_len,
				f->reply_wide, f->reply_bufs, f->reply_offsets);
		if (ret) {
			reply_cleanup(f);
			goto out;
		}
		/* Listener whitelist offsets are relative to its shared-buffer base. */
		for (i = 0; i < MAX_ION_FD; i++)
			if (f->reply_bufs[i])
				f->sginfo[i].index_flags += off;
	}
	dma_wmb();
	spin_lock_irqsave(&f->state_lock, flags);
	f->replied = true;
	spin_unlock_irqrestore(&f->state_lock, flags);
	wake_up_all(&f->wait);
out:
	mutex_unlock(&f->lock);
	return ret;
}

static long qsee_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
	struct qsee_file *f = file->private_data;
	void __user *up = (void __user *)arg;
	struct qcom_scm_res r;
	u64 args[7] = {};
	int ret = 0;

	if (cmd == QSEECOM_IOCTL_RECEIVE_REQ)
		return qsee_receive(f);
	if (cmd == QSEECOM_IOCTL_SEND_RESP_REQ ||
	    cmd == QSEECOM_IOCTL_SEND_MODFD_RESP ||
	    cmd == QSEECOM_IOCTL_SEND_MODFD_RESP_64)
		return qsee_reply(f, cmd, up);
	mutex_lock(&f->lock);
	if (cmd == QSEECOM_IOCTL_UNREGISTER_LISTENER_REQ) {
		ret = qsee_unregister(f);
		goto out_file;
	}
	mutex_lock(&transaction_lock);
	switch (cmd) {
	case QSEECOM_IOCTL_GET_QSEOS_VERSION_REQ: {
		/*
		 * This is the Android ABI version, not the secure OS feature version.
		 * Downstream QSEOS_VERSION_14 == 0x14.
		 */
		struct qseecom_qseos_version_req v = { .qseos_version = 0x14 };

		ret = copy_to_user(up, &v, sizeof(v)) ? -EFAULT : 0;
		break;
	}
	case QSEECOM_IOCTL_SET_MEM_PARAM_REQ: {
		struct qseecom_set_sb_mem_param_req req;

		if (copy_from_user(&req, up, sizeof(req))) {
			ret = -EFAULT;
			break;
		}
		if (f->registered) {
			ret = -EINVAL;
			break;
		}
		ret = qsee_bind(f, req.ifd_data_fd,
				(unsigned long)req.virt_sb_base, req.sb_len);
		break;
	}
	case QSEECOM_IOCTL_REGISTER_LISTENER_REQ: {
		struct qseecom_register_listener_req req;
		struct qsee_file *other;

		if (copy_from_user(&req, up, sizeof(req))) {
			ret = -EFAULT;
			break;
		}
		if (f->registered || f->app_id || !req.listener_id ||
		    f->abort) {
			ret = -EINVAL;
			if (req.listener_id == 0x2000)
				pr_info_ratelimited("A9_RPMB_REGISTER phase=1 rc=%d\n", ret);
			break;
		}
		mutex_lock(&listeners_lock);
		list_for_each_entry(other, &listeners, node)
			if (other->listener_id == req.listener_id) {
				ret = -EBUSY;
				break;
			}
		mutex_unlock(&listeners_lock);
		if (ret) {
			if (req.listener_id == 0x2000)
				pr_info_ratelimited("A9_RPMB_REGISTER phase=2 rc=%d\n", ret);
			break;
		}
		ret = qsee_bind(f, req.ifd_data_fd,
				(unsigned long)req.virt_sb_base, req.sb_size);
		if (ret) {
			if (req.listener_id == 0x2000)
				pr_info_ratelimited("A9_RPMB_REGISTER phase=3 rc=%d\n", ret);
			break;
		}
		ret = qsee_register_listener_scm(f, req.listener_id, &r);
		if (req.listener_id == 0x2000)
			pr_info_ratelimited("A9_RPMB_REGISTER phase=4 rc=%d\n", ret);
		if (!ret) {
			ret = finish_call(&r, 0);
			if (req.listener_id == 0x2000)
				pr_info_ratelimited("A9_RPMB_REGISTER phase=5 rc=%d\n", ret);
		}
		if (ret) {
			dma_buf_put(f->dbuf);
			f->dbuf = NULL;
			f->buf = NULL;
			break;
		}
		f->listener_id = req.listener_id;
		mutex_lock(&listeners_lock);
		WRITE_ONCE(f->registered, true);
		list_add_tail(&f->node, &listeners);
		mutex_unlock(&listeners_lock);
		if (req.listener_id == 0x2000)
			pr_info_ratelimited("A9_RPMB_REGISTER phase=6 rc=0\n");
		break;
	}
	case QSEECOM_IOCTL_APP_LOADED_QUERY_REQ: {
		struct qseecom_qseos_app_load_query req;
		void *name;
		struct qsee_buf *query;

		if (copy_from_user(&req, up, sizeof(req))) {
			ret = -EFAULT;
			break;
		}
		if (f->registered || f->app_id ||
		    !memchr(req.app_name, 0, sizeof(req.app_name))) {
			ret = -EINVAL;
			break;
		}
		query = qsee_shared_alloc(64);
		if (IS_ERR(query)) {
			ret = PTR_ERR(query);
			break;
		}
		name = query->cpu;
		memcpy(name, req.app_name, 64);
		args[0] = query->dma;
		args[1] = strnlen(name, 64);
		ret = scm(QSEE_OWNER_OS, QSEE_APP_MGR, 3,
			  QCOM_SCM_ARGS(2, QCOM_SCM_RW, QCOM_SCM_VAL), args,
			  &r);
		qsee_shared_free(query);
		if (ret)
			break;
		/* A missing app is normal: userspace follows with LOAD_APP. */
		if (r.result[0] == U32_MAX || !r.result[2])
			break;
		ret = finish_call(&r, 0);
		if (ret)
			break;
		if (r.result[1] != QSEE_APP_ID) {
			ret = -EPROTO;
			break;
		}
		ret = app_attach(f, r.result[2], 0, req.app_name);
		if (ret)
			break;
		req.app_id = f->app_id;
		req.app_arch = f->app_arch;
		if (copy_to_user(up, &req, sizeof(req))) {
			app_unload(f);
			ret = -EFAULT;
			break;
		}
		ret = -EEXIST; /* Stock libQSEEComAPI expects errno EEXIST. */
		break;
	}
	case QSEECOM_IOCTL_LOAD_APP_REQ:
	case QSEECOM_IOCTL_LOAD_EXTERNAL_ELF_REQ: {
		struct qseecom_load_img_req req;
		struct dma_buf *d;
		struct qsee_buf *b, *image;
		u32 extent;
		bool external = cmd == QSEECOM_IOCTL_LOAD_EXTERNAL_ELF_REQ;

		if (copy_from_user(&req, up, sizeof(req))) {
			ret = -EFAULT;
			break;
		}
		if (f->registered || f->app_id ||
		    !a9q_load_extent(req.mdt_len, req.img_len, QSEE_MAX_ALLOC, &extent) ||
		    (req.app_arch != 1 && req.app_arch != 2) ||
		    !memchr(req.img_name, 0, sizeof(req.img_name))) {
			pr_warn_ratelimited("qseecom: LOAD rejected: state/length/architecture/name\n");
			ret = -EINVAL;
			break;
		}
		d = qsee_get_buf(req.ifd_data_fd);
		if (IS_ERR(d)) {
			ret = PTR_ERR(d);
			pr_warn_ratelimited("qseecom: LOAD buffer import failed: %d\n", ret);
			break;
		}
		b = d->priv;
		if (req.img_len > b->len) {
			pr_warn_ratelimited("qseecom: LOAD image exceeds buffer: %u > %zu\n",
					    req.img_len, b->len);
			dma_buf_put(d);
			ret = -EINVAL;
			break;
		}
		/*
		 * Stock TZ APP_START (0x100cdc14) checks [PA, PA + MDT + IMG)
		 * before parsing; its range validator rejects missing SHM coverage
		 * with -47. Stock libQSEEComAPI puts the complete concatenation in
		 * img_len, but stock shares the entire 16 MiB TA CMA heap. Sharing
		 * only the imported allocation leaves the additional MDT uncovered.
		 * Keep the stock bytes/lengths unchanged and share an owned, zeroed
		 * staging extent instead. Never expose adjacent allocations to TZ.
		 */
		image = qsee_shared_alloc(extent);
		if (IS_ERR(image)) {
			ret = PTR_ERR(image);
			dma_buf_put(d);
			break;
		}
		memcpy(image->cpu, b->cpu, req.img_len);
		args[0] = req.mdt_len;
		args[1] = req.img_len;
		args[2] = image->dma;
		ret = scm(QSEE_OWNER_OS, external ? 3 : QSEE_APP_MGR, 1,
			  QCOM_SCM_ARGS(3), args, &r);
		pr_info_ratelimited("qseecom: LOAD %s arch=%u mdt=%u image=%u validated=%u allocated=%zu shared=%u scm=%d result=%llu type=%llx data=%llx\n",
				    req.img_name, req.app_arch, req.mdt_len,
				    req.img_len, extent, image->len, !!image->bridge,
				    ret, r.result[0], r.result[1], r.result[2]);
		if (!ret)
			ret = finish_call(&r, 0);
		pr_info_ratelimited("qseecom: LOAD %s completion=%d result=%llu type=%llx data=%llx\n",
				    req.img_name, ret, r.result[0], r.result[1], r.result[2]);
		qsee_shared_free(image);
		dma_buf_put(d);
		if (ret || external)
			break;
		if (r.result[1] != QSEE_APP_ID) {
			ret = -EPROTO;
			break;
		}
		ret = app_attach(f, r.result[2], req.app_arch, req.img_name);
		if (ret)
			break;
		req.app_id = f->app_id;
		if (copy_to_user(up, &req, sizeof(req))) {
			app_unload(f);
			ret = -EFAULT;
		}
		break;
	}
	case QSEECOM_IOCTL_UNLOAD_APP_REQ:
		ret = app_unload(f);
		break;
	case QSEECOM_IOCTL_UNLOAD_EXTERNAL_ELF_REQ:
		ret = scm(QSEE_OWNER_OS, 3, 2, QCOM_SCM_ARGS(0), args, &r);
		if (!ret)
			ret = finish_call(&r, 0);
		break;
	case QSEECOM_IOCTL_SEND_CMD_REQ:
	case QSEECOM_IOCTL_SEND_MODFD_CMD_REQ:
	case QSEECOM_IOCTL_SEND_MODFD_CMD_64_REQ: {
		struct qseecom_send_modfd_cmd_req req = {};
		struct dma_buf *held[MAX_ION_FD] = {};
		u32 offsets[MAX_ION_FD] = {}, in, out;
		bool modified = cmd != QSEECOM_IOCTL_SEND_CMD_REQ;
		bool wide = cmd == QSEECOM_IOCTL_SEND_MODFD_CMD_64_REQ;
		struct qsee_app *app;
		unsigned long flags;
		unsigned int km_context;
		int i;

		if (copy_from_user(
			    &req, up,
			    modified ? sizeof(req) :
				       sizeof(struct qseecom_send_cmd_req))) {
			ret = -EFAULT;
			break;
		}
		if (!f->app_id || f->registered ||
		    !qsee_range(f, req.cmd_req_buf, req.cmd_req_len, &in) ||
		    !qsee_range(f, req.resp_buf, req.resp_len, &out) ||
		    req.cmd_req_len > f->size - req.resp_len) {
			ret = -EINVAL;
			break;
		}
		if (modified) {
			ret = patch_fds(f, req.ifd_data, in, req.cmd_req_len,
					wide, held, offsets);
			if (ret)
				goto send_done;
		}
		app = app_find(f->app_id);
		km_context = a9_kmtb_context(app && app->keymaster,
			req.cmd_req_len >= 4 ? get_unaligned_le32(f->buf->cpu + in) : 0);
		spin_lock_irqsave(&rpmb_map_lock, flags);
		a9_kmtb_begin(&rpmb_kmtb, km_context);
		rpmb_public_context = a9_rpmb_public_context(app && app->keymaster,
			req.cmd_req_len >= 4 ? get_unaligned_le32(f->buf->cpu + in) : 0);
		spin_unlock_irqrestore(&rpmb_map_lock, flags);
		args[0] = f->app_id;
		args[1] = f->buf->dma + in;
		args[2] = req.cmd_req_len;
		args[3] = f->buf->dma + out;
		args[4] = req.resp_len;
		args[5] = f->sg_dma;
		args[6] = MAX_ION_FD * sizeof(*f->sginfo);
		ret = scm(QSEE_OWNER_APP, 0, whitelist ? 6 : 1,
			  whitelist ? QCOM_SCM_ARGS(7, QCOM_SCM_VAL,
						    QCOM_SCM_RW, QCOM_SCM_VAL,
						    QCOM_SCM_RW, QCOM_SCM_VAL,
						    QCOM_SCM_RW, QCOM_SCM_VAL) :
				      QCOM_SCM_ARGS(5, QCOM_SCM_VAL,
						    QCOM_SCM_RW, QCOM_SCM_VAL,
						    QCOM_SCM_RW, QCOM_SCM_VAL),
			  args, &r);
		if (!ret)
			ret = finish_call(&r, f->app_id);
		dma_rmb();
		spin_lock_irqsave(&rpmb_map_lock, flags);
		if (ret)
			rpmb_kmtb.valid = false;
		a9_kmtb_end(&rpmb_kmtb, !ret && req.resp_len >= 4 &&
			!get_unaligned_le32(f->buf->cpu + out));
		rpmb_public_context = 0;
		spin_unlock_irqrestore(&rpmb_map_lock, flags);
send_done:
		for (i = 0; i < MAX_ION_FD; i++) {
			if (!held[i])
				continue;
			if (wide)
				put_unaligned_le64(0, f->buf->cpu + offsets[i]);
			else
				put_unaligned_le32(0, f->buf->cpu + offsets[i]);
			dma_buf_put(held[i]);
		}
		memset(f->sginfo, 0, MAX_ION_FD * sizeof(*f->sginfo));
		break;
	}
	case QSEECOM_IOCTL_FBE_CLEAR_KEY: {
		/*
		 * Stock A9 qseecom_ioctl at 0xffffff80087b01e0 copies 72 bytes
		 * and returns zero at ...87b0244. It makes no secure/keyslot call.
		 * This obsolete legacy ABI does not evict blk-crypto keys.
		 */
		struct qseecom_ice_key_data_t req;

		ret = copy_from_user(&req, up, sizeof(req)) ? -EFAULT : 0;
		memzero_explicit(&req, sizeof(req));
		break;
	}
	case QSEECOM_IOCTL_PERF_ENABLE_REQ:
	case QSEECOM_IOCTL_PERF_DISABLE_REQ:
		/*
		 * SM6115 stock DT supplies no independently controlled QSEE clocks.
		 * Performance votes are advisory;
		 * secure calls still run normally.
		 */
		break;
	case QSEECOM_IOCTL_SET_BUS_SCALING_REQ: {
		u32 mode;

		if (copy_from_user(&mode, up, sizeof(mode)))
			ret = -EFAULT;
		else if (mode > HIGH)
			ret = -EINVAL;
		break;
	}
	default:
		ret = -ENOTTY;
	}
	mutex_unlock(&transaction_lock);
out_file:
	mutex_unlock(&f->lock);
	return ret;
}

static int qsee_open(struct inode *inode, struct file *file)
{
	struct qsee_file *f = kzalloc_obj(*f);

	if (!f)
		return -ENOMEM;
	mutex_init(&f->lock);
	kref_init(&f->ref);
	INIT_LIST_HEAD(&f->node);
	init_waitqueue_head(&f->wait);
	spin_lock_init(&f->state_lock);
	f->sgbuf = qsee_shared_alloc(MAX_ION_FD * sizeof(*f->sginfo));
	if (IS_ERR(f->sgbuf)) {
		int ret = PTR_ERR(f->sgbuf);

		kfree(f);
		return ret;
	}
	f->sginfo = f->sgbuf->cpu;
	f->sg_dma = f->sgbuf->dma;
	file->private_data = f;
	return nonseekable_open(inode, file);
}
static int qsee_release(struct inode *inode, struct file *file)
{
	struct qsee_file *f = file->private_data;

	mutex_lock(&f->lock);
	if (f->registered && qsee_unregister(f)) {
		/*
		 * Secure world may still own this buffer. Deliberately retain one
		 * reference rather than returning live shared pages to the allocator.
		 */
		pr_err("qseecom: listener deregistration failed; buffer quarantined\n");
		mutex_unlock(&f->lock);
		return 0;
	}
	mutex_lock(&transaction_lock);
	if (f->app_id && app_unload(f))
		pr_err("qseecom: app unload failed\n");
	mutex_unlock(&transaction_lock);
	mutex_unlock(&f->lock);
	kref_put(&f->ref, qsee_file_free);
	return 0;
}
static const struct file_operations qsee_fops = {
	.owner = THIS_MODULE,
	.open = qsee_open,
	.release = qsee_release,
	.unlocked_ioctl = qsee_ioctl,
	.llseek = noop_llseek,
};
static struct miscdevice qsee_misc = {
	.minor = MISC_DYNAMIC_MINOR,
	.name = "qseecom",
	.fops = &qsee_fops,
	.mode = 0600,
};
static int __init qsee_init(void)
{
	struct platform_device *pdev;
	struct qcom_scm_res r;
	u64 args[] = { 15 };
	int ret;

	if (!of_machine_is_compatible("hisense,hlte556n"))
		return -ENODEV;
	if (!qcom_scm_is_available())
		return -EPROBE_DEFER;
	pdev = platform_device_register_simple("a9-qseecom", -1, NULL, 0);
	if (IS_ERR(pdev))
		return PTR_ERR(pdev);
	qsee_dev = &pdev->dev;
	ret = dma_coerce_mask_and_coherent(qsee_dev, DMA_BIT_MASK(32));
	if (ret)
		goto fail;
	/* Validate that the QSEE feature exists before exposing either device. */
	args[0] = 10;
	ret = scm(2, 6, 3, QCOM_SCM_ARGS(1), args, &r);
	if (ret || !r.result[0] || r.result[0] == U32_MAX) {
		if (!ret)
			ret = -ENODEV;
		goto fail;
	}
	args[0] = 15;
	/* Downstream FEATURE_ID_WHITELIST=15 via SIP INFO GET_FEAT_VERSION. */
	ret = scm(2, 6, 3, QCOM_SCM_ARGS(1), args, &r);
	whitelist = !ret && r.result[0] >= 0x400000 && r.result[0] != U32_MAX;
	ret = misc_register(&ion_misc);
	if (ret)
		goto fail;
	ret = misc_register(&qsee_misc);
	if (ret) {
		misc_deregister(&ion_misc);
		goto fail;
	}
	pr_info("qseecom: Android transport ready (native arm64, whitelist %u)\n",
		whitelist);
	a9_qsee_log_init();
	return 0;
fail:
	platform_device_unregister(pdev);
	return ret;
}
late_initcall(qsee_init);
MODULE_DESCRIPTION(
	"SM6115 Android QSEECom and QSEE ION compatibility transport");
MODULE_LICENSE("GPL");
MODULE_IMPORT_NS("DMA_BUF");
