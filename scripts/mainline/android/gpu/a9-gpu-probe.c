/*
 * a9-gpu-probe -- A9 mainline GPU mode gate (Adreno 610, msm render node,
 * Mesa freedreno). Run by a9ml-gpu.sh before SurfaceFlinger starts; also
 * useful by hand on the phone (root shell). runs/gpu.md.
 *
 *   a9-gpu-probe info            find the msm render node, open it (this is
 *                                what makes msm load the SQE + zap firmware and
 *                                run hw_init), print GPU_ID / CHIP_ID / GMEM;
 *                                exit 0 only for an A610 that answered
 *   a9-gpu-probe render [egl_dir] dlopen libEGL_mesa / libGLESv2_mesa from
 *                                egl_dir (default /vendor/lib64/egl), make a
 *                                64x64 pbuffer ES2 context, clear it to a known
 *                                colour, glFinish, read it back; exit 0 only if
 *                                every pixel matches and GL_RENDERER is freedreno
 *   a9-gpu-probe dmabuf [egl_dir] the SHARED-BUFFER test (review 27): a dumb
 *                                buffer on the vkms card exactly as minigbm makes
 *                                them (width aligned to 64 px, AB24), imported
 *                                into Mesa as an EGLImage (dma-buf), 8 rounds of:
 *                                CPU fills it (incl. the row padding) through the
 *                                vkms mmap; GPU reads it back (CPU->GPU); GPU
 *                                clears it with a per-round pattern incl. edge
 *                                columns/rows; native fence fd waited with poll;
 *                                CPU checks every visible pixel (GPU->CPU) and the
 *                                padding: stores inside the 16x4 GMEM store band
 *                                (x < align16(W), y < align4(H)) are counted and
 *                                allowed, ANY other padding change fails; run for
 *                                824x64 in 832x64, 820x62 in 832x128 (guard rows)
 *                                and 808x62 in 832x128 (guard columns + rows)
 *
 * Output: one "A9GPU <key> <value>" line per fact, then "A9GPU result PASS|FAIL
 * <why>". No writes anywhere. Self-contained ioctl definitions (uapi
 * drm.h / msm_drm.h, stable ABI) so it builds with the bare NDK.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <limits.h>
#include <unistd.h>

#include <EGL/egl.h>
#include <GLES2/gl2.h>

/* --- uapi subset ---------------------------------------------------------- */
struct a9_drm_version {
	int version_major, version_minor, version_patchlevel;
	size_t name_len; char *name;
	size_t date_len; char *date;
	size_t desc_len; char *desc;
};
#define A9_DRM_IOCTL_VERSION _IOWR('d', 0x00, struct a9_drm_version)
struct a9_msm_param { uint32_t pipe, param; uint64_t value; uint32_t len, pad; };
#define A9_DRM_IOCTL_MSM_GET_PARAM _IOWR('d', 0x40 + 0x00, struct a9_msm_param)
#define MSM_PIPE_3D0 0x10
#define MSM_PARAM_GPU_ID 0x01
#define MSM_PARAM_GMEM_SIZE 0x02
#define MSM_PARAM_CHIP_ID 0x03
struct a9_create_dumb { uint32_t height, width, bpp, flags, handle, pitch; uint64_t size; };
struct a9_map_dumb { uint32_t handle, pad; uint64_t offset; };
struct a9_destroy_dumb { uint32_t handle; };
struct a9_prime_handle { uint32_t handle, flags; int32_t fd; };
#define A9_DRM_IOCTL_MODE_CREATE_DUMB _IOWR('d', 0xB2, struct a9_create_dumb)
#define A9_DRM_IOCTL_MODE_MAP_DUMB _IOWR('d', 0xB3, struct a9_map_dumb)
#define A9_DRM_IOCTL_MODE_DESTROY_DUMB _IOWR('d', 0xB4, struct a9_destroy_dumb)
#define A9_DRM_IOCTL_PRIME_HANDLE_TO_FD _IOWR('d', 0x2d, struct a9_prime_handle)
#define A9_DRM_IOCTL_DROP_MASTER _IO('d', 0x1f)

