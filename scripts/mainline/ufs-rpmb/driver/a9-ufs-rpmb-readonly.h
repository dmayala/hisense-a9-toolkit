/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef A9_UFS_RPMB_READONLY_H
#define A9_UFS_RPMB_READONLY_H
#include "a9-rpmb-read-policy.h"
#if IS_BUILTIN(CONFIG_QCOM_QSEECOM_ANDROID) && IS_BUILTIN(CONFIG_SCSI_UFSHCD)
bool a9_qsee_rpmb_data_write_allowed(struct scsi_cmnd *cmd);
#endif

/* Each callsite has its own bounded budget. Discovery must not hide reads or
 * refusals; these records describe submission policy, never I/O success.
 */
static void a9_ufs_rpmb_log(bool allowed, unsigned int operation, int reason)
{
	if (!allowed)
		pr_info_ratelimited("A9_RPMB_GUARD mode=2 allowed=%u operation=%u reason=%d\n",
				    allowed, operation, reason);
	else if (operation == 1)
		pr_info_ratelimited("A9_RPMB_GUARD mode=2 allowed=%u operation=%u reason=%d\n",
				    allowed, operation, reason);
	else if (operation == 2 || operation == 3)
		pr_info_ratelimited("A9_RPMB_GUARD mode=2 allowed=%u operation=%u reason=%d\n",
				    allowed, operation, reason);
	else if (operation == 5 || operation == 6)
		pr_info_ratelimited("A9_RPMB_GUARD mode=2 allowed=%u operation=%u reason=%d\n",
				    allowed, operation, reason);
	else
		pr_info_ratelimited("A9_RPMB_GUARD mode=2 allowed=%u operation=%u reason=%d\n",
				    allowed, operation, reason);
}

static bool a9_ufs_rpmb_cmd_readonly(struct scsi_cmnd *cmd)
{
	u8 frame[512] = { 0 };
	unsigned int copied = 0, operation = 0;
	unsigned int request, bytes = scsi_bufflen(cmd), opcode = 0;
	bool bytes_valid = bytes && !(bytes % sizeof(frame));
	enum a9_rpmb_direction dir = A9_RPMB_BAD_DIR;
	int reason;

	if (cmd->sc_data_direction == DMA_NONE)
		dir = A9_RPMB_NONE;
	else if (cmd->sc_data_direction == DMA_FROM_DEVICE)
		dir = A9_RPMB_IN;
	else if (cmd->sc_data_direction == DMA_TO_DEVICE)
		dir = A9_RPMB_OUT;
	if (cmd->cmd_len && cmd->cmnd[0] == 0xa2)
		opcode = 1;
	else if (cmd->cmd_len && cmd->cmnd[0] == 0xb5)
		opcode = 2;
	/* First frame for diagnostics; a permitted data write must additionally
	 * pass inspection of EVERY frame by the bounded partition policy.
	 */
	if (opcode == 2 && dir == A9_RPMB_OUT && bytes_valid)
		copied = scsi_sg_copy_to_buffer(cmd, frame, sizeof(frame));
	reason = a9_rpmb_read_policy(cmd->cmnd, cmd->cmd_len, dir,
			scsi_bufflen(cmd), copied ? frame : NULL, copied);
#if IS_BUILTIN(CONFIG_QCOM_QSEECOM_ANDROID) && IS_BUILTIN(CONFIG_SCSI_UFSHCD)
	if (reason && a9_rpmb_security_out_shape(cmd->cmnd, cmd->cmd_len, dir, bytes) &&
	    a9_qsee_rpmb_data_write_allowed(cmd)) {
		reason = 0;
		operation = 5; /* scoped GK/KMTB authenticated write submission */
	}
#endif
	if (!reason) {
		if (cmd->cmnd[0] == 0xa2)
			operation = 4; /* read response, not authenticated success */
		else if (cmd->cmnd[0] == 0xb5) {
			if (operation != 5)
				operation = frame[511] == 2 ? 2 : (frame[511] == 5 ? 6 : 3);
		}
		else
			operation = 1; /* discovery/control */
	}
	request = a9_rpmb_public_request(frame, copied);
	memzero_explicit(frame, sizeof(frame));
	a9_ufs_rpmb_log(!reason, operation, reason);
	if (reason)
		pr_info_ratelimited("A9_RPMB_REJECT reason=%d opcode=%u direction=%u bytes_valid=%u length_match=%u frames=%u frame_valid=%u request=%u\n",
			reason, opcode, (unsigned int)dir,
			(unsigned int)bytes_valid,
			(unsigned int)a9_rpmb_length_matches(cmd->cmnd, cmd->cmd_len, bytes),
			bytes_valid ? bytes / 512 : 0,
			(unsigned int)(copied == sizeof(frame)), request);
	return !reason;
}
#endif
