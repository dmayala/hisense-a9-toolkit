/* SPDX-License-Identifier: GPL-2.0-only */
#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <assert.h>
#include <linux/dma-buf.h>
#include "../driver/qseecom-uapi.h"
#include "../driver/ion-uapi.h"
#define SIZE(s, n) _Static_assert(sizeof(struct s) == (n), #s)
#define OFF(s, f, n) _Static_assert(offsetof(struct s, f) == (n), #s "." #f)
#define IOC(n, v) _Static_assert((n) == (v), #n)
_Static_assert(sizeof(void *) == 8, "native ARM64 LP64 ABI");
SIZE(qseecom_register_listener_req, 24);
OFF(qseecom_register_listener_req, ifd_data_fd, 4);
OFF(qseecom_register_listener_req, virt_sb_base, 8);
OFF(qseecom_register_listener_req, sb_size, 16);
SIZE(qseecom_send_cmd_req, 32);
OFF(qseecom_send_cmd_req, cmd_req_len, 8);
OFF(qseecom_send_cmd_req, resp_buf, 16);
OFF(qseecom_send_cmd_req, resp_len, 24);
SIZE(qseecom_send_modfd_cmd_req, 64);
OFF(qseecom_send_modfd_cmd_req, ifd_data, 28);
SIZE(qseecom_send_modfd_listener_resp, 48);
OFF(qseecom_send_modfd_listener_resp, ifd_data, 12);
SIZE(qseecom_set_sb_mem_param_req, 24);
OFF(qseecom_set_sb_mem_param_req, virt_sb_base, 8);
OFF(qseecom_set_sb_mem_param_req, sb_len, 16);
SIZE(qseecom_load_img_req, 84);
OFF(qseecom_load_img_req, img_name, 12);
OFF(qseecom_load_img_req, app_arch, 76);
OFF(qseecom_load_img_req, app_id, 80);
SIZE(qseecom_qseos_app_load_query, 72);
OFF(qseecom_qseos_app_load_query, app_id, 64);
OFF(qseecom_qseos_app_load_query, app_arch, 68);
SIZE(qseecom_ice_key_data_t, 72);
OFF(qseecom_ice_key_data_t, key_len, 32);
OFF(qseecom_ice_key_data_t, salt, 36);
OFF(qseecom_ice_key_data_t, salt_len, 68);
SIZE(ion_allocation_data, 24);
OFF(ion_allocation_data, heap_id_mask, 8);
OFF(ion_allocation_data, flags, 12);
OFF(ion_allocation_data, fd, 16);
SIZE(ion_heap_data, 52);
SIZE(ion_heap_query, 24);
OFF(ion_heap_query, heaps, 8);
IOC(QSEECOM_IOCTL_REGISTER_LISTENER_REQ, 0xc0189701UL);
IOC(QSEECOM_IOCTL_UNREGISTER_LISTENER_REQ, 0x9702UL);
IOC(QSEECOM_IOCTL_SEND_CMD_REQ, 0xc0209703UL);
IOC(QSEECOM_IOCTL_SEND_MODFD_CMD_REQ, 0xc0409704UL);
IOC(QSEECOM_IOCTL_RECEIVE_REQ, 0x9705UL);
IOC(QSEECOM_IOCTL_SEND_RESP_REQ, 0x9706UL);
IOC(QSEECOM_IOCTL_LOAD_APP_REQ, 0xc0549707UL);
IOC(QSEECOM_IOCTL_SET_MEM_PARAM_REQ, 0xc0189708UL);
IOC(QSEECOM_IOCTL_UNLOAD_APP_REQ, 0x9709UL);
IOC(QSEECOM_IOCTL_GET_QSEOS_VERSION_REQ, 0xc004970aUL);
IOC(QSEECOM_IOCTL_PERF_ENABLE_REQ, 0x970bUL);
IOC(QSEECOM_IOCTL_PERF_DISABLE_REQ, 0x970cUL);
IOC(QSEECOM_IOCTL_LOAD_EXTERNAL_ELF_REQ, 0xc054970dUL);
IOC(QSEECOM_IOCTL_UNLOAD_EXTERNAL_ELF_REQ, 0x970eUL);
IOC(QSEECOM_IOCTL_APP_LOADED_QUERY_REQ, 0xc048970fUL);
IOC(QSEECOM_IOCTL_SEND_MODFD_RESP, 0xc0309715UL);
IOC(QSEECOM_IOCTL_SET_BUS_SCALING_REQ, 0xc0049717UL);
IOC(QSEECOM_IOCTL_SEND_MODFD_CMD_64_REQ, 0xc0409723UL);
IOC(QSEECOM_IOCTL_SEND_MODFD_RESP_64, 0xc0309724UL);
IOC(QSEECOM_IOCTL_FBE_CLEAR_KEY, 0xc048972cUL);
IOC(_IOWR('I', 1, uint32_t), 0xc0044901UL);
IOC(DMA_BUF_IOCTL_SYNC, 0x40086200UL);
IOC(ION_IOC_ALLOC, 0xc0184900UL);
IOC(ION_IOC_HEAP_QUERY, 0xc0184908UL);
/* Fixtures use byte offsets recovered from the stock libQSEEComAPI
 * instructions, not serialization through another copy of these structs. */