static void say(const char *k, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
#include <stdarg.h>
static void say(const char *k, const char *fmt, ...)
{
	va_list ap;
	printf("A9GPU %s ", k);
	va_start(ap, fmt);
	vprintf(fmt, ap);
	va_end(ap);
	putchar('\n');
	fflush(stdout);
}
static int fail(const char *why) { say("result", "FAIL %s", why); return 1; }

static int get_param(int fd, uint32_t p, uint64_t *v)
{
	struct a9_msm_param a = { .pipe = MSM_PIPE_3D0, .param = p };
	if (ioctl(fd, A9_DRM_IOCTL_MSM_GET_PARAM, &a)) return -errno;
	*v = a.value;
	return 0;
}

/* first /dev/dri/renderD* whose driver is msm; returns an open fd or -1 */
static int open_msm_render(char *path, size_t len)
{
	DIR *d = opendir("/dev/dri");
	struct dirent *e;
	int found = -1;
	if (!d) return -1;
	while ((e = readdir(d))) {
		char name[64] = { 0 };
		struct a9_drm_version v = { .name_len = sizeof(name) - 1, .name = name };
		int fd;
		if (strncmp(e->d_name, "renderD", 7)) continue;
		snprintf(path, len, "/dev/dri/%s", e->d_name);
		fd = open(path, O_RDWR | O_CLOEXEC);
		if (fd < 0) { say("node", "%s open failed: %s", path, strerror(errno)); continue; }
		if (ioctl(fd, A9_DRM_IOCTL_VERSION, &v) == 0 && !strcmp(name, "msm")) {
			say("node", "%s driver msm %d.%d.%d", path, v.version_major, v.version_minor, v.version_patchlevel);
			found = fd;
			break;
		}
		say("node", "%s driver %s (skipped)", path, name);
		close(fd);
	}
	closedir(d);
	return found;
}

static int cmd_info(void)
{
	char path[300];
	uint64_t id = 0, chip = 0, gmem = 0;
	int r, fd = open_msm_render(path, sizeof(path));
	if (fd < 0) return fail("no msm render node");
	/* msm loads the GPU (firmware, zap, hw_init) on open; failure = no gpu = -ENXIO */
	if ((r = get_param(fd, MSM_PARAM_GPU_ID, &id))) { close(fd); return fail(r == -ENXIO ? "GPU not loaded (firmware/zap/hw_init: see dmesg)" : "GET_PARAM GPU_ID failed"); }
	get_param(fd, MSM_PARAM_CHIP_ID, &chip);
	get_param(fd, MSM_PARAM_GMEM_SIZE, &gmem);
	close(fd);
	say("gpu_id", "%llu", (unsigned long long)id);
	say("chip_id", "0x%llx", (unsigned long long)chip);
	say("gmem", "%llu", (unsigned long long)gmem);
	if (id != 610) return fail("not an A610");
	say("result", "PASS info");
	return 0;
}

/* the NDK's EGL/egl.h has no PFN typedefs; EGLImage/sync entry points come
 * from eglGetProcAddress (extensions) */
typedef EGLDisplay (*PFN_eglGetDisplay)(EGLNativeDisplayType);
typedef EGLBoolean (*PFN_eglInitialize)(EGLDisplay, EGLint *, EGLint *);
typedef EGLBoolean (*PFN_eglChooseConfig)(EGLDisplay, const EGLint *, EGLConfig *, EGLint, EGLint *);
typedef EGLContext (*PFN_eglCreateContext)(EGLDisplay, EGLConfig, EGLContext, const EGLint *);
typedef EGLSurface (*PFN_eglCreatePbufferSurface)(EGLDisplay, EGLConfig, const EGLint *);
typedef EGLBoolean (*PFN_eglMakeCurrent)(EGLDisplay, EGLSurface, EGLSurface, EGLContext);
typedef EGLint (*PFN_eglGetError)(void);
typedef EGLBoolean (*PFN_eglTerminate)(EGLDisplay);
typedef const char *(*PFN_eglQueryString)(EGLDisplay, EGLint);
typedef void *(*PFN_eglGetProcAddress)(const char *);
typedef void *(*PFN_eglCreateImageKHR)(EGLDisplay, EGLContext, EGLenum, EGLClientBuffer, const EGLint *);
typedef EGLBoolean (*PFN_eglDestroyImageKHR)(EGLDisplay, void *);
typedef void *(*PFN_eglCreateSyncKHR)(EGLDisplay, EGLenum, const EGLint *);
typedef EGLBoolean (*PFN_eglDestroySyncKHR)(EGLDisplay, void *);
typedef EGLint (*PFN_eglDupNativeFenceFDANDROID)(EGLDisplay, void *);
typedef void (*PFN_glEGLImageTargetTexture2DOES)(GLenum, void *);

#define A9_EGL_LINUX_DMA_BUF_EXT 0x3270
#define A9_EGL_LINUX_DRM_FOURCC_EXT 0x3271
#define A9_EGL_DMA_BUF_PLANE0_FD_EXT 0x3272
#define A9_EGL_DMA_BUF_PLANE0_OFFSET_EXT 0x3273
#define A9_EGL_DMA_BUF_PLANE0_PITCH_EXT 0x3274
#define A9_EGL_SYNC_NATIVE_FENCE_ANDROID 0x3144
#define A9_EGL_NO_NATIVE_FENCE_FD_ANDROID (-1)
#define A9_DRM_FORMAT_ABGR8888 0x34324241 /* 'AB24' */

static struct egl {
	void *he, *hg;
	PFN_eglGetDisplay GetDisplay; PFN_eglInitialize Initialize; PFN_eglChooseConfig ChooseConfig;
	PFN_eglCreateContext CreateContext; PFN_eglCreatePbufferSurface CreatePbufferSurface;
	PFN_eglMakeCurrent MakeCurrent; PFN_eglGetError GetError; PFN_eglTerminate Terminate;
	PFN_eglQueryString QueryString; PFN_eglGetProcAddress GetProcAddress;
	PFNGLCLEARCOLORPROC ClearColor; PFNGLCLEARPROC Clear; PFNGLFINISHPROC Finish; PFNGLFLUSHPROC Flush;
	PFNGLREADPIXELSPROC ReadPixels; PFNGLGETSTRINGPROC GetString; PFNGLGETERRORPROC GLGetError;
	PFNGLGENTEXTURESPROC GenTextures; PFNGLBINDTEXTUREPROC BindTexture; PFNGLGENFRAMEBUFFERSPROC GenFramebuffers;
	PFNGLBINDFRAMEBUFFERPROC BindFramebuffer; PFNGLFRAMEBUFFERTEXTURE2DPROC FramebufferTexture2D;
	PFNGLCHECKFRAMEBUFFERSTATUSPROC CheckFramebufferStatus; PFNGLVIEWPORTPROC Viewport;
	PFNGLSCISSORPROC Scissor; PFNGLENABLEPROC Enable; PFNGLDISABLEPROC Disable;
	PFNGLDELETETEXTURESPROC DeleteTextures; PFNGLDELETEFRAMEBUFFERSPROC DeleteFramebuffers;
	EGLDisplay dpy; EGLContext ctx; EGLSurface surf;
	char renderer[256], version[256];
} E;

#define SYM(h, f, n) do { *(void **)&E.f = dlsym(h, n); if (!E.f) { say("dlsym", "%s missing", n); return fail("entry point missing"); } } while (0)
/* load Mesa, make a 64x64 pbuffer ES2 context current; renderer/version are
 * COPIED here while the context is alive (review 27: glGetString's storage
 * belongs to the context/display and may be freed by eglTerminate) */
static int egl_up(const char *dir)
{
	char p1[256], p2[256];
	snprintf(p1, sizeof(p1), "%s/libEGL_mesa.so", dir);
	snprintf(p2, sizeof(p2), "%s/libGLESv2_mesa.so", dir);
	if (!(E.he = dlopen(p1, RTLD_NOW | RTLD_LOCAL))) { say("dlopen", "%s", dlerror()); return fail("libEGL_mesa"); }
	if (!(E.hg = dlopen(p2, RTLD_NOW | RTLD_LOCAL))) { say("dlopen", "%s", dlerror()); return fail("libGLESv2_mesa"); }
	SYM(E.he, GetDisplay, "eglGetDisplay"); SYM(E.he, Initialize, "eglInitialize");
	SYM(E.he, ChooseConfig, "eglChooseConfig"); SYM(E.he, CreateContext, "eglCreateContext");
	SYM(E.he, CreatePbufferSurface, "eglCreatePbufferSurface"); SYM(E.he, MakeCurrent, "eglMakeCurrent");
	SYM(E.he, GetError, "eglGetError"); SYM(E.he, Terminate, "eglTerminate");
	SYM(E.he, QueryString, "eglQueryString"); SYM(E.he, GetProcAddress, "eglGetProcAddress");
	SYM(E.hg, ClearColor, "glClearColor"); SYM(E.hg, Clear, "glClear"); SYM(E.hg, Finish, "glFinish");
	SYM(E.hg, Flush, "glFlush"); SYM(E.hg, ReadPixels, "glReadPixels"); SYM(E.hg, GetString, "glGetString");
	SYM(E.hg, GLGetError, "glGetError"); SYM(E.hg, GenTextures, "glGenTextures"); SYM(E.hg, BindTexture, "glBindTexture");
	SYM(E.hg, GenFramebuffers, "glGenFramebuffers"); SYM(E.hg, BindFramebuffer, "glBindFramebuffer");
	SYM(E.hg, FramebufferTexture2D, "glFramebufferTexture2D"); SYM(E.hg, CheckFramebufferStatus, "glCheckFramebufferStatus");
	SYM(E.hg, Viewport, "glViewport"); SYM(E.hg, Scissor, "glScissor"); SYM(E.hg, Enable, "glEnable");
	SYM(E.hg, Disable, "glDisable"); SYM(E.hg, DeleteTextures, "glDeleteTextures");
	SYM(E.hg, DeleteFramebuffers, "glDeleteFramebuffers");
	EGLint maj = 0, min = 0, n = 0;
	E.dpy = E.GetDisplay(EGL_DEFAULT_DISPLAY);
	if (E.dpy == EGL_NO_DISPLAY || !E.Initialize(E.dpy, &maj, &min)) { say("egl_error", "0x%x", E.GetError()); return fail("eglInitialize"); }
	say("egl", "%d.%d %s", maj, min, E.QueryString(E.dpy, EGL_VENDOR));
	const EGLint ca[] = { EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
			      EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE };
	EGLConfig cfg;
	if (!E.ChooseConfig(E.dpy, ca, &cfg, 1, &n) || n < 1) return fail("no RGBA8888 pbuffer config");
	const EGLint xa[] = { EGL_CONTEXT_CLIENT_VERSION, 2, EGL_NONE };
	E.ctx = E.CreateContext(E.dpy, cfg, EGL_NO_CONTEXT, xa);
	if (E.ctx == EGL_NO_CONTEXT) { say("egl_error", "0x%x", E.GetError()); return fail("eglCreateContext"); }
	const EGLint sa[] = { EGL_WIDTH, 64, EGL_HEIGHT, 64, EGL_NONE };
	E.surf = E.CreatePbufferSurface(E.dpy, cfg, sa);
	if (E.surf == EGL_NO_SURFACE || !E.MakeCurrent(E.dpy, E.surf, E.surf, E.ctx)) { say("egl_error", "0x%x", E.GetError()); return fail("pbuffer/makecurrent"); }
	const char *r = (const char *)E.GetString(GL_RENDERER), *v = (const char *)E.GetString(GL_VERSION);
	snprintf(E.renderer, sizeof(E.renderer), "%s", r ? r : "");
	snprintf(E.version, sizeof(E.version), "%s", v ? v : "");
	say("gl_renderer", "%s", E.renderer);
	say("gl_version", "%s", E.version);
	if (!strstr(E.renderer, "FD610")) return fail("renderer is not freedreno FD610 (software fallback?)");
	return 0;
}
static void egl_down(void)
{
	if (E.dpy != EGL_NO_DISPLAY && E.dpy) {
		E.MakeCurrent(E.dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
		E.Terminate(E.dpy);
	}
}

static int cmd_render(const char *dir)
{
	int r = egl_up(dir);
	if (r) { egl_down(); return r; }
	static uint8_t px[64 * 64 * 4];
	E.ClearColor(64 / 255.0f, 128 / 255.0f, 192 / 255.0f, 1.0f);
	E.Clear(GL_COLOR_BUFFER_BIT);
	E.Finish();
	memset(px, 0, sizeof(px));
	E.ReadPixels(0, 0, 64, 64, GL_RGBA, GL_UNSIGNED_BYTE, px);
	unsigned bad = 0;
	for (unsigned i = 0; i < 64 * 64; i++) {
		const uint8_t *q = px + 4 * i;
		if (abs(q[0] - 64) > 1 || abs(q[1] - 128) > 1 || abs(q[2] - 192) > 1 || q[3] != 255) bad++;
	}
	GLenum ge = E.GLGetError();
	say("pixels", "%u/4096 wrong (first %u,%u,%u,%u) glError 0x%x", bad, px[0], px[1], px[2], px[3], ge);
	egl_down();
	if (bad || ge) return fail("readback mismatch");
	say("result", "PASS render");
	return 0;
}

/* first /dev/dri/card* whose driver is vkms; drops DRM master at once if this
 * open made us master (nobody else may lose it: the composer must own it) */
/* review 38 R5: pick the vkms card from SYSFS before any open(), so the probe
 * never opens (and so can never become DRM master of) msm-kms or a9_eink.
 * Same rule as a9ml-eink.sh card_kind(): the card's device lives under
 * /devices/faux/vkms/, or its device's driver is "vkms". */
static int sysfs_is_vkms(const char *sysdrm, const char *card)
{
	char p[512], r[PATH_MAX];
	const char *b;
	snprintf(p, sizeof(p), "%s/%s", sysdrm, card);
	if (realpath(p, r) && strstr(r, "/devices/faux/vkms/")) return 1;
	snprintf(p, sizeof(p), "%s/%s/device/driver", sysdrm, card);
	if (!realpath(p, r)) return 0;
	b = strrchr(r, '/');
	return b && !strcmp(b + 1, "vkms");
}

static int open_vkms(void)
{
	DIR *d = opendir("/dev/dri");
	struct dirent *e;
	int found = -1;
	if (!d) return -1;
	while ((e = readdir(d))) {
		char path[300], name[64] = { 0 };
		struct a9_drm_version v = { .name_len = sizeof(name) - 1, .name = name };
		int fd;
		if (strncmp(e->d_name, "card", 4)) continue;
		if (!sysfs_is_vkms("/sys/class/drm", e->d_name)) continue;
		snprintf(path, sizeof(path), "/dev/dri/%s", e->d_name);
		if ((fd = open(path, O_RDWR | O_CLOEXEC)) < 0) continue;
		if (ioctl(fd, A9_DRM_IOCTL_VERSION, &v) == 0 && !strcmp(name, "vkms")) {
			int dm = ioctl(fd, A9_DRM_IOCTL_DROP_MASTER, 0);
			say("vkms", "%s (%s)", path, dm == 0 ? "was DRM master: dropped" : "not master");
			found = fd;
			break;
		}
		close(fd);
	}
	closedir(d);
	return found;
}

static uint32_t pat(unsigned round, unsigned x, unsigned y, int cpu)
{	/* CPU fill pattern (R,G,B,A bytes = little-endian u32) */
	if (cpu) return 0xff000000u | ((x * 7 + round * 13) & 0xff) | (((y * 5 + round) & 0xff) << 8) | (((x ^ y ^ round) & 0xff) << 16);
	return 0;
}
static const uint8_t RC[8][4] = { {255,0,0,255}, {0,255,0,255}, {0,0,255,255}, {255,255,255,255},
				   {0,0,0,255}, {128,64,32,255}, {17,34,51,255}, {200,100,50,255} };

/*
 * Row/edge padding the GPU MAY write (G1 attempt 2, runs/gpu.md): freedreno
 * a6xx stores GMEM tiles to linear memory at a 16x4-pixel granularity
 * (Mesa 26.2.5 fd6_gmem.cc set_blit_scissor: maxx = align(width, 16),
 * maxy = align(height, 4)); fd6_layout.c accepts an imported linear pitch only
 * if it is a multiple of 64 B (16 px at 4 B/px) and sizes the BO check with
 * height aligned to 4, "because the blits used for mem<->gmem work at a
 * granularity of 16x4". So pixels with x in [W, align16(W)) or y in
 * [H, align4(H)) (both inside align16(W) x align4(H)) are an expected,
 * bounded over-store. Anything else outside the visible W x H must stay
 * untouched: that is the strict out-of-bounds guard.
 */
static int pad_allowed(unsigned x, unsigned y, unsigned w, unsigned h)
{
	unsigned aw = (w + 15) & ~15u, ah = (h + 3) & ~3u;
	return (x >= w || y >= h) && x < aw && y < ah;
}

struct shared_res { unsigned c2g, g2c, band, guard, fence, rounds_band; GLenum ge; int setup_fail; };

/* one geometry: visible w x h AB24 inside a vkms dumb buffer of aw x ah */
static int shared_test(int kfd, unsigned w, unsigned h, unsigned aw, unsigned ah, struct shared_res *res,
		       PFN_eglCreateImageKHR CreateImage, PFN_eglDestroyImageKHR DestroyImage,
		       PFN_eglCreateSyncKHR CreateSync, PFN_eglDestroySyncKHR DestroySync,
		       PFN_eglDupNativeFenceFDANDROID DupFence, PFN_glEGLImageTargetTexture2DOES ImageTarget)
{
	memset(res, 0, sizeof(*res));
	struct a9_create_dumb cd = { .width = aw, .height = ah, .bpp = 32 };
	if (ioctl(kfd, A9_DRM_IOCTL_MODE_CREATE_DUMB, &cd)) { res->setup_fail = 1; return fail("CREATE_DUMB"); }
	struct a9_map_dumb md = { .handle = cd.handle };
	struct a9_prime_handle ph = { .handle = cd.handle, .flags = O_CLOEXEC | O_RDWR, .fd = -1 };
	uint8_t *map = MAP_FAILED;
	void *img = NULL;
	GLuint tex = 0, fbo = 0;
	int rc = 1;
	if (ioctl(kfd, A9_DRM_IOCTL_MODE_MAP_DUMB, &md) ||
	    (map = mmap(NULL, cd.size, PROT_READ | PROT_WRITE, MAP_SHARED, kfd, (off_t)md.offset)) == MAP_FAILED) { res->setup_fail = 1; fail("MAP_DUMB/mmap"); goto out; }
	if (ioctl(kfd, A9_DRM_IOCTL_PRIME_HANDLE_TO_FD, &ph) || ph.fd < 0) { res->setup_fail = 1; fail("PRIME_HANDLE_TO_FD"); goto out; }
	say("buffer", "%ux%u (alloc %ux%u) pitch %u size %llu; GPU over-store band allowed up to %ux%u",
	    w, h, cd.width, cd.height, cd.pitch, (unsigned long long)cd.size, (w + 15) & ~15u, (h + 3) & ~3u);
	if (((w + 15) & ~15u) * 4 > cd.pitch || ((h + 3) & ~3u) > cd.height) { res->setup_fail = 1; fail("allocation smaller than the 16x4 store granularity"); goto out; }
	const EGLint ia[] = { EGL_WIDTH, (EGLint)w, EGL_HEIGHT, (EGLint)h, A9_EGL_LINUX_DRM_FOURCC_EXT, (EGLint)A9_DRM_FORMAT_ABGR8888,
			      A9_EGL_DMA_BUF_PLANE0_FD_EXT, ph.fd, A9_EGL_DMA_BUF_PLANE0_OFFSET_EXT, 0,
			      A9_EGL_DMA_BUF_PLANE0_PITCH_EXT, (EGLint)cd.pitch, EGL_NONE };
	img = CreateImage(E.dpy, EGL_NO_CONTEXT, A9_EGL_LINUX_DMA_BUF_EXT, NULL, ia);
	if (!img) { say("egl_error", "0x%x", E.GetError()); res->setup_fail = 1; fail("eglCreateImage(dma-buf AB24 LINEAR)"); goto out; }
	E.GenTextures(1, &tex); E.BindTexture(GL_TEXTURE_2D, tex);
	ImageTarget(GL_TEXTURE_2D, img);
	E.GenFramebuffers(1, &fbo); E.BindFramebuffer(GL_FRAMEBUFFER, fbo);
	E.FramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, tex, 0);
	GLenum st = E.CheckFramebufferStatus(GL_FRAMEBUFFER);
	if (st != GL_FRAMEBUFFER_COMPLETE) { say("fbo", "status 0x%x", st); res->setup_fail = 1; fail("dma-buf not renderable"); goto out; }
	E.Viewport(0, 0, w, h);
	uint8_t *rd = malloc((size_t)w * h * 4);
	if (!rd) { res->setup_fail = 1; fail("malloc"); goto out; }
	const uint32_t PAD = 0xa5a5a5a5u;
	unsigned bx0 = ~0u, by0 = ~0u, bx1 = 0, by1 = 0;
	for (unsigned r = 0; r < 8; r++) {
		for (unsigned y = 0; y < cd.height; y++)
			for (unsigned x = 0; x < cd.pitch / 4; x++)
				((uint32_t *)(map + (size_t)y * cd.pitch))[x] = (x < w && y < h) ? pat(r, x, y, 1) : PAD;
		__sync_synchronize();
		memset(rd, 0, (size_t)w * h * 4);
		E.ReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, rd);
		for (unsigned y = 0; y < h; y++)
			for (unsigned x = 0; x < w; x++) {
				uint32_t g; memcpy(&g, rd + 4 * ((size_t)y * w + x), 4);
				if (g != pat(r, x, y, 1)) res->c2g++;
			}
		const uint8_t *c = RC[r], *e = RC[(r + 3) & 7];
		E.Disable(GL_SCISSOR_TEST);
		E.ClearColor(c[0] / 255.0f, c[1] / 255.0f, c[2] / 255.0f, c[3] / 255.0f); E.Clear(GL_COLOR_BUFFER_BIT);
		E.Enable(GL_SCISSOR_TEST);
		E.ClearColor(e[0] / 255.0f, e[1] / 255.0f, e[2] / 255.0f, e[3] / 255.0f);
		E.Scissor(0, 0, 1, h); E.Clear(GL_COLOR_BUFFER_BIT);
		E.Scissor(w - 1, 0, 1, h); E.Clear(GL_COLOR_BUFFER_BIT);
		E.Scissor(0, h - 1, w, 1); E.Clear(GL_COLOR_BUFFER_BIT);
		E.Disable(GL_SCISSOR_TEST);
		const EGLint fa[] = { EGL_NONE };
		void *sync = CreateSync(E.dpy, A9_EGL_SYNC_NATIVE_FENCE_ANDROID, fa);
		E.Flush();
		int ffd = sync ? DupFence(E.dpy, sync) : -1;
		if (ffd < 0) res->fence++;
		else {
			struct pollfd pf = { .fd = ffd, .events = POLLIN };
			if (poll(&pf, 1, 5000) != 1) res->fence++;
			close(ffd);
		}
		if (sync) DestroySync(E.dpy, sync);
		unsigned band_r = 0, guard_r = 0;
		for (unsigned y = 0; y < cd.height; y++)
			for (unsigned x = 0; x < cd.pitch / 4; x++) {
				uint32_t v = ((volatile uint32_t *)(map + (size_t)y * cd.pitch))[x];
				if (x >= w || y >= h) {
					if (v == PAD) continue;
					if (pad_allowed(x, y, w, h)) band_r++; else guard_r++;
					if (x < bx0) bx0 = x; if (y < by0) by0 = y; if (x > bx1) bx1 = x; if (y > by1) by1 = y;
					continue;
				}
				const uint8_t *q = (x == 0 || x == w - 1 || y == h - 1) ? e : c;
				uint32_t want = q[0] | (q[1] << 8) | (q[2] << 16) | ((uint32_t)q[3] << 24);
				if (v != want) res->g2c++;
			}
		if (band_r) { res->rounds_band++; say("round", "%u: %u padding px in the 16x4 store band, %u outside it", r, band_r, guard_r); }
		res->band += band_r; res->guard += guard_r;
	}
	free(rd);
	res->ge = E.GLGetError();
	if (res->band || res->guard)
		say("padding_box", "x %u..%u y %u..%u (visible %ux%u)", bx0, bx1, by0, by1, w, h);
	say("shared", "%ux%u in %ux%u, 8 rounds: cpu->gpu %u wrong, gpu->cpu %u wrong, padding: %u px in the allowed 16x4 band (%u rounds), %u px OUTSIDE it, fences %u bad, glError 0x%x",
	    w, h, cd.width, cd.height, res->c2g, res->g2c, res->band, res->rounds_band, res->guard, res->fence, res->ge);
	rc = (res->c2g || res->g2c || res->guard || res->fence || res->ge) ? 1 : 0;
out:
	if (fbo) { E.BindFramebuffer(GL_FRAMEBUFFER, 0); E.DeleteFramebuffers(1, &fbo); }
	if (tex) E.DeleteTextures(1, &tex);
	if (img) DestroyImage(E.dpy, img);
	if (ph.fd >= 0) close(ph.fd);
	if (map != MAP_FAILED) munmap(map, cd.size);
	{ struct a9_destroy_dumb dd = { .handle = cd.handle }; ioctl(kfd, A9_DRM_IOCTL_MODE_DESTROY_DUMB, &dd); }
	return rc;
}

