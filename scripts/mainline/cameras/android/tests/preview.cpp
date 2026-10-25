// SPDX-License-Identifier: LGPL-2.1-or-later
// Production stream planner + postprocessors, anonymous files replacing dma-bufs.
#include "camera_preview.h"
#include "camera_stream_config.h"
#include "yuv/post_processor_yuv.h"
#include "jpeg/post_processor_jpeg.h"
#include "mm/cros_gralloc_handle.h"
#include <libcamera/base/shared_fd.h>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <sys/mman.h>
#include <unistd.h>
using namespace libcamera;
LOG_DEFINE_CATEGORY(HAL)

static PixelFormat pipelineFormat(int format)
{
    if (format == HAL_PIXEL_FORMAT_BLOB) return formats::MJPEG;
    if (format == HAL_PIXEL_FORMAT_IMPLEMENTATION_DEFINED ||
        format == HAL_PIXEL_FORMAT_YCBCR_420_888) return formats::NV12;
    return {};
}

static void plan(bool jpeg, bool analysis, bool sameSize = false)
{
    camera3_stream_t preview{}, still{}, analyse{};
    preview.width=1280; preview.height=960;
    preview.format=HAL_PIXEL_FORMAT_IMPLEMENTATION_DEFINED;
    preview.usage=GRALLOC_USAGE_HW_COMPOSER | GRALLOC_USAGE_HW_TEXTURE;
    still.width=sameSize ? 1280 : 1600; still.height=sameSize ? 960 : 1200;
    still.format=jpeg ? HAL_PIXEL_FORMAT_BLOB : HAL_PIXEL_FORMAT_YCBCR_420_888;
    still.usage=GRALLOC_USAGE_SW_READ_OFTEN;
    analyse.width=640; analyse.height=480; analyse.format=HAL_PIXEL_FORMAT_YCBCR_420_888;
    analyse.usage=GRALLOC_USAGE_SW_READ_OFTEN;
    camera3_stream_t *streams[]={&preview,&still,&analyse};
    camera3_stream_configuration_t request{};
    request.num_streams=analysis ? 3 : 2; request.streams=streams;
    std::vector<Camera3StreamConfig> configs;
    camera3_stream_t *blob=nullptr;
    assert(a9PlanCamera3Streams(&request,pipelineFormat,configs,blob)==0);
    assert(configs.size()==1 && configs[0].config.pixelFormat==formats::NV12);
    assert(configs[0].config.size==Size(still.width,still.height));
    assert(configs[0].streams.size()==request.num_streams);
    assert(configs[0].streams[0].type==CameraStream::Type::Internal);
    configs[0].config.stride=still.width;
    for (const auto &mapping : configs[0].streams) {
        const auto *s=mapping.stream;
        StreamConfiguration output=configs[0].config;
        output.size={s->width,s->height};
        output.pixelFormat=a9CameraOutputFormat(s->format,s->usage,pipelineFormat(s->format));
        if (s==&preview) assert(output.pixelFormat==formats::ABGR8888);
        else assert(output.pixelFormat==(jpeg && s==&still ? formats::MJPEG : formats::NV12));
        if (output.pixelFormat==formats::MJPEG) {
            PostProcessorJpeg processor(nullptr);
            assert(processor.configure(configs[0].config,output)==0);
        } else {
            PostProcessorYuv processor;
            assert(processor.configure(configs[0].config,output)==0);
        }
        a9OverrideCameraPreviewFormat(*mapping.stream,output.pixelFormat);
        if (s==&preview) assert(s->format==HAL_PIXEL_FORMAT_RGBA_8888);
    }
    printf("PASS production stream plan/configure: RGBA preview + %s%s %ux%u NV12 source\n",
        jpeg ? "BLOB" : "YUV still",analysis ? " + YUV analysis" : "",still.width,still.height);
}

static UniqueFD temporary(size_t length)
{
    char path[]="/tmp/a9-preview-XXXXXX";
    UniqueFD fd(mkstemp(path)); assert(fd.isValid()); unlink(path);
    assert(ftruncate(fd.get(),length)==0); return fd;
}

