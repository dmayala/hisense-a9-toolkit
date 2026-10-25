// SPDX-License-Identifier: Apache-2.0
// Actual minigbm callbacks/library with a mock CREATE_DUMB. No DRM device opens.
#include <cassert>
#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <dlfcn.h>
#include <xf86drm.h>
#include "cros_gralloc/cros_gralloc_helpers.h"
extern "C" {
#include "drv_priv.h"
#include "drv_helpers.h"
extern const struct backend backend_vkms;
extern const struct backend backend_udl;
int drmIoctl(int fd, unsigned long request, void* arg) {
    assert(fd==-123 && request==DRM_IOCTL_MODE_CREATE_DUMB);
    auto* d=static_cast<drm_mode_create_dumb*>(arg);
    d->pitch=((d->width*d->bpp/8+63)/64)*64;
    d->size=((uint64_t(d->pitch)*d->height+4095)/4096)*4096;
    d->handle=42;
    return 0;
}
}
int main(int argc, char** argv) {
    // dlopen only resolves the prebuilt mapper; never call AIMapper_loadIMapper.
    if(argc==3 && !strcmp(argv[1],"--link")) {
        void* h=dlopen(argv[2],RTLD_NOW|RTLD_LOCAL);
        if(!h) { fprintf(stderr,"link check: %s\n",dlerror()); return 1; }
        dlclose(h); puts("PASS prebuilt mapper binds patched core"); return 0;
    }
    driver drv{}; drv.fd=-123; drv.backend=&backend_vkms;
    drv.combos=drv_array_init(sizeof(combination)); assert(drv.combos);
    assert(!backend_vkms.init(&drv));
    uint32_t format=0; uint64_t flags=0;
    // Aperture SurfaceView: exact observed IMPL_DEF usage0x20930. Camera
    // software-write/read hints added by the HAL must not change resolution.
    for (uint64_t usage : {0x20930ull, 0x20933ull, 0x20130ull}) {
        backend_vkms.resolve_format_and_use_flags(&drv,cros_gralloc_convert_format(34),
            cros_gralloc_convert_usage(usage),&format,&flags);
        assert(format==DRM_FORMAT_ABGR8888 && drv_get_combination(&drv,format,flags));
        bo preview{}; preview.drv=&drv; preview.meta.width=1280; preview.meta.height=960; preview.meta.format=format;
        assert(!backend_vkms.bo_create(&preview,1280,960,format,flags));
        assert(preview.meta.strides[0]==5120 && preview.meta.total_size>=4915200);
    }
    for (uint64_t usage : {0x20003ull, 0x30003ull}) { // CPU / encoder camera output
        backend_vkms.resolve_format_and_use_flags(&drv,cros_gralloc_convert_format(34),
            cros_gralloc_convert_usage(usage),&format,&flags);
        assert(format==DRM_FORMAT_NV12 && drv_get_combination(&drv,format,flags));
    }
    backend_vkms.resolve_format_and_use_flags(&drv,cros_gralloc_convert_format(35),
        cros_gralloc_convert_usage(0x20033),&format,&flags);
    assert(format==DRM_FORMAT_NV12 && drv_get_combination(&drv,format,flags));
    puts("PASS Aperture IMPL_DEF camera display RGBA1280x960; CPU/encoder/explicit YUV retain NV12");
    uint64_t requested=cros_gralloc_convert_usage(0x400900);
    assert(requested&BO_USE_SCANOUT); assert(requested&BO_USE_TEXTURE); assert(requested&BO_USE_HW_VIDEO_DECODER);
    backend_vkms.resolve_format_and_use_flags(&drv,cros_gralloc_convert_format(35),requested,&format,&flags);
    assert(format==DRM_FORMAT_NONE && !drv_get_combination(&drv,format,flags));
    backend_vkms.resolve_format_and_use_flags(&drv,cros_gralloc_convert_format(35),
        BO_USE_HW_VIDEO_DECODER|BO_USE_SW_READ_OFTEN,&format,&flags);
    assert(format==DRM_FORMAT_NV12 && drv_get_combination(&drv,format,flags));
    assert(!drv_get_combination(&drv,format,flags|BO_USE_PROTECTED));
    for (uint32_t yuv : {DRM_FORMAT_NV12, DRM_FORMAT_NV21, DRM_FORMAT_YVU420}) {
        for (uint64_t display : {BO_USE_TEXTURE, BO_USE_SCANOUT, BO_USE_RENDERING}) {
            backend_vkms.resolve_format_and_use_flags(&drv,yuv,display,&format,&flags);
            assert(format==DRM_FORMAT_NONE && !drv_get_combination(&drv,format,flags));
        }
    }
    backend_vkms.resolve_format_and_use_flags(&drv,DRM_FORMAT_NV12,
        BO_USE_HW_VIDEO_DECODER|BO_USE_SW_READ_OFTEN,&format,&flags);
    struct { uint32_t width,height,stride,scanlines; uint64_t minimum; } cases[]={
        {384,256,384,256,147456}, {320,240,384,256,147456},
        {640,480,640,480,462848}, {1920,1088,1920,1088,3133440},
        {384,96,384,96,57344}
    };
    for(const auto& c:cases) {
        bo b{}; b.drv=&drv; b.meta.width=c.width; b.meta.height=c.height; b.meta.format=DRM_FORMAT_NV12;
        assert(!backend_vkms.bo_create(&b,c.width,c.height,format,flags));
        assert(b.meta.strides[0]==c.stride && b.meta.strides[1]==c.stride);
        assert(b.meta.offsets[1]==c.stride*c.scanlines && b.meta.total_size>=c.minimum);
        assert(b.meta.width==c.width && b.meta.height==c.height);
        printf("PASS NV12 %ux%u stride=%u uv=%u capacity=%llu\n",c.width,c.height,b.meta.strides[0],b.meta.offsets[1],(unsigned long long)b.meta.total_size);
    }
    backend_vkms.resolve_format_and_use_flags(&drv,cros_gralloc_convert_format(1),
        cros_gralloc_convert_usage(0x133|0x800),&format,&flags);
    assert(format==DRM_FORMAT_ABGR8888 && drv_get_combination(&drv,format,flags));
    // Ordinary RGB scanout retains the original generic behavior.
    backend_vkms.resolve_format_and_use_flags(&drv,DRM_FORMAT_XRGB8888,BO_USE_RENDERING|BO_USE_SCANOUT,&format,&flags);
    assert((flags&BO_USE_SCANOUT) && drv_get_combination(&drv,format,flags));
    bo rgb{}; rgb.drv=&drv; rgb.meta.width=320; rgb.meta.height=240; rgb.meta.format=format;
    assert(!backend_vkms.bo_create(&rgb,320,240,format,flags) && rgb.meta.strides[0]==1280 && rgb.meta.total_size==327680);
    backend_vkms.resolve_format_and_use_flags(&drv,DRM_FORMAT_FLEX_YCbCr_420_888,BO_USE_TEXTURE|BO_USE_SCANOUT,&format,&flags);
    assert(format==DRM_FORMAT_NONE); // all YUV display allocations refuse
    backend_udl.resolve_format_and_use_flags(&drv,DRM_FORMAT_FLEX_YCbCr_420_888,requested,&format,&flags);
    assert(flags&BO_USE_SCANOUT); // other dumb backends unchanged
    drv_array_destroy(drv.combos);
    puts("PASS usage0x400900 refusal + private NV12 capture, protected refusal, RGB/other-backend regressions");
}
