/* a9eink-pattern: draw a test pattern on the a9_eink KMS card (824x1648)
 * with raw DRM ioctls. Static, no libdrm, for the busybox initramfs.
 *
 *   a9eink-pattern [-d /dev/dri/cardN] [--hold SEC] PATTERN [ARGS]
 *     white | black | steps | ramp | checker N | box X Y W H
 *
 * Without -d it picks the card whose driver name is "a9_eink" (never the msm
 * card). One full-screen commit (SETCRTC); the driver turns it into one E Ink
 * update of the pixels that changed. --hold keeps the framebuffer alive for
 * SEC seconds (default 2) before exiting; the glass keeps its image after.
 *
 * Build: aarch64-unknown-linux-gnu-gcc -static -O2 -I<kernel>/usr/include ...
 * (uapi headers from `make headers` of the kernel build).
 */
#define _GNU_SOURCE
#include <drm/drm.h>
#include <drm/drm_fourcc.h>
#include <drm/drm_mode.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#define W 824
#define H 1648

static int xioctl(int fd, unsigned long req, void *arg)
{
	int r;
	do
		r = ioctl(fd, req, arg);
	while (r == -1 && (errno == EINTR || errno == EAGAIN));
	return r;
}

static int open_card(const char *path)
{
	char name[64], p[32];
	for (int i = 0; !path && i < 8; i++) {
		snprintf(p, sizeof(p), "/dev/dri/card%d", i);
		int fd = open(p, O_RDWR | O_CLOEXEC);
		if (fd < 0)
			continue;
		struct drm_version v = { .name = name, .name_len = sizeof(name) - 1 };
		memset(name, 0, sizeof(name));
		if (!xioctl(fd, DRM_IOCTL_VERSION, &v) && !strcmp(name, "a9_eink")) {
			fprintf(stderr, "using %s (%s)\n", p, name);
			return fd;
		}
		close(fd);
	}
	if (!path) {
		fprintf(stderr, "no a9_eink card under /dev/dri\n");
		exit(1);
	}
	int fd = open(path, O_RDWR | O_CLOEXEC);
	if (fd < 0) {
		perror(path);
		exit(1);
	}
	return fd;
}

static uint8_t grey_at(const char *pat, char **a, int x, int y)
{
	if (!strcmp(pat, "white")) return 255;
	if (!strcmp(pat, "black")) return 0;
	if (!strcmp(pat, "steps")) return (uint8_t)(17 * ((y * 16) / H));
	if (!strcmp(pat, "ramp")) return (uint8_t)((y * 255) / (H - 1));
	if (!strcmp(pat, "checker")) {
		int n = a[0] ? atoi(a[0]) : 64;
		if (n < 1) n = 1;
		return ((x / n + y / n) & 1) ? 0 : 255;
	}
	if (!strcmp(pat, "box")) {
		int bx = atoi(a[0]), by = atoi(a[1]), bw = atoi(a[2]), bh = atoi(a[3]);
		return (x >= bx && x < bx + bw && y >= by && y < by + bh) ? 0 : 255;
	}
	fprintf(stderr, "unknown pattern %s\n", pat);
	exit(2);
}

int main(int argc, char **argv)
{
	const char *dev = NULL;
	int hold = 2, i = 1;
	for (; i < argc && argv[i][0] == '-'; i++) {
		if (!strcmp(argv[i], "-d") && i + 1 < argc) dev = argv[++i];
		else if (!strcmp(argv[i], "--hold") && i + 1 < argc) hold = atoi(argv[++i]);
		else { fprintf(stderr, "usage: see source\n"); return 2; }
	}
	if (i >= argc) { fprintf(stderr, "usage: a9eink-pattern [-d card] [--hold S] PATTERN [ARGS]\n"); return 2; }
	const char *pat = argv[i];
	char **args = argv + i + 1;
	if (!strcmp(pat, "box") && argc - i - 1 < 4) { fprintf(stderr, "box X Y W H\n"); return 2; }
	int fd = open_card(dev);

	struct drm_mode_card_res res = { 0 };
	if (xioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &res)) { perror("GETRESOURCES"); return 1; }
	uint32_t crtcs[8], conns[8];
	if (res.count_crtcs < 1 || res.count_connectors < 1 || res.count_crtcs > 8 || res.count_connectors > 8) {
		fprintf(stderr, "unexpected resources\n");
		return 1;
	}
	struct drm_mode_card_res r2 = { .crtc_id_ptr = (uintptr_t)crtcs, .connector_id_ptr = (uintptr_t)conns,
					.count_crtcs = res.count_crtcs, .count_connectors = res.count_connectors };
	if (xioctl(fd, DRM_IOCTL_MODE_GETRESOURCES, &r2)) { perror("GETRESOURCES"); return 1; }

	struct drm_mode_modeinfo modes[4];
	struct drm_mode_get_connector c = { .connector_id = conns[0] };
	if (xioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &c)) { perror("GETCONNECTOR"); return 1; }
	if (c.count_modes < 1) { fprintf(stderr, "no modes\n"); return 1; }
	struct drm_mode_get_connector c2 = { .connector_id = conns[0], .count_modes = 1,
					     .modes_ptr = (uintptr_t)modes };
	if (xioctl(fd, DRM_IOCTL_MODE_GETCONNECTOR, &c2)) { perror("GETCONNECTOR"); return 1; }
	if (modes[0].hdisplay != W || modes[0].vdisplay != H) {
		fprintf(stderr, "mode %ux%u is not %ux%u\n", modes[0].hdisplay, modes[0].vdisplay, W, H);
		return 1;
	}

	struct drm_mode_create_dumb cd = { .width = W, .height = H, .bpp = 32 };
	if (xioctl(fd, DRM_IOCTL_MODE_CREATE_DUMB, &cd)) { perror("CREATE_DUMB"); return 1; }
	struct drm_mode_map_dumb md = { .handle = cd.handle };
	if (xioctl(fd, DRM_IOCTL_MODE_MAP_DUMB, &md)) { perror("MAP_DUMB"); return 1; }
	uint8_t *px = mmap(0, cd.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, (off_t)md.offset);
	if (px == MAP_FAILED) { perror("mmap"); return 1; }
	for (int y = 0; y < H; y++) {
		uint32_t *row = (uint32_t *)(px + (size_t)y * cd.pitch);
		for (int x = 0; x < W; x++) {
			uint32_t g = grey_at(pat, args, x, y);
			row[x] = 0xff000000u | g << 16 | g << 8 | g;
		}
	}
	struct drm_mode_fb_cmd2 fb = { .width = W, .height = H, .pixel_format = DRM_FORMAT_XRGB8888 };
	fb.handles[0] = cd.handle;
	fb.pitches[0] = cd.pitch;
	if (xioctl(fd, DRM_IOCTL_MODE_ADDFB2, &fb)) { perror("ADDFB2"); return 1; }
	struct drm_mode_crtc sc = { .crtc_id = crtcs[0], .fb_id = fb.fb_id, .set_connectors_ptr = (uintptr_t)conns,
				    .count_connectors = 1, .mode = modes[0], .mode_valid = 1 };
	if (xioctl(fd, DRM_IOCTL_MODE_SETCRTC, &sc)) { perror("SETCRTC"); return 1; }
	printf("committed %s on crtc %u connector %u; holding %d s\n", pat, crtcs[0], conns[0], hold);
	fflush(stdout);
	sleep((unsigned)hold);
	return 0;
}