static void pixels(bool rgb)
{
    constexpr unsigned sw=1600,sh=1200,dw=1280,dh=960;
    constexpr size_t sourceY=sw*sh,sourceLength=sourceY*3/2;
    SharedFD fd(temporary(sourceLength));
    auto *src=static_cast<uint8_t*>(mmap(nullptr,sourceLength,PROT_READ|PROT_WRITE,MAP_SHARED,fd.get(),0));
    assert(src!=MAP_FAILED);
    for(unsigned y=0;y<sh;y++) for(unsigned x=0;x<sw;x++)
        src[y*sw+x]=x<sw/4 ? 0 : x<sw/2 ? 255 : x<3*sw/4 ? 76 : 128;
    memset(src+sourceY,128,sourceY/2);
    for(unsigned y=0;y<sh/2;y++) for(unsigned x=sw/2;x<3*sw/4;x+=2) {
        src[sourceY+y*sw+x]=85; src[sourceY+y*sw+x+1]=255;
    }
    std::vector<FrameBuffer::Plane> planes{{fd,0,sourceY},{fd,sourceY,sourceY/2}};
    FrameBuffer input(planes);
    unsigned stride=rgb ? dw*4+64 : dw+64;
    unsigned offset=64, ySize=stride*dh, uvOffset=offset+ySize+128;
    size_t length=rgb ? offset+ySize+64 : uvOffset+stride*dh/2+64;
    UniqueFD out=temporary(length);
    auto *dst=static_cast<uint8_t*>(mmap(nullptr,length,PROT_READ|PROT_WRITE,MAP_SHARED,out.get(),0));
    assert(dst!=MAP_FAILED); memset(dst,0x5a,length);
    cros_gralloc_handle handle{};
    handle.version=sizeof(native_handle_t); handle.numFds=rgb ? 1 : 2;
    handle.numInts=(sizeof(handle)-sizeof(native_handle_t))/sizeof(int)-handle.numFds;
    handle.fds[0]=out.get(); handle.fds[1]=out.get();
    handle.magic=0xABCDDCBA; handle.width=dw; handle.height=dh;
    handle.num_planes=rgb ? 1 : 2; handle.format=rgb ? formats::ABGR8888.fourcc() : formats::NV12.fourcc();
    handle.strides[0]=stride; handle.offsets[0]=offset; handle.sizes[0]=ySize;
    if(!rgb) {handle.strides[1]=stride;handle.offsets[1]=uvOffset;handle.sizes[1]=stride*dh/2;}
    camera3_stream_buffer_t cameraBuffer{}; cameraBuffer.acquire_fence=-1;
    Camera3RequestDescriptor::StreamBuffer buffer(nullptr,cameraBuffer,nullptr);
    buffer.srcBuffer=&input;
    buffer.dstBuffer=std::make_unique<CameraBuffer>(&handle,rgb ? formats::ABGR8888 : formats::NV12,Size(dw,dh),PROT_READ|PROT_WRITE);
    assert(buffer.dstBuffer->isValid());
    StreamConfiguration inCfg,outCfg;
    inCfg.size={sw,sh};inCfg.stride=sw;inCfg.pixelFormat=formats::NV12;
    outCfg.size={dw,dh};outCfg.pixelFormat=rgb ? formats::ABGR8888 : formats::NV12;
    PostProcessorYuv processor;
    assert(processor.configure(inCfg,outCfg)==0);
    bool complete=false;
    processor.processComplete.connect(&complete,[&](auto *result,PostProcessor::Status status) {
        assert(result==&buffer && status==PostProcessor::Status::Success);complete=true;
    });
    processor.process(&buffer); assert(complete);
    for(unsigned y=0;y<dh;y++) {
        auto *row=dst+offset+y*stride;
        if(rgb) {
            for(unsigned c=0;c<3;c++) {assert(row[100*4+c]<=3);assert(row[480*4+c]>=252);}
            assert(row[800*4]>=250 && row[800*4+1]<=4 && row[800*4+2]<=4);
            for(unsigned c=0;c<3;c++) assert(abs(int(row[1120*4+c])-128)<=3);
            for(unsigned x=0;x<dw;x++) assert(row[x*4+3]==255);
        } else {assert(row[100]==0 && row[480]==255 && row[800]==76 && row[1120]==128);}
        for(unsigned x=dw*(rgb ? 4 : 1);x<stride;x++) assert(row[x]==0x5a);
    }
    for(unsigned i=0;i<offset;i++) assert(dst[i]==0x5a);
    if(!rgb) {
        // libyuv's bilinear UV scaler rounds by up to two code values.
        assert(abs(int(dst[uvOffset+800])-85)<=2 && dst[uvOffset+801]>=253);
    }
    munmap(dst,length);munmap(src,sourceLength);
    printf("PASS production %s scale1600x1200->1280x960, full-range black/white/red/neutral, padded native layout\n",rgb ? "RGBA" : "NV12");
}
int main()
{
    plan(false,true);plan(true,true);plan(true,false,true);
    pixels(true);pixels(false);
    assert(a9CameraOutputFormat(34,0x20003,formats::NV12)==formats::NV12);
    assert(a9CameraOutputFormat(34,0x30003,formats::NV12)==formats::NV12);
    assert(a9CameraOutputFormat(35,0x20933,formats::NV12)==formats::NV12);
}
