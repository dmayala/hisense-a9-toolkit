// SPDX-License-Identifier: Apache-2.0
// One CPU-filled NV12 frame directly to Venus. No EGL, gralloc or Codec2.
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <linux/dma-buf.h>
#include <linux/dma-heap.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define OUTPUT V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE
#define CAPTURE V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE
#define MAX_BUFFERS 16
#define BYTE_LIMIT (4U * 1024 * 1024)
struct buffer { void *p; size_t len; int dma_fd; };
struct queue { unsigned type, memory, count; struct buffer b[MAX_BUFFERS]; bool streaming; };

static void mark(const char *op, int phase, int rc) {
    printf("HVENC_LINEAR op=%s phase=%d rc=%d\n", op, phase, rc);
    fflush(stdout);
}
static void expired(int sig) {
    (void)sig;
    static const char msg[]="HVENC_LINEAR op=alarm phase=1 rc=-110\n";
    ssize_t written=write(STDOUT_FILENO,msg,sizeof(msg)-1);
    (void)written;_exit(124);
}
static int xioctl(int fd, unsigned long op, void *arg) {
    int rc; do { rc=ioctl(fd,op,arg); } while (rc<0 && errno==EINTR); return rc;
}
static int operation(int fd, unsigned long request, void *arg, const char *name) {
    mark(name,0,0);
    int rc=xioctl(fd,request,arg)<0 ? -errno : 0;
    mark(name,1,rc);return rc;
}
static int64_t now_ms(void) {
    struct timespec t; if(clock_gettime(CLOCK_MONOTONIC,&t))return -1;
    return (int64_t)t.tv_sec*1000+t.tv_nsec/1000000;
}
static int open_encoder(void) {
    for(unsigned i=0;i<64;++i) {
        char path[32];snprintf(path,sizeof(path),"/dev/video%u",i);
        int fd=open(path,O_RDWR|O_NONBLOCK|O_CLOEXEC);if(fd<0)continue;
        struct v4l2_capability c={0};
        if(!xioctl(fd,VIDIOC_QUERYCAP,&c) &&
           !strncmp((char *)c.driver,"qcom-venus",sizeof(c.driver)) &&
           !strncmp((char *)c.card,"Qualcomm Venus video encoder",sizeof(c.card)) &&
           ((c.capabilities&V4L2_CAP_DEVICE_CAPS ? c.device_caps : c.capabilities)&V4L2_CAP_VIDEO_M2M_MPLANE)) {
            printf("HVENC_LINEAR node=%u\n",i);return fd;
        }
        close(fd);
    }
    errno=ENODEV;return -1;
}
static int control(int fd, unsigned id, int value) {
    struct v4l2_control c={.id=id,.value=value};
    int rc=xioctl(fd,VIDIOC_S_CTRL,&c)<0 ? -errno : 0;
    printf("HVENC_LINEAR control=%#x value=%d rc=%d\n",id,value,rc);return rc;
}
static int fill_nv12(void *data,size_t len,unsigned stride,unsigned scanlines) {
    if(stride<320 || stride>4096 || scanlines<240 || scanlines>4096 ||
       stride%2 || scanlines%2 || (size_t)stride*scanlines*3/2>len)return -EINVAL;
    size_t uv=(size_t)stride*scanlines;
    memset(data,16,len);memset((uint8_t *)data+uv,128,(size_t)stride*scanlines/2);
    for(unsigned y=0;y<240;++y)
        for(unsigned x=0;x<320;++x)((uint8_t *)data)[(size_t)y*stride+x]=(uint8_t)(16+(x+y)%220);
    return 0;
}
static bool has_vcl(const uint8_t *p,size_t n) {
    for(size_t i=0;i+3<n;++i) {
        if(p[i] || p[i+1])continue;
        size_t nal;
        if(p[i+2]==1)nal=i+3;
        else if(i+4<n && !p[i+2] && p[i+3]==1)nal=i+4;
        else continue;
        unsigned type=p[nal]&31;if(type==1 || type==5)return true;
    }
    return false;
}
static int allocate(int fd,struct queue *q,int heap,unsigned stride,unsigned scanlines) {
    struct v4l2_requestbuffers req={.type=q->type,.memory=q->memory,.count=4};
    int rc=operation(fd,VIDIOC_REQBUFS,&req,q->type==OUTPUT?"input.reqbufs":"capture.reqbufs");
    if(rc)return rc;
    if(req.count<1 || req.count>MAX_BUFFERS)return -EOVERFLOW;
    printf("HVENC_LINEAR queue=%u count=%u memory=%u\n",q->type,req.count,q->memory);
    for(unsigned i=0;i<req.count;++i) {
        struct buffer *b=&q->b[i];b->dma_fd=-1;q->count=i+1;
        struct v4l2_plane plane={0};
        struct v4l2_buffer v={.type=q->type,.memory=q->memory,.index=i,.length=1,.m.planes=&plane};
        rc=operation(fd,VIDIOC_QUERYBUF,&v,"querybuf");if(rc)return rc;
        if(v.length!=1 || !plane.length || plane.length>BYTE_LIMIT)return -EOVERFLOW;
        b->len=plane.length;
        int mapfd=fd;off_t offset=plane.m.mem_offset;
        if(q->memory==V4L2_MEMORY_DMABUF) {
            struct dma_heap_allocation_data a={.len=(b->len+4095)&~UINT64_C(4095),.fd_flags=O_RDWR|O_CLOEXEC};
            rc=operation(heap,DMA_HEAP_IOCTL_ALLOC,&a,"system.alloc");if(rc)return rc;
            b->dma_fd=(int)a.fd;b->len=(size_t)a.len;mapfd=b->dma_fd;offset=0;
        }
        b->p=mmap(NULL,b->len,PROT_READ|PROT_WRITE,MAP_SHARED,mapfd,offset);
        if(b->p==MAP_FAILED){b->p=NULL;return -errno;}
        printf("HVENC_LINEAR queue=%u index=%u allocation=%zu\n",q->type,i,b->len);
        if(q->type==OUTPUT) {
            struct dma_buf_sync sync={.flags=DMA_BUF_SYNC_START|DMA_BUF_SYNC_WRITE};
            if(b->dma_fd>=0 && (rc=operation(b->dma_fd,DMA_BUF_IOCTL_SYNC,&sync,"cpu.begin")))return rc;
            rc=fill_nv12(b->p,b->len,stride,scanlines);if(rc)return rc;
            sync.flags=DMA_BUF_SYNC_END|DMA_BUF_SYNC_WRITE;
            if(b->dma_fd>=0 && (rc=operation(b->dma_fd,DMA_BUF_IOCTL_SYNC,&sync,"cpu.end")))return rc;
        }
    }
    return 0;
}
static int queue_buffer(int fd,struct queue *q,unsigned index,size_t used) {
    struct buffer *b=&q->b[index];
    struct v4l2_plane p={.length=(uint32_t)b->len,.bytesused=(uint32_t)used};
    if(q->memory==V4L2_MEMORY_DMABUF)p.m.fd=b->dma_fd;
    struct v4l2_buffer v={.type=q->type,.memory=q->memory,.index=index,.length=1,.m.planes=&p};
    return operation(fd,VIDIOC_QBUF,&v,q->type==OUTPUT?"input.qbuf":"capture.qbuf");
}
static int release_queue(int fd,struct queue *q) {
    int rc=0;
    if(q->streaming)rc=operation(fd,VIDIOC_STREAMOFF,&q->type,q->type==OUTPUT?"input.streamoff":"capture.streamoff");
    for(unsigned i=0;i<q->count;++i) {
        if(q->b[i].p)munmap(q->b[i].p,q->b[i].len);
        if(q->b[i].dma_fd>=0)close(q->b[i].dma_fd);
    }
    return rc;
}
int main(int argc,char **argv) {
    if(argc!=3 || (strcmp(argv[1],"mmap") && strcmp(argv[1],"system"))) {
        fprintf(stderr,"usage: %s mmap|system NEW_OUTPUT.h264\n",argv[0]);return 2;
    }
    setvbuf(stdout,NULL,_IONBF,0);
    struct sigaction sa={.sa_handler=expired};sigemptyset(&sa.sa_mask);sigaction(SIGALRM,&sa,NULL);alarm(30);
    struct queue input={.type=OUTPUT,.memory=!strcmp(argv[1],"system")?V4L2_MEMORY_DMABUF:V4L2_MEMORY_MMAP};
    struct queue capture={.type=CAPTURE,.memory=V4L2_MEMORY_MMAP};
    int fd=-1,heap=-1,dest=-1,rc=-1;unsigned vcl=0,ebd=0;size_t bytes=0;
    printf("HVENC_LINEAR mode=%s visible_width=320 visible_height=240 frames=1\n",argv[1]);
    dest=open(argv[2],O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);
    if(dest<0){rc=-errno;goto done;}
    // Fail a system-heap permission check BEFORE opening/starting the codec.
    if(input.memory==V4L2_MEMORY_DMABUF) {
        mark("system.open",0,0);heap=open("/dev/dma_heap/system",O_RDONLY|O_CLOEXEC);
        rc=heap<0?-errno:0;mark("system.open",1,rc);if(rc)goto done;
    }
    mark("encoder.open",0,0);fd=open_encoder();rc=fd<0?-errno:0;mark("encoder.open",1,rc);if(rc)goto done;
    struct v4l2_format coded={.type=CAPTURE};
    coded.fmt.pix_mp=(struct v4l2_pix_format_mplane){.width=320,.height=240,.pixelformat=V4L2_PIX_FMT_H264,.field=V4L2_FIELD_NONE,.num_planes=1};
    coded.fmt.pix_mp.plane_fmt[0].sizeimage=2*1024*1024;
    if((rc=operation(fd,VIDIOC_S_FMT,&coded,"capture.format")))goto done;
    struct v4l2_format raw={.type=OUTPUT};
    raw.fmt.pix_mp=(struct v4l2_pix_format_mplane){.width=320,.height=240,.pixelformat=V4L2_PIX_FMT_NV12,.field=V4L2_FIELD_NONE,.num_planes=1};
    if((rc=operation(fd,VIDIOC_S_FMT,&raw,"input.format")))goto done;
    if(raw.fmt.pix_mp.pixelformat!=V4L2_PIX_FMT_NV12 || raw.fmt.pix_mp.num_planes!=1 ||
       coded.fmt.pix_mp.pixelformat!=V4L2_PIX_FMT_H264 || coded.fmt.pix_mp.num_planes!=1){rc=-EINVAL;goto done;}
    unsigned stride=raw.fmt.pix_mp.plane_fmt[0].bytesperline,scanlines=raw.fmt.pix_mp.height;
    size_t filled=(size_t)stride*scanlines*3/2;
    if(stride<320 || stride>4096 || scanlines<240 || scanlines>4096 || stride%2 || scanlines%2 || filled>BYTE_LIMIT){rc=-EINVAL;goto done;}
    printf("HVENC_LINEAR fourcc=%#x stride=%u scanlines=%u uv_offset=%zu filled=%zu sizeimage=%u\n",raw.fmt.pix_mp.pixelformat,stride,scanlines,(size_t)stride*scanlines,filled,raw.fmt.pix_mp.plane_fmt[0].sizeimage);
    struct v4l2_selection crop={.type=V4L2_BUF_TYPE_VIDEO_OUTPUT,.target=V4L2_SEL_TGT_CROP,.r={.width=320,.height=240}};
    if((rc=operation(fd,VIDIOC_S_SELECTION,&crop,"input.crop")))goto done;
    if(crop.r.left || crop.r.top || crop.r.width!=320 || crop.r.height!=240){rc=-EINVAL;goto done;}
    struct v4l2_streamparm parm={.type=OUTPUT};parm.parm.output.timeperframe.numerator=1;parm.parm.output.timeperframe.denominator=10;
    (void)operation(fd,VIDIOC_S_PARM,&parm,"framerate");
    const struct { unsigned id;int value; } controls[]={
        {V4L2_CID_MPEG_VIDEO_H264_PROFILE,V4L2_MPEG_VIDEO_H264_PROFILE_BASELINE},
        {V4L2_CID_MPEG_VIDEO_H264_LEVEL,V4L2_MPEG_VIDEO_H264_LEVEL_4_0},
        {V4L2_CID_MPEG_VIDEO_B_FRAMES,0},{V4L2_CID_MPEG_VIDEO_GOP_SIZE,1},
        {V4L2_CID_MPEG_VIDEO_BITRATE_MODE,V4L2_MPEG_VIDEO_BITRATE_MODE_CBR},
        {V4L2_CID_MPEG_VIDEO_BITRATE,300000},{V4L2_CID_MPEG_VIDEO_H264_MAX_QP,51},
        {V4L2_CID_MPEG_VIDEO_HEADER_MODE,V4L2_MPEG_VIDEO_HEADER_MODE_JOINED_WITH_1ST_FRAME},
    };
    // Codec tuning is best effort: rejected controls retain driver defaults.
    // Format/layout, buffer I/O and streaming failures still stop the probe.
    for(unsigned i=0;i<sizeof(controls)/sizeof(controls[0]);++i)
        (void)control(fd,controls[i].id,controls[i].value);
    if((rc=allocate(fd,&input,heap,stride,scanlines)))goto done;
    if((rc=allocate(fd,&capture,-1,0,0)))goto done;
    for(unsigned i=0;i<capture.count;++i)if((rc=queue_buffer(fd,&capture,i,0)))goto done;
    // Both queues are streaming before the one nonempty input is submitted.
    if((rc=operation(fd,VIDIOC_STREAMON,&capture.type,"capture.streamon")))goto done;
    capture.streaming=true;
    if((rc=operation(fd,VIDIOC_STREAMON,&input.type,"input.streamon")))goto done;
    input.streaming=true;
    if(filled>input.b[0].len){rc=-EOVERFLOW;goto done;}
    if((rc=queue_buffer(fd,&input,0,filled)))goto done;
    int64_t deadline=now_ms()+10000;
    while(!vcl && now_ms()<deadline) {
        struct pollfd p={.fd=fd,.events=POLLIN|POLLOUT|POLLPRI};
        int ready=poll(&p,1,50);if(ready<0 && errno!=EINTR){rc=-errno;goto done;}
        struct v4l2_plane plane={0};
        struct v4l2_buffer b={.type=OUTPUT,.memory=input.memory,.length=1,.m.planes=&plane};
        if(!xioctl(fd,VIDIOC_DQBUF,&b)){
            if(b.index>=input.count || b.flags&V4L2_BUF_FLAG_ERROR || ++ebd>1){rc=-EIO;goto done;}
            mark("input.done",1,0);
        }
        else if(errno!=EAGAIN){rc=-errno;goto done;}
        plane=(struct v4l2_plane){0};
        b=(struct v4l2_buffer){.type=CAPTURE,.memory=capture.memory,.length=1,.m.planes=&plane};
        if(xioctl(fd,VIDIOC_DQBUF,&b)){if(errno==EAGAIN)continue;rc=-errno;goto done;}
        printf("HVENC_LINEAR op=capture.done index=%u used=%u offset=%u flags=%#x\n",b.index,plane.bytesused,plane.data_offset,b.flags);
        if(b.index>=capture.count || b.flags&V4L2_BUF_FLAG_ERROR || plane.data_offset>plane.bytesused || plane.bytesused>capture.b[b.index].len || bytes+plane.bytesused>BYTE_LIMIT){rc=-EOVERFLOW;goto done;}
        size_t n=plane.bytesused-plane.data_offset;
        const uint8_t *data=(uint8_t *)capture.b[b.index].p+plane.data_offset;
        vcl=has_vcl(data,n);
        for(size_t off=0;off<n;) {
            ssize_t written=write(dest,data+off,n-off);
            if(written<0 && errno==EINTR)continue;
            if(written<=0){rc=-EIO;goto done;}off+=(size_t)written;
        }
        bytes+=n;if(!vcl && (rc=queue_buffer(fd,&capture,b.index,0)))goto done;
    }
    rc=vcl?0:-ETIMEDOUT;
done:
    if(fd>=0){
        int stopped=release_queue(fd,&capture);if(!rc)rc=stopped;
        stopped=release_queue(fd,&input);if(!rc)rc=stopped;
        mark("encoder.close",0,0);close(fd);mark("encoder.close",1,0);
    }
    if(heap>=0)close(heap);
    if(dest>=0){if(fsync(dest) && !rc)rc=-errno;close(dest);}
    printf("HVENC_LINEAR_RESULT rc=%d vcl=%u input_done=%u bytes=%zu\n",rc,vcl,ebd,bytes);alarm(0);
    return rc?1:0;
}
