// Host-only syscall model executes the real probe. No device nodes are opened.
#define main linear_main
#define open model_open
#define close model_close
#define ioctl model_ioctl
#define mmap model_mmap
#define munmap model_munmap
#define poll model_poll
#define write model_write
#define fsync model_fsync
#include "linear.c"
#undef main
#include <assert.h>
#include <stdarg.h>

static bool deny_heap,bad_capture,reject_tuning;
static unsigned encoder_opens,input_queues,ebd_count,capture_done,starts,ends;
static uint8_t *first_input,*first_capture;
static unsigned controls_set;
int model_open(const char *path,int flags,...) {
    (void)flags;
    if(!strcmp(path,"fake.h264"))return 5;
    if(!strcmp(path,"/dev/dma_heap/system")){
        if(deny_heap){errno=EACCES;return -1;}return 6;
    }
    if(!strcmp(path,"/dev/video0")){++encoder_opens;return 7;}
    errno=ENOENT;return -1;
}
int model_close(int fd){(void)fd;return 0;}
int model_fsync(int fd){assert(fd==5);return 0;}
void *model_mmap(void *p,size_t n,int prot,int flags,int fd,off_t off) {
    (void)p;(void)prot;(void)flags;(void)fd;
    void *memory=malloc(n);assert(memory);memset(memory,0,n);
    if(off==(off_t)OUTPUT*0x1000000 || (fd==20 && !off))first_input=memory;
    if(off==(off_t)CAPTURE*0x1000000)first_capture=memory;
    return memory;
}
int model_munmap(void *p,size_t n){(void)n;free(p);return 0;}
ssize_t model_write(int fd,const void *p,size_t n){assert(fd==5);assert(p);return (ssize_t)n;}
int model_poll(struct pollfd *fds,nfds_t n,int t){assert(n==1 && fds && t==50);return 1;}
int model_ioctl(int fd,unsigned long request,...) {
    va_list a;va_start(a,request);void *arg=va_arg(a,void *);va_end(a);
    if(fd==6){
        assert(request==DMA_HEAP_IOCTL_ALLOC);
        struct dma_heap_allocation_data *d=arg;
        assert(d->len==147456 && d->fd_flags==(O_RDWR|O_CLOEXEC) && d->heap_flags==0);
        static unsigned slot;d->fd=20+(slot++%4);return 0;
    }
    if(fd>=20){
        assert(request==DMA_BUF_IOCTL_SYNC);
        uint64_t flags=((struct dma_buf_sync *)arg)->flags;
        if(flags==DMA_BUF_SYNC_WRITE)++starts;
        else{assert(flags==(DMA_BUF_SYNC_WRITE|DMA_BUF_SYNC_END));++ends;}
        return 0;
    }
    assert(fd==7);
    if(request==VIDIOC_QUERYCAP){
        struct v4l2_capability *c=arg;strcpy((char *)c->driver,"qcom-venus");
        strcpy((char *)c->card,"Qualcomm Venus video encoder");c->capabilities=V4L2_CAP_VIDEO_M2M_MPLANE;return 0;
    }
    if(request==VIDIOC_S_FMT){
        struct v4l2_format *f=arg;
        if(f->type==OUTPUT){assert(f->fmt.pix_mp.pixelformat==V4L2_PIX_FMT_NV12);f->fmt.pix_mp.width=384;f->fmt.pix_mp.height=256;f->fmt.pix_mp.plane_fmt[0].bytesperline=384;f->fmt.pix_mp.plane_fmt[0].sizeimage=147456;}
        else assert(f->fmt.pix_mp.pixelformat==V4L2_PIX_FMT_H264);
        return 0;
    }
    if(request==VIDIOC_S_SELECTION)return 0;
    if(request==VIDIOC_S_PARM){if(reject_tuning){errno=EINVAL;return -1;}return 0;}
    if(request==VIDIOC_S_CTRL){
        struct v4l2_control *c=arg;
        if(c->id==V4L2_CID_MPEG_VIDEO_GOP_SIZE)assert(c->value==1);
        ++controls_set;
        if(reject_tuning){errno=EINVAL;return -1;}
        return 0;
    }
    if(request==VIDIOC_REQBUFS){assert(((struct v4l2_requestbuffers *)arg)->count==4);return 0;}
    if(request==VIDIOC_QUERYBUF){
        struct v4l2_buffer *b=arg;
        b->m.planes[0].length=b->type==OUTPUT?147456:2097152;
        b->m.planes[0].m.mem_offset=b->type*0x1000000+b->index*0x400000;return 0;
    }
    if(request==VIDIOC_STREAMON || request==VIDIOC_STREAMOFF)return 0;
    if(request==VIDIOC_QBUF){
        struct v4l2_buffer *b=arg;
        if(b->type==OUTPUT){
            assert(++input_queues==1 && b->index==0 && b->m.planes[0].bytesused==147456);
            assert(first_input && first_input[320]==16 && first_input[384*240]==16);
            assert(first_input[98304]==128 && first_input[147455]==128);
            if(b->memory==V4L2_MEMORY_DMABUF)assert(starts==4 && ends==4);
        }
        return 0;
    }
    if(request==VIDIOC_DQBUF){
        struct v4l2_buffer *b=arg;
        assert(input_queues==1);b->index=0;
        if(b->type==OUTPUT){if(!ebd_count++)return 0;errno=EAGAIN;return -1;}
        static const uint8_t header[]={0,0,0,1,0x67,0x42,0,30};
        static const uint8_t frame[]={0,0,1,0x65,1,2,3,4};
        memcpy(first_capture,capture_done?frame:header,8);++capture_done;
        b->m.planes[0].bytesused=bad_capture?2097153:8;return 0;
    }
    assert(!"unexpected ioctl");return -1;
}
static void reset(void){
    encoder_opens=input_queues=ebd_count=capture_done=starts=ends=controls_set=0;
    first_input=first_capture=NULL;
}
int main(void) {
    uint8_t nv12[147456];assert(!fill_nv12(nv12,sizeof(nv12),384,256));
    assert(nv12[320]==16 && nv12[98304]==128 && nv12[147455]==128);
    assert(fill_nv12(nv12,100,384,256)==-EINVAL);
    assert(!has_vcl((uint8_t[]){0,0,1,0x67,1},5));
    assert(has_vcl((uint8_t[]){0,0,0,1,0x65,1},6));
    char *mmap_args[]={"linear","mmap","fake.h264",NULL};
    char *system_args[]={"linear","system","fake.h264",NULL};
    reset();assert(!linear_main(3,mmap_args));assert(input_queues==1 && capture_done==2 && controls_set==8);
    reset();reject_tuning=true;assert(!linear_main(3,mmap_args));
    assert(input_queues==1 && capture_done==2 && controls_set==8);reject_tuning=false;
    reset();deny_heap=true;assert(linear_main(3,system_args)==1);assert(!encoder_opens && !input_queues);deny_heap=false;
    reset();assert(!linear_main(3,system_args));assert(starts==4 && ends==4);
    reset();bad_capture=true;assert(linear_main(3,mmap_args)==1);bad_capture=false;
    puts("LINEAR_NATIVE_MODEL_PASS layout/vcl/mmap/rejected_tuning/system_sync/heap_denial/capture_bounds");
    return 0;
}
