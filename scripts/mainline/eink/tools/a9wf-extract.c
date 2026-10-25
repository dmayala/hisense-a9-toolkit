/* a9wf-extract: build /lib/firmware/hisense/a9-waveform.bin for the a9_eink
 * kernel driver from the user's OWN composer binary.
 *
 *   a9wf-extract COMPOSER OUT.bin
 *
 * COMPOSER = vendor.qti.hardware.display.composer-service from the phone's
 * /vendor (sha256 8c89557b..., checked; any other build is refused because
 * the table offsets are only known for that one). The output is
 *   struct a9e_wf_header (80 bytes: "A9WF", version 1, sizes, the composer's
 *   sha256, the payload's sha256) + struct a9t_tables (the raw tables).
 * The output contains vendor data: keep it on the device / your own disk,
 * never commit or redistribute it (docs/mainline-eink-driver.md).
 */
#include "a9_eink_engine.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void put32(uint8_t *p, uint32_t v)
{
	p[0] = (uint8_t)v, p[1] = (uint8_t)(v >> 8), p[2] = (uint8_t)(v >> 16), p[3] = (uint8_t)(v >> 24);
}

int main(int argc, char **argv)
{
	if (argc != 3) {
		fprintf(stderr, "usage: %s COMPOSER OUT.bin\n", argv[0]);
		return 2;
	}
	FILE *f = fopen(argv[1], "rb");
	if (!f) {
		perror(argv[1]);
		return 2;
	}
	fseek(f, 0, SEEK_END);
	long n = ftell(f);
	fseek(f, 0, SEEK_SET);
	uint8_t *bin = malloc((size_t)n);
	if (!bin || fread(bin, 1, (size_t)n, f) != (size_t)n) {
		fprintf(stderr, "%s: read failed\n", argv[1]);
		return 2;
	}
	fclose(f);

	size_t total = sizeof(struct a9e_wf_header) + sizeof(struct a9t_tables);
	uint8_t *out = calloc(1, total);
	struct a9t_tables *t = (struct a9t_tables *)(out + sizeof(struct a9e_wf_header));
	if (!out || a9t_load(t, bin, (size_t)n)) {
		fprintf(stderr, "%s: not the supported composer (size or sha256 %s mismatch)\n", argv[1],
			A9T_COMPOSER_SHA256);
		return 1;
	}
	put32(out, A9E_WF_MAGIC);
	put32(out + 4, A9E_WF_VERSION);
	put32(out + 8, (uint32_t)sizeof(struct a9e_wf_header));
	put32(out + 12, (uint32_t)sizeof(struct a9t_tables));
	a9t_sha256(bin, (size_t)n, out + 16);
	a9t_sha256((const uint8_t *)t, sizeof(*t), out + 48);
	free(bin);

	int rc = a9e_wf_check(out, total, NULL);
	if (rc) {
		fprintf(stderr, "table check failed (%d); not writing %s\n", rc, argv[2]);
		return 1;
	}
	f = fopen(argv[2], "wb");
	if (!f || fwrite(out, 1, total, f) != total || fclose(f)) {
		perror(argv[2]);
		return 2;
	}
	printf("%s: %zu bytes, payload sha256 ", argv[2], total);
	for (int i = 0; i < 32; i++)
		printf("%02x", out[48 + i]);
	printf("\n");
	return 0;
}
