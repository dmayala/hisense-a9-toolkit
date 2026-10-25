/* SPDX-License-Identifier: GPL-2.0-only */
/* Pure read-first policy. No request/response data may be logged here. */
#ifndef A9_RPMB_READ_POLICY_H
#define A9_RPMB_READ_POLICY_H

enum a9_rpmb_direction { A9_RPMB_NONE, A9_RPMB_IN, A9_RPMB_OUT, A9_RPMB_BAD_DIR };

/* Public classification only. Never expose unknown packet bytes as a value. */
static inline unsigned int a9_rpmb_public_request(const unsigned char *frame,
		unsigned int frame_bytes)
{
	unsigned int request;

	if (!frame || frame_bytes != 512)
		return 0;
	request = ((unsigned int)frame[510] << 8) | frame[511];
	return request >= 1 && request <= 5 ? request : 0;
}

static inline int a9_rpmb_length_matches(const unsigned char *cdb,
		unsigned int cdb_size, unsigned int bytes)
{
	unsigned long long declared;

	if (!cdb || cdb_size != 12 || !bytes)
		return 0;
	declared = ((unsigned long long)cdb[6] << 24) |
		   ((unsigned int)cdb[7] << 16) |
		   ((unsigned int)cdb[8] << 8) | cdb[9];
	if (cdb[4] & 0x80)
		declared *= 512;
	return declared == bytes;
}

/* Shared strict shape check before any optional GK data-write allowance. */
static inline int a9_rpmb_security_out_shape(const unsigned char *cdb,
		unsigned int cdb_size, enum a9_rpmb_direction dir, unsigned int bytes)
{
	return cdb && cdb_size == 12 && cdb[0] == 0xb5 && cdb[1] == 0xec &&
	       !cdb[2] && cdb[3] == 1 && !(cdb[4] & 0x7f) && !cdb[5] &&
	       !cdb[10] && !cdb[11] && dir == A9_RPMB_OUT &&
	       bytes && !(bytes % 512) && a9_rpmb_length_matches(cdb, cdb_size, bytes);
}

/* Zero allows a read/control operation. Positive values are public reasons. */
static inline int a9_rpmb_read_policy(const unsigned char *cdb,
		unsigned int cdb_size, enum a9_rpmb_direction dir,
		unsigned int bytes, const unsigned char *frame,
		unsigned int frame_bytes)
{
	unsigned long long declared;
	unsigned int request;

	if (!cdb || !cdb_size)
		return 1;
	/* SCSI discovery, sense and LU power control; none programs RPMB data. */
	switch (cdb[0]) {
	case 0x00: /* TEST UNIT READY */
	case 0x1b: /* START STOP UNIT */
		return dir == A9_RPMB_NONE && !bytes ? 0 : 3;
	case 0x03: /* REQUEST SENSE */
	case 0x12: /* INQUIRY */
	case 0x1a: /* MODE SENSE(6) */
	case 0x25: /* READ CAPACITY(10) */
	case 0x5a: /* MODE SENSE(10) */
	case 0xa0: /* REPORT LUNS */
		return dir == A9_RPMB_IN ? 0 : 3;
	case 0xa2: /* SECURITY PROTOCOL IN */
	case 0xb5: /* SECURITY PROTOCOL OUT */
		break;
	default:
		return 1;
	}
	if (cdb_size != 12 || cdb[1] != 0xec || cdb[2] || cdb[3] != 1 ||
	    (cdb[4] & 0x7f) || cdb[5] || cdb[10] || cdb[11])
		return 2;
	declared = ((unsigned long long)cdb[6] << 24) |
		   ((unsigned int)cdb[7] << 16) |
		   ((unsigned int)cdb[8] << 8) | cdb[9];
	if (cdb[4] & 0x80)
		declared *= 512;
	if (!bytes || bytes % 512 || declared != bytes)
		return 3;
	if (cdb[0] == 0xa2)
		return dir == A9_RPMB_IN ? 0 : 3;
	/* A read needs one outbound request frame. Reject every other OUT form,
	 * including PROGRAM_KEY(1) and WRITE_DATA(3). RESULT_READ(5) is a
	 * nonmutating response request, needed after an allowed GK write.
	 */
	if (dir != A9_RPMB_OUT || bytes != 512 || !frame || frame_bytes != 512)
		return 3;
	request = ((unsigned int)frame[510] << 8) | frame[511];
	return request == 2 || request == 4 || request == 5 ? 0 : 4;
}
#endif
