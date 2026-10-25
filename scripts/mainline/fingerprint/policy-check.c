// SPDX-License-Identifier: GPL-2.0-only
/* Host-only libsepol decision query. Never loads policy into the kernel. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sepol/policydb/services.h>

int main(int argc, char **argv)
{
	FILE *file;
	void *image;
	long size;
	sepol_security_id_t source, target;
	sepol_security_class_t cls;
	sepol_access_vector_t perm;
	struct sepol_av_decision decision;
	int allowed;

	if (argc != 7)
		return 2;
	file = fopen(argv[1], "rb");
	if (!file || fseek(file, 0, SEEK_END))
		return 2;
	size = ftell(file);
	if (size <= 0 || fseek(file, 0, SEEK_SET))
		return 2;
	image = malloc(size);
	if (!image || fread(image, 1, size, file) != (size_t)size)
		return 2;
	fclose(file);
	if (sepol_load_policy(image, size) ||
	    sepol_context_to_sid(argv[2], strlen(argv[2]) + 1, &source) ||
	    sepol_context_to_sid(argv[3], strlen(argv[3]) + 1, &target) ||
	    sepol_string_to_security_class(argv[4], &cls) ||
	    sepol_string_to_av_perm(cls, argv[5], &perm) ||
	    sepol_compute_av(source, target, cls, perm, &decision))
		return 2;
	allowed = (decision.allowed & perm) == perm;
	printf("POLICY %s -> %s %s:%s allowed=%d expected=%s\n",
	       argv[2], argv[3], argv[4], argv[5], allowed, argv[6]);
	free(image);
	return allowed == atoi(argv[6]) ? 0 : 1;
}
