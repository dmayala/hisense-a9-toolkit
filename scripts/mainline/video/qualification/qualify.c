// SPDX-License-Identifier: Apache-2.0
// Synthetic-only exact-name Venus qualification. No camera/audio/user-media input.
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES2/gl2.h>
#include <android/log.h>
#include <android/native_window.h>
#include <media/NdkMediaCodec.h>
#include <media/NdkMediaExtractor.h>
#include <media/NdkMediaFormat.h>
#include <media/NdkMediaMuxer.h>
#include <fcntl.h>
#include <inttypes.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define FRAMES 30
#define STEP_US INT64_C(100000)
#define LIMIT (4 * 1024 * 1024)
static int64_t ms(void) { struct timespec t; if (clock_gettime(CLOCK_MONOTONIC,&t)) return -1; return t.tv_sec*INT64_C(1000)+t.tv_nsec/1000000; }
static void mark(int op,int phase,int rc,int frames) {
    printf("HVENC_QUAL op=%d phase=%d rc=%d frames=%d\n",op,phase,rc,frames);fflush(stdout);
    __android_log_print(ANDROID_LOG_INFO,"A9HVEnc","HVENC_QUAL op=%d phase=%d rc=%d frames=%d",op,phase,rc,frames);
}
static void expired(int sig) {
    (void)sig;
    static const char message[]="HVENC_QUAL op=99 phase=1 rc=-110 frames=0\n";
    (void)write(STDOUT_FILENO,message,sizeof(message)-1);_exit(124);
}
static AMediaCodec *named(const char *wanted,int *identity) {
    AMediaCodec *codec=AMediaCodec_createCodecByName(wanted);char *actual=NULL;
    *identity=0;
    if (codec && AMediaCodec_getName(codec,&actual)==AMEDIA_OK && actual) *identity=!strcmp(actual,wanted);
    if (actual) AMediaCodec_releaseName(codec,actual);
    return codec;
}
static AMediaExtractor *extract(const char *path,int *fd,AMediaFormat **format,const char *mime) {
    *fd=open(path,O_RDONLY|O_CLOEXEC|O_NOFOLLOW);struct stat st;
    if (*fd<0 || fstat(*fd,&st) || !S_ISREG(st.st_mode) || st.st_size<=0 || st.st_size>LIMIT) return NULL;
    AMediaExtractor *ex=AMediaExtractor_new();
    if (!ex || AMediaExtractor_setDataSourceFd(ex,*fd,0,st.st_size)) { if(ex)AMediaExtractor_delete(ex);return NULL; }
    if (AMediaExtractor_getTrackCount(ex)!=1) {AMediaExtractor_delete(ex);return NULL;}
    *format=AMediaExtractor_getTrackFormat(ex,0);const char *type=NULL;
    if (!*format || !AMediaFormat_getString(*format,AMEDIAFORMAT_KEY_MIME,&type) || strcmp(type,mime) || AMediaExtractor_selectTrack(ex,0)) {
        AMediaExtractor_delete(ex);return NULL;
    }
    return ex;
}
static int decode(const char *path,bool hevc,int expected,int op,int *frames,int *eos,int *identity) {
    int fd=-1,rc=-1;AMediaFormat *fmt=NULL;AMediaCodec *codec=NULL;
    bool started=false,queued=false;int64_t pts=-1,last=-1,deadline=ms()+10000;
    mark(op,0,0,0);
    AMediaExtractor *ex=extract(path,&fd,&fmt,hevc?"video/hevc":"video/avc");
    if (!ex) goto done;
    codec=named(hevc?"c2.v4l2.hevc.decoder":"c2.v4l2.avc.decoder",identity);
    if (!codec || !*identity) goto done;
    AMediaFormat_setInt32(fmt,AMEDIAFORMAT_KEY_COLOR_FORMAT,0x7f420888);
    rc=AMediaCodec_configure(codec,fmt,NULL,NULL,0);if(rc)goto done;
    rc=AMediaCodec_start(codec);if(rc)goto done;started=true;
    while (!*eos && ms()<deadline) {
        if (!queued) {
            ssize_t index=AMediaCodec_dequeueInputBuffer(codec,10000);
            if (index>=0) {
                size_t cap=0;uint8_t *data=AMediaCodec_getInputBuffer(codec,index,&cap);
                if(!data){rc=-2;break;}
                ssize_t size=AMediaExtractor_readSampleData(ex,data,cap);
                if(size<0) {rc=AMediaCodec_queueInputBuffer(codec,index,0,0,pts+STEP_US,AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM);queued=true;}
                else {pts=AMediaExtractor_getSampleTime(ex);rc=AMediaCodec_queueInputBuffer(codec,index,0,(size_t)size,pts,0);AMediaExtractor_advance(ex);}
                if(rc)break;
            } else if(index!=AMEDIACODEC_INFO_TRY_AGAIN_LATER){rc=(int)index;break;}
        }
        AMediaCodecBufferInfo info={0};ssize_t index=AMediaCodec_dequeueOutputBuffer(codec,&info,10000);
        if(index>=0) {
            if(info.size>0 && !(info.flags&AMEDIACODEC_BUFFER_FLAG_CODEC_CONFIG)) {
                if(info.presentationTimeUs<=last){rc=-3;AMediaCodec_releaseOutputBuffer(codec,index,false);break;}
                last=info.presentationTimeUs;++*frames;
            }
            *eos=!!(info.flags&AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM);
            rc=AMediaCodec_releaseOutputBuffer(codec,index,false);if(rc)break;
        } else if(index==AMEDIACODEC_INFO_OUTPUT_FORMAT_CHANGED) {
            AMediaFormat *out=AMediaCodec_getOutputFormat(codec);if(out)AMediaFormat_delete(out);
        } else if(index!=AMEDIACODEC_INFO_TRY_AGAIN_LATER && index!=AMEDIACODEC_INFO_OUTPUT_BUFFERS_CHANGED){rc=(int)index;break;}
    }
    if(!*eos)rc=-110;else if(*frames!=expected)rc=-4;
done:
    if(started) {mark(op+20,0,rc,*frames);int stopped=AMediaCodec_stop(codec);mark(op+20,1,stopped,*frames);if(!rc)rc=stopped;}
    if(codec) {mark(op+40,0,rc,*frames);int released=AMediaCodec_delete(codec);mark(op+40,1,released,*frames);if(!rc)rc=released;}
    if(ex)AMediaExtractor_delete(ex);if(fmt)AMediaFormat_delete(fmt);if(fd>=0)close(fd);
    mark(op,1,rc,*frames);return rc;
}
struct output {int track,started,frames,eos;int64_t bytes,last;};
static int drain(AMediaCodec *codec,AMediaMuxer *mux,struct output *out,int64_t wait) {
    for(int n=0;n<100;++n) {
        AMediaCodecBufferInfo info={0};ssize_t index=AMediaCodec_dequeueOutputBuffer(codec,&info,wait);wait=0;
        if(index==AMEDIACODEC_INFO_TRY_AGAIN_LATER)return 0;
        if(index==AMEDIACODEC_INFO_OUTPUT_BUFFERS_CHANGED)continue;
        if(index==AMEDIACODEC_INFO_OUTPUT_FORMAT_CHANGED) {
            if(out->started)return -10;
            AMediaFormat *format=AMediaCodec_getOutputFormat(codec);if(!format)return -11;
            out->track=(int)AMediaMuxer_addTrack(mux,format);AMediaFormat_delete(format);
            if(out->track<0)return -12;
            int rc=AMediaMuxer_start(mux);if(rc)return rc;out->started=1;continue;
        }
        if(index<0)return (int)index;
        int rc=0;
        if(info.size>0 && !(info.flags&AMEDIACODEC_BUFFER_FLAG_CODEC_CONFIG)) {
            size_t cap=0;uint8_t *data=AMediaCodec_getOutputBuffer(codec,index,&cap);
            if(!data || info.offset<0 || info.size<0 || (size_t)info.offset>cap || (size_t)info.size>cap-(size_t)info.offset || !out->started || info.presentationTimeUs<=out->last || out->bytes+info.size>LIMIT-65536 || out->frames>=FRAMES)rc=-13;
            else {
                rc=AMediaMuxer_writeSampleData(mux,out->track,data,&info);
                if(!rc) {out->bytes+=info.size;out->last=info.presentationTimeUs;++out->frames;if(out->frames==1)mark(8,2,0,1);}
            }
        }
        out->eos=!!(info.flags&AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM);
        int released=AMediaCodec_releaseOutputBuffer(codec,index,false);if(!rc)rc=released;
        if(rc || out->eos)return rc;
    }
    return -14;
}
static int encode(const char *path,int *identity,int *inputs,struct output *out,int *mux_ok) {
    int rc=-1,fd=-1;bool started=false;AMediaCodec *codec=NULL;AMediaFormat *fmt=NULL;AMediaMuxer *mux=NULL;ANativeWindow *window=NULL;
    EGLDisplay display=EGL_NO_DISPLAY;EGLContext context=EGL_NO_CONTEXT;EGLSurface surface=EGL_NO_SURFACE;
    mark(3,0,0,0);codec=named("c2.v4l2.avc.encoder",identity);if(!codec || !*identity)goto done;
    fmt=AMediaFormat_new();if(!fmt)goto done;
    AMediaFormat_setString(fmt,AMEDIAFORMAT_KEY_MIME,"video/avc");
    AMediaFormat_setInt32(fmt,AMEDIAFORMAT_KEY_WIDTH,320);AMediaFormat_setInt32(fmt,AMEDIAFORMAT_KEY_HEIGHT,240);
    AMediaFormat_setInt32(fmt,AMEDIAFORMAT_KEY_FRAME_RATE,10);AMediaFormat_setInt32(fmt,AMEDIAFORMAT_KEY_BIT_RATE,300000);
    AMediaFormat_setInt32(fmt,"profile",1);AMediaFormat_setInt32(fmt,"max-bframes",0);
    AMediaFormat_setInt32(fmt,AMEDIAFORMAT_KEY_I_FRAME_INTERVAL,1);AMediaFormat_setInt32(fmt,AMEDIAFORMAT_KEY_COLOR_FORMAT,0x7f000789);
    rc=AMediaCodec_configure(codec,fmt,NULL,NULL,AMEDIACODEC_CONFIGURE_FLAG_ENCODE);mark(3,1,rc,0);if(rc)goto done;
    mark(4,0,0,0);rc=AMediaCodec_createInputSurface(codec,&window);mark(4,1,rc,0);if(!window && !rc)rc=-25;if(rc)goto done;
    mark(14,0,0,0);rc=AMediaCodec_start(codec);mark(14,1,rc,0);if(rc)goto done;started=true;
    fd=open(path,O_CREAT|O_EXCL|O_RDWR|O_CLOEXEC|O_NOFOLLOW,0600);if(fd<0){rc=-15;goto done;}
    mux=AMediaMuxer_new(fd,AMEDIAMUXER_OUTPUT_FORMAT_MPEG_4);if(!mux){rc=-16;goto done;}
    mark(5,0,0,0);display=eglGetDisplay(EGL_DEFAULT_DISPLAY);EGLint count=0;EGLConfig config;
    const EGLint attrs[]={EGL_RED_SIZE,8,EGL_GREEN_SIZE,8,EGL_BLUE_SIZE,8,EGL_RENDERABLE_TYPE,EGL_OPENGL_ES2_BIT,EGL_SURFACE_TYPE,EGL_WINDOW_BIT,0x3142,1,EGL_NONE};
    const EGLint context_attrs[]={EGL_CONTEXT_CLIENT_VERSION,2,EGL_NONE};
    if(display==EGL_NO_DISPLAY || !eglInitialize(display,NULL,NULL) || !eglBindAPI(EGL_OPENGL_ES_API) || !eglChooseConfig(display,attrs,&config,1,&count) || count!=1){rc=-17;goto done;}
    context=eglCreateContext(display,config,EGL_NO_CONTEXT,context_attrs);surface=eglCreateWindowSurface(display,config,window,NULL);
    if(context==EGL_NO_CONTEXT || surface==EGL_NO_SURFACE || !eglMakeCurrent(display,surface,surface,context)){rc=-18;goto done;}
    PFNEGLPRESENTATIONTIMEANDROIDPROC presentation=(PFNEGLPRESENTATIONTIMEANDROIDPROC)eglGetProcAddress("eglPresentationTimeANDROID");
    if(!presentation){rc=-19;goto done;}mark(5,1,0,0);
    int64_t deadline=ms()+20000;
    for(int frame=0;frame<FRAMES && ms()<deadline;++frame) {
        glViewport(0,0,320,240);glDisable(GL_SCISSOR_TEST);glClearColor((frame%10)/10.0f,0.2f,0.7f,1);glClear(GL_COLOR_BUFFER_BIT);
        glEnable(GL_SCISSOR_TEST);glScissor((frame*11)%240,40,80,160);glClearColor(0.9f,0.8f,0.1f,1);glClear(GL_COLOR_BUFFER_BIT);glDisable(GL_SCISSOR_TEST);
        if(glGetError()!=GL_NO_ERROR || !presentation(display,surface,frame*STEP_US*1000)){rc=-20;goto done;}
        if(frame==0)mark(6,0,0,0);
        if(!eglSwapBuffers(display,surface)){rc=-21;goto done;}
        ++*inputs;if(frame==0)mark(6,1,0,1);
        rc=drain(codec,mux,out,10000);if(rc)goto done;
    }
    if(*inputs!=FRAMES){rc=-110;goto done;}
    mark(7,0,0,*inputs);rc=AMediaCodec_signalEndOfInputStream(codec);mark(7,1,rc,*inputs);if(rc)goto done;
    mark(15,0,0,out->frames);
    while(!out->eos && ms()<deadline){rc=drain(codec,mux,out,10000);if(rc)goto done;}
    if(!out->eos)rc=-110;else if(out->frames!=FRAMES || out->bytes<=0)rc=-23;
    mark(15,1,rc,out->frames);
done:
    if(mux && out->started) {mark(9,0,rc,out->frames);int stopped=AMediaMuxer_stop(mux);mark(9,1,stopped,out->frames);*mux_ok=stopped==0;if(!rc)rc=stopped;}
    if(display!=EGL_NO_DISPLAY) {
        eglMakeCurrent(display,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT);
        if(surface!=EGL_NO_SURFACE)eglDestroySurface(display,surface);
        if(context!=EGL_NO_CONTEXT)eglDestroyContext(display,context);
        eglTerminate(display);eglReleaseThread();
    }
    if(window)ANativeWindow_release(window);
    if(started){mark(10,0,rc,out->frames);int stopped=AMediaCodec_stop(codec);mark(10,1,stopped,out->frames);if(!rc)rc=stopped;}
    if(codec){mark(11,0,rc,out->frames);int released=AMediaCodec_delete(codec);mark(11,1,released,out->frames);if(!rc)rc=released;}
    if(mux){int released=AMediaMuxer_delete(mux);if(!rc)rc=released;}
    if(fmt)AMediaFormat_delete(fmt);
    if(fd>=0){if(fsync(fd) && !rc)rc=-24;close(fd);}
    return rc;
}
static int inspect(const char *path,int *samples,int64_t *duration,int64_t *span) {
    int fd=-1,rc=-1;AMediaFormat *fmt=NULL;AMediaExtractor *ex=extract(path,&fd,&fmt,"video/avc");
    if(!ex)goto done;
    int64_t first=-1,last=-1;
    if(!AMediaFormat_getInt64(fmt,AMEDIAFORMAT_KEY_DURATION,duration) || *duration<=0)goto done;
    do {
        int64_t pts=AMediaExtractor_getSampleTime(ex);if(pts<0)break;
        if(pts<=last || AMediaExtractor_getSampleSize(ex)<=0)goto done;
        if(first<0)first=pts;last=pts;++*samples;
        if(*samples>FRAMES)goto done;
    } while(AMediaExtractor_advance(ex));
    *span=last-first;
    if(*samples==FRAMES && *duration>=2800000 && *duration<=4000000 && *span>=2800000 && *span<=3100000)rc=0;
done:
    if(ex)AMediaExtractor_delete(ex);if(fmt)AMediaFormat_delete(fmt);if(fd>=0)close(fd);return rc;
}
int main(int argc,char **argv) {
    if(argc!=2)return 2;
    struct sigaction sa={0};sa.sa_handler=expired;sigemptyset(&sa.sa_mask);sigaction(SIGALRM,&sa,NULL);alarm(50);
    char avc[1024],hevc[1024],mp4[1024];
    if(snprintf(avc,sizeof(avc),"%s/avc.mp4",argv[1])>=(int)sizeof(avc) || snprintf(hevc,sizeof(hevc),"%s/hevc.mp4",argv[1])>=(int)sizeof(hevc) || snprintf(mp4,sizeof(mp4),"%s/output.mp4",argv[1])>=(int)sizeof(mp4))return 2;
    int avcf=0,avce=0,avcn=0,hevcf=0,hevce=0,hevcn=0,encn=0,inputs=0,muxok=0,samples=0,roundf=0,rounde=0,roundn=0;
    int64_t duration=0,span=0;struct output out={.track=-1,.last=-1};
    int rc=decode(avc,false,20,1,&avcf,&avce,&avcn);
    if(!rc)rc=decode(hevc,true,20,2,&hevcf,&hevce,&hevcn);
    if(!rc)rc=encode(mp4,&encn,&inputs,&out,&muxok);
    if(!rc){mark(12,0,0,0);rc=inspect(mp4,&samples,&duration,&span);mark(12,1,rc,samples);}
    if(!rc)rc=decode(mp4,false,FRAMES,13,&roundf,&rounde,&roundn);
    char result[1024];snprintf(result,sizeof(result),"HVENC_RESULT status=%d avc_frames=%d avc_eos=%d avc_named=%d hevc_frames=%d hevc_eos=%d hevc_named=%d encoder_named=%d surface_frames=%d encoded_frames=%d encoder_eos=%d muxer_ok=%d mp4_frames=%d duration_us=%" PRId64 " pts_span_us=%" PRId64 " roundtrip_frames=%d roundtrip_eos=%d roundtrip_named=%d bytes=%" PRId64,
        rc,avcf,avce,avcn,hevcf,hevce,hevcn,encn,inputs,out.frames,out.eos,muxok,samples,duration,span,roundf,rounde,roundn,out.bytes);
    puts(result);fflush(stdout);__android_log_write(ANDROID_LOG_INFO,"A9HVEnc",result);alarm(0);
    return rc==0 ? 0 : 1;
}