static int cmd_dmabuf(const char *dir)
{
	char wc[8] = "?";
	FILE *f = fopen("/sys/module/vkms/parameters/map_wc", "r");
	if (f) { if (!fgets(wc, sizeof(wc), f)) wc[0] = '?'; fclose(f); wc[strcspn(wc, "\n")] = 0; }
	say("vkms_map_wc", "%s", wc);
	if (strcmp(wc, "Y")) return fail("vkms.map_wc is not Y: GPU-shared buffers would be cached");
	int kfd = open_vkms();
	if (kfd < 0) return fail("no vkms card");
	int rc = egl_up(dir);
	if (rc) { egl_down(); close(kfd); return rc; }
	rc = 1;
	PFN_eglCreateImageKHR CreateImage = (PFN_eglCreateImageKHR)E.GetProcAddress("eglCreateImageKHR");
	PFN_eglDestroyImageKHR DestroyImage = (PFN_eglDestroyImageKHR)E.GetProcAddress("eglDestroyImageKHR");
	PFN_eglCreateSyncKHR CreateSync = (PFN_eglCreateSyncKHR)E.GetProcAddress("eglCreateSyncKHR");
	PFN_eglDestroySyncKHR DestroySync = (PFN_eglDestroySyncKHR)E.GetProcAddress("eglDestroySyncKHR");
	PFN_eglDupNativeFenceFDANDROID DupFence = (PFN_eglDupNativeFenceFDANDROID)E.GetProcAddress("eglDupNativeFenceFDANDROID");
	PFN_glEGLImageTargetTexture2DOES ImageTarget = (PFN_glEGLImageTargetTexture2DOES)E.GetProcAddress("glEGLImageTargetTexture2DOES");
	const char *ext = E.QueryString(E.dpy, EGL_EXTENSIONS);
	if (!CreateImage || !DestroyImage || !ImageTarget || !ext || !strstr(ext, "EGL_EXT_image_dma_buf_import")) { fail("no EGL_EXT_image_dma_buf_import"); goto out; }
	if (!CreateSync || !DestroySync || !DupFence || !strstr(ext, "EGL_ANDROID_native_fence_sync")) { fail("no EGL_ANDROID_native_fence_sync"); goto out; }
	/* A: the E Ink frame width as minigbm allocates it (64-px aligned stride and
	 *    height): 824x64 in 832x64, the geometry of G1 attempt 2;
	 * B: guard geometry: 820x62 (store band up to 832x64) in 832x128, so rows
	 *    64..127 inside the allocation are a strict sentinel for any store past
	 *    the 16x4 granularity (true out-of-BO stores would be SMMU faults) */
	/* C (review 36 F1): column slack past the band inside the pitch: 808x62 in
	 *    832x128, band x 808..815 / rows 62..63; columns 816..831 and rows
	 *    64..127 are strict, so a column over-store past align16 is caught too */
	struct shared_res a, b, c;
	int ra = shared_test(kfd, 824, 64, 832, 64, &a, CreateImage, DestroyImage, CreateSync, DestroySync, DupFence, ImageTarget);
	int rb = a.setup_fail ? 1 : shared_test(kfd, 820, 62, 832, 128, &b, CreateImage, DestroyImage, CreateSync, DestroySync, DupFence, ImageTarget);
	int rcc = (a.setup_fail || b.setup_fail) ? 1 : shared_test(kfd, 808, 62, 832, 128, &c, CreateImage, DestroyImage, CreateSync, DestroySync, DupFence, ImageTarget);
	if (ra || rb || rcc) { fail("shared dma-buf readback"); goto out; }
	say("result", "PASS dmabuf");
	rc = 0;
out:
	egl_down();
	close(kfd);
	return rc;
}