static void u32(unsigned char *p, uint32_t n)
{
	memcpy(p, &n, 4);
}
static void u64(unsigned char *p, uint64_t n)
{
	memcpy(p, &n, 8);
}
int main(void)
{
	unsigned char expected[84] = { 0 };
	struct qseecom_register_listener_req l = { 0 };
	struct qseecom_send_modfd_cmd_req c = { 0 };
	struct qseecom_send_modfd_listener_resp r = { 0 };
	struct qseecom_load_img_req a = { 0 };
	struct qseecom_set_sb_mem_param_req m = { 0 };
	l.listener_id = 0x2000;
	l.ifd_data_fd = 23;
	l.virt_sb_base = (void *)0x1234567800UL;
	l.sb_size = 0x6400;
	u32(expected, 0x2000);
	u32(expected + 4, 23);
	u64(expected + 8, 0x1234567800UL);
	u32(expected + 16, 0x6400);
	assert(!memcmp(expected, &l, sizeof(l)));
	memset(expected, 0, sizeof(expected));
	c.cmd_req_buf = (void *)0x1234567800UL;
	c.cmd_req_len = 0x80;
	c.resp_buf = (void *)0x1234567880UL;
	c.resp_len = 0x100;
	c.ifd_data[0].fd = 9;
	c.ifd_data[0].cmd_buf_offset = 4;
	u64(expected, 0x1234567800UL);
	u32(expected + 8, 0x80);
	u64(expected + 16, 0x1234567880UL);
	u32(expected + 24, 0x100);
	u32(expected + 28, 9);
	u32(expected + 32, 4);
	assert(!memcmp(expected, &c, sizeof(c)));
	memset(expected, 0, sizeof(expected));
	r.resp_buf_ptr = (void *)0x1234567800UL;
	r.resp_len = 0x100;
	r.ifd_data[0].fd = 10;
	r.ifd_data[0].cmd_buf_offset = 8;
	u64(expected, 0x1234567800UL);
	u32(expected + 8, 0x100);
	u32(expected + 12, 10);
	u32(expected + 16, 8);
	assert(!memcmp(expected, &r, sizeof(r)));
	memset(expected, 0, sizeof(expected));
	a.mdt_len = 0x1000;
	a.img_len = 0x20000;
	a.ifd_data_fd = 7;
	memcpy(a.img_name, "keymaster64", 12);
	a.app_arch = 2;
	a.app_id = 3;
	u32(expected, 0x1000);
	u32(expected + 4, 0x20000);
	u32(expected + 8, 7);
	memcpy(expected + 12, "keymaster64", 12);
	u32(expected + 76, 2);
	u32(expected + 80, 3);
	assert(!memcmp(expected, &a, sizeof(a)));
	memset(expected, 0, sizeof(expected));
	m.ifd_data_fd = 11;
	m.virt_sb_base = (void *)0x1234567800UL;
	m.sb_len = 0xa080;
	u32(expected, 11);
	u64(expected + 8, 0x1234567800UL);
	u32(expected + 16, 0xa080);
	assert(!memcmp(expected, &m, sizeof(m)));
	puts("PASS: 24 ioctl values, LP64 layouts/offsets, five stock byte fixtures");
	return 0;
}
