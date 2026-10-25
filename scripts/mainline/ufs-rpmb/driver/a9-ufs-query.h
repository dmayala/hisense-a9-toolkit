/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Read-only subset of Qualcomm msm-4.19 ufshcd_query_ioctl/ufshcd_ioctl.
 * Copyright (c) 2013-2020, The Linux Foundation. All rights reserved.
 * Adapted for mainline device-management locking and runtime PM.
 * Stock librpmb uses READ_DESC device, geometry, and per-LU unit descriptors.
 */
#ifndef A9_UFS_QUERY_H
#define A9_UFS_QUERY_H

#include "a9-ufs-query-abi.h"

static int a9_ufs_query_ioctl(struct scsi_device *sdev, unsigned int cmd,
			    void __user *buffer)
{
	struct ufs_hba *hba = shost_priv(sdev->host);
	struct ufs_ioctl_query_data query;
	u8 *desc, index = 0;
	int ret, length;
	bool length_valid = false;

	if (cmd != UFS_IOCTL_QUERY)
		return -ENOIOCTLCMD;
	if (!buffer)
		return -EINVAL;
	if (copy_from_user(&query, buffer, sizeof(query)))
		return -EFAULT;
	if (query.opcode != UPIU_QUERY_OPCODE_READ_DESC)
		return -EINVAL;
	switch (query.idn) {
	case QUERY_DESC_IDN_DEVICE:
	case QUERY_DESC_IDN_GEOMETRY:
		break;
	case QUERY_DESC_IDN_UNIT:
		index = ufshcd_scsi_to_upiu_lun(sdev->lun);
		if (!ufs_is_valid_unit_desc_lun(&hba->dev_info, index)) {
			ret = -EINVAL;
			goto out_log;
		}
		break;
	default:
		return -EINVAL;
	}
	length = min_t(int, query.buf_size, QUERY_DESC_MAX_SIZE);
	if (length < QUERY_DESC_MIN_SIZE) {
		ret = -EINVAL;
		goto out_log;
	}
	desc = kzalloc(length, GFP_KERNEL);
	if (!desc) {
		ret = -ENOMEM;
		goto out_log;
	}

	/* Resume the device WLUN, as mainline UFS BSG does, before any query. */
	ret = pm_runtime_resume_and_get(&hba->ufs_device_wlun->sdev_gendev);
	if (ret < 0)
		goto out_free;
	/* The query helper holds clocks, dev_cmd.lock and clk_scaling_lock. */
	ret = ufshcd_query_descriptor_retry(hba, UPIU_QUERY_OPCODE_READ_DESC,
					  query.idn, index, 0, desc, &length);
	ufshcd_rpm_put_sync(hba);
	if (ret) {
		if (ret > 0)
			ret = -EIO;
		goto out_free;
	}
	/* Never copy beyond the allocation, even on an invalid device reply. */
	if (length < 0 || length > min_t(int, query.buf_size, QUERY_DESC_MAX_SIZE)) {
		ret = -EIO;
		goto out_free;
	}
	length_valid = true;
	query.buf_size = length;
	if (copy_to_user(buffer, &query, sizeof(query)) ||
	    copy_to_user((u8 __user *)buffer + sizeof(query), desc, length))
		ret = -EFAULT;
out_free:
	kfree(desc);
out_log:
	/* rc includes user copy-out; length_valid describes only the returned
	 * descriptor's existing allocation-bound check, not its contents.
	 */
	pr_info_ratelimited("A9_RPMB_QUERY idn=%u rpmb_lun=%u rc=%d length_valid=%u\n",
		query.idn, ufshcd_scsi_to_upiu_lun(sdev->lun) == UFS_UPIU_RPMB_WLUN,
		ret, length_valid);
	return ret;
}

#endif