#ifdef A9_GPU_PROBE_UNIT
/* host unit test of the padding classification (tests/test-gpu-probe-host.sh) */
int main(void)
{
	int bad = 0;
	/* 824x64: band = x 824..831, rows 0..63; nothing else */
	bad |= !pad_allowed(824, 0, 824, 64) || !pad_allowed(831, 63, 824, 64);
	bad |= pad_allowed(832, 0, 824, 64) || pad_allowed(824, 64, 824, 64) || pad_allowed(100, 10, 824, 64);
	/* 820x62: band = x 820..831 (rows 0..63) and rows 62..63 (x 0..831) */
	bad |= !pad_allowed(820, 0, 820, 62) || !pad_allowed(0, 62, 820, 62) || !pad_allowed(831, 63, 820, 62);
	bad |= pad_allowed(0, 64, 820, 62) || pad_allowed(832, 10, 820, 62) || pad_allowed(819, 61, 820, 62);
	/* 808x62 (geometry C): band x 808..815 and rows 62..63 up to x 815; x >= 816 strict */
	bad |= !pad_allowed(808, 0, 808, 62) || !pad_allowed(815, 63, 808, 62) || !pad_allowed(0, 63, 808, 62);
	bad |= pad_allowed(816, 0, 808, 62) || pad_allowed(831, 10, 808, 62) || pad_allowed(816, 63, 808, 62) || pad_allowed(0, 64, 808, 62);
	/* already aligned: no band at all */
	bad |= pad_allowed(832, 0, 832, 64) || pad_allowed(0, 64, 832, 64);
	printf("pad_allowed unit: %s\n", bad ? "FAIL" : "PASS");
	/* R5: sysfs card selection against a fake /sys (layout as on the phone) */
	{
		char t[] = "/tmp/a9probe-XXXXXX", p[600], q[600];
		int vb = 0;
		if (!mkdtemp(t)) return 1;
		const char *dirs[] = { "devices", "devices/faux", "devices/faux/vkms", "devices/faux/vkms/drm",
			"devices/faux/vkms/drm/card0", "devices/platform", "devices/platform/eink-display",
			"devices/platform/eink-display/drm", "devices/platform/eink-display/drm/card2",
			"devices/platform/soc@0", "devices/platform/soc@0/5e00000.display-subsystem",
			"devices/platform/soc@0/5e00000.display-subsystem/drm",
			"devices/platform/soc@0/5e00000.display-subsystem/drm/card3", "class", "class/drm", NULL };
		for (int i = 0; dirs[i]; i++) { snprintf(p, sizeof(p), "%s/%s", t, dirs[i]); mkdir(p, 0755); }
		const char *links[][2] = { { "card0", "../../devices/faux/vkms/drm/card0" },
			{ "card2", "../../devices/platform/eink-display/drm/card2" },
			{ "card3", "../../devices/platform/soc@0/5e00000.display-subsystem/drm/card3" } };
		for (int i = 0; i < 3; i++) { snprintf(p, sizeof(p), "%s/class/drm/%s", t, links[i][0]); if (symlink(links[i][1], p)) vb = 1; }
		snprintf(q, sizeof(q), "%s/class/drm", t);
		vb |= !sysfs_is_vkms(q, "card0") || sysfs_is_vkms(q, "card2") || sysfs_is_vkms(q, "card3") || sysfs_is_vkms(q, "card9");
		printf("sysfs_is_vkms unit: %s\n", vb ? "FAIL" : "PASS");
		bad |= vb;
		snprintf(p, sizeof(p), "rm -rf '%s'", t);
		if (system(p)) bad = 1;
	}
	return bad;
}
#else
int main(int argc, char **argv)
{
	setvbuf(stdout, NULL, _IOLBF, 0);
	if (argc >= 2 && !strcmp(argv[1], "info")) return cmd_info();
	if (argc >= 2 && !strcmp(argv[1], "render")) return cmd_render(argc >= 3 ? argv[2] : "/vendor/lib64/egl");
	if (argc >= 2 && !strcmp(argv[1], "dmabuf")) return cmd_dmabuf(argc >= 3 ? argv[2] : "/vendor/lib64/egl");
	fprintf(stderr, "usage: %s info | render [egl_dir] | dmabuf [egl_dir]\n", argv[0]);
	return 2;
}
#endif
