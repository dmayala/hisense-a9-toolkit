/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef A9_RPMB_COMMAND_H
#define A9_RPMB_COMMAND_H
/* Public diagnostic enum only: unknown=0, init=1, read=2, write=3.
 * Do not inspect a short buffer or expose other packet fields.
 */
static inline unsigned int a9_rpmb_command(const unsigned char *buf,
					 unsigned int size)
{
	unsigned int command;

	if (!buf || size < 4)
		return 0;
	command = (unsigned int)buf[0] | (unsigned int)buf[1] << 8 |
		  (unsigned int)buf[2] << 16 | (unsigned int)buf[3] << 24;
	return command >= 0x101 && command <= 0x103 ? command - 0x100 : 0;
}

/* Stock dispatch105bbc stores read/write operation status as LE signed32 at
 * offset4 before SEND_RESP. This is distinct from the response SCM/QSEE rc.
 */
static inline bool a9_rpmb_operation_result(const unsigned char *buf,
					 unsigned int size, int *operation_rc)
{
	unsigned int value;

	if (!operation_rc)
		return false;
	*operation_rc = 0;
	if (!buf || size < 8)
		return false;
	value = (unsigned int)buf[4] | (unsigned int)buf[5] << 8 |
		(unsigned int)buf[6] << 16 | (unsigned int)buf[7] << 24;
	/* Express the negative half without an out-of-range unsigned->int cast. */
	*operation_rc = value & 0x80000000U ? -1 - (int)~value : (int)value;
	return true;
}
#endif
