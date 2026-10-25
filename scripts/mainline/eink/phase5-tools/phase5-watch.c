/* SPDX-License-Identifier: MIT
 * Bounded trace-pipe recorder. Trigger on the post-kickoff MSM phase5 wait.
 * Reads events only, controls its caller-created trace instance, no panel APIs.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#define CAP (8u*1024u*1024u)
#define LINE 8192
static volatile sig_atomic_t stopped;
static void on_signal(int sig){(void)sig;stopped=1;}
struct recorder {
 char *ring;size_t pos,used;uint64_t dropped,lines;
 bool program,active,triggered,finished;
 uint64_t begin,trigger_at,trigger_received,end;const char *reason;
 unsigned threshold_us;
};
static uint64_t mono(void){struct timespec t;if(clock_gettime(CLOCK_MONOTONIC,&t))return 0;return (uint64_t)t.tv_sec*1000000000+t.tv_nsec;}
static int allwrite(int fd,const void *buf,size_t n){const char *p=buf;while(n){ssize_t w=write(fd,p,n);if(w<0&&errno==EINTR)continue;if(w<=0)return -1;p+=w;n-=(size_t)w;}return 0;}
static bool event(const char *p,const char *name){size_t n=strlen(name);return !strncmp(p,name,n)&&p[n]==':';}
/* Trace clock mono, decimal seconds with up to9 fractional digits. */
static const char *timestamp(const char *line,uint64_t *stamp)
{
 const char *p=strchr(line,']');if(!p)return NULL;
 for(;*p;p++){
  if(*p<'0'||*p>'9')continue;
  char *e;errno=0;unsigned long long s=strtoull(p,&e,10);
  if(errno||*e!='.'||s>UINT64_MAX/1000000000)continue;
  const char *f=e+1;uint64_t ns=0;unsigned n=0;
  while(*f>='0'&&*f<='9'&&n<9){ns=ns*10+(unsigned)(*f-'0');f++;n++;}
  if(!n||*f!=':')continue;
  while(n++<9)ns*=10;
  if((uint64_t)s*1000000000>UINT64_MAX-ns)continue;
  *stamp=(uint64_t)s*1000000000+ns;f++;
  while(*f==' '||*f=='\t')f++;
  return f;
 }
 return NULL;
}
static void ringadd(struct recorder *r,const char *p,size_t n)
{
 for(size_t i=0;i<n;i++){
  r->ring[r->pos]=p[i];r->pos=(r->pos+1)%CAP;
  if(r->used<CAP)r->used++;else r->dropped++;
 }
}
static void check_age(struct recorder *r,uint64_t now)
{
 if(r->active&&!r->triggered&&now>=r->begin&&now-r->begin>=(uint64_t)r->threshold_us*1000){
  r->triggered=true;r->trigger_at=now;r->trigger_received=mono();r->reason="phase5 exceeded threshold";
 }
}
static void parsed(struct recorder *r,const char *line)
{
 uint64_t at;const char *e=timestamp(line,&at);r->lines++;
 if(!e)return;
 /* Start and flush/wait markers are filtered to the sole waiter by tracefs. */
 if(event(e,"msm_atomic_commit_tail_start")){
  if(r->active&&r->triggered){r->reason="new tail before prior wait end";r->finished=true;return;}
  r->program=false;r->active=false;
 }else if(event(e,"msm_atomic_flush_commit"))r->program=true;
 else if(event(e,"msm_atomic_wait_flush_start")&&r->program){r->begin=at;r->active=true;r->program=false;}
 check_age(r,at);
 if(event(e,"msm_atomic_wait_flush_finish")&&r->active){
  if(at>=r->begin&&at-r->begin>=(uint64_t)r->threshold_us*1000){
   r->triggered=true;r->end=at;r->reason="completed phase5 exceeded threshold";r->finished=true;
  }else {
   /* An event from another CPU can be emitted/read ahead of the waiter end.
    * A short completed wait cancels a provisional cross-CPU trigger. */
   r->triggered=false;r->trigger_at=0;r->trigger_received=0;
  }
  r->active=false;
 }
}
static int save(struct recorder *r,const char *out)
{
 char path[PATH_MAX];int fd;
 if(snprintf(path,sizeof(path),"%s/trace-private.txt",out)>=(int)sizeof(path))return -1;
 fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);if(fd<0)return -1;
 size_t start=(r->pos+CAP-r->used)%CAP,n=r->used;
 if(r->dropped){while(n&&r->ring[start]!='\n'){start=(start+1)%CAP;n--;}if(n){start=(start+1)%CAP;n--;}}
 size_t first=CAP-start;if(first>n)first=n;
 int rc=allwrite(fd,r->ring+start,first)||allwrite(fd,r->ring,n-first);if(close(fd))rc=-1;if(rc)return -1;
 if(snprintf(path,sizeof(path),"%s/trigger.json",out)>=(int)sizeof(path))return -1;
 fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC|O_NOFOLLOW,0600);if(fd<0)return -1;
 char b[1400];int len=snprintf(b,sizeof(b),"{\"triggered\":%s,\"threshold_us\":%u,\"phase5_start_ns\":%llu,\"observed_trigger_ns\":%llu,\"phase5_end_ns\":%llu,\"completed_age_us\":%llu,\"reason\":\"%s\",\"parsed_lines\":%llu,\"recorder_capacity\":%u,\"recorder_overwritten_bytes\":%llu}\n",r->triggered?"true":"false",r->threshold_us,(unsigned long long)r->begin,(unsigned long long)r->trigger_at,(unsigned long long)r->end,(unsigned long long)(r->end>=r->begin&&r->end?(r->end-r->begin)/1000:0),r->reason?r->reason:"session ended without trigger",(unsigned long long)r->lines,CAP,(unsigned long long)r->dropped);
 rc=len<0||(size_t)len>=sizeof(b)||allwrite(fd,b,(size_t)len);if(close(fd))rc=-1;return rc?-1:0;
}
static bool number(const char *s,unsigned *n,unsigned max){char *e;errno=0;unsigned long x=strtoul(s,&e,10);if(errno||!*s||*e||!x||x>max)return false;*n=(unsigned)x;return true;}
int main(int argc,char **argv)
{
 bool replay=argc==6&&!strcmp(argv[1],"replay");unsigned seconds=0,threshold=0;
 if(argc!=6||(!replay&&strcmp(argv[1],"capture"))||!number(argv[4],&seconds,900)||!number(argv[5],&threshold,1000000)){
  fprintf(stderr,"usage: phase5-watch capture INSTANCE OUTDIR SECONDS THRESHOLD_US\n       phase5-watch replay TRACE_FILE OUTDIR SECONDS THRESHOLD_US\n");return 2;
 }
 struct recorder r={.threshold_us=threshold};r.ring=malloc(CAP);if(!r.ring){perror("malloc");return 1;}
 int fd=-1,control=-1,rc=1;char p[PATH_MAX];struct sigaction sa={.sa_handler=on_signal};sigemptyset(&sa.sa_mask);
 if(sigaction(SIGINT,&sa,0)||sigaction(SIGTERM,&sa,0)||sigaction(SIGHUP,&sa,0)){perror("sigaction");goto done;}
 if(replay)fd=open(argv[2],O_RDONLY|O_CLOEXEC|O_NOFOLLOW);
 else {
  if(snprintf(p,sizeof(p),"%s/tracing_on",argv[2])>=(int)sizeof(p))goto done;
  control=open(p,O_WRONLY|O_CLOEXEC|O_NOFOLLOW);if(control<0){perror("open tracing control");goto done;}
  if(snprintf(p,sizeof(p),"%s/trace_pipe",argv[2])>=(int)sizeof(p))goto done;
  fd=open(p,O_RDONLY|O_NONBLOCK|O_CLOEXEC|O_NOFOLLOW);
 }
 if(fd<0){perror("open trace input");goto done;}
 uint64_t deadline=mono()+(uint64_t)seconds*1000000000;char buf[16384],line[LINE];size_t used=0;bool eof=false;
 if(!replay&&allwrite(control,"1\n",2)){perror("enable own instance");goto done;}
 puts("READY: phase5 capture active; perform ordinary UI activity; no playback or gate changes made");fflush(stdout);
 while(!stopped&&!eof&&!r.finished){
  if(!replay){
   uint64_t now=mono();
   if(now>=deadline){r.reason="session duration reached";break;}
   if(r.triggered&&now>=r.trigger_received+100000000){r.reason="100ms post-trigger bound; phase5 still open";break;}
   /* While phase5 is open, checking the pipe at5ms works when a partly full per-CPU ring
    * has not reached its normal wakeup watermark. Never use reader wall
    * time to classify a queued trace phase as slow. */
   int ms=r.active?5:1000;
   uint64_t next=deadline;

   if(r.triggered&&r.trigger_received+100000000<next)next=r.trigger_received+100000000;
   if(next>now&&(next-now)/1000000<(uint64_t)ms)ms=(int)((next-now+999999)/1000000);
   if(ms<1)ms=1;
   struct pollfd pollfd={.fd=fd,.events=POLLIN};int n=poll(&pollfd,1,ms);
   if(n<0&&errno==EINTR)continue;
   if(n<0){perror("poll trace");goto done;}
   /* Read nonblocking even after timeout to bypass the wakeup watermark. */
  }
  ssize_t n=read(fd,buf,sizeof(buf));
  if(n<0&&(errno==EINTR||errno==EAGAIN))continue;
  if(n<0){perror("read trace");goto done;}
  if(!n){eof=true;break;}
  /* Preserve entire read chunk so the return/next event already queued is kept. */
  ringadd(&r,buf,(size_t)n);
  for(ssize_t i=0;i<n;i++){
   if(used+1>=LINE){fprintf(stderr,"oversized trace line\n");goto done;}
   line[used++]=buf[i];
   if(buf[i]=='\n'){line[used]=0;if(!r.finished)parsed(&r,line);used=0;}
  }
 }
 if(stopped)r.reason="interrupted; own instance stopped";
 if(control>=0&&allwrite(control,"0\n",2)){perror("stop own instance");goto done;}
 /* Bound final drain to the kernel-instance capacity budget, no endless read. */
 if(!replay)for(size_t drained=0;drained<4u*1024u*1024u;){
  ssize_t n=read(fd,buf,sizeof(buf));if(n<0&&errno==EINTR)continue;if(n<=0)break;ringadd(&r,buf,(size_t)n);drained+=(size_t)n;
 }
 if(save(&r,argv[3])){perror("save bounded trace");goto done;}
 printf("SAVED triggered=%d reason=%s\n",r.triggered,r.reason?r.reason:"no completed slow phase");rc=0;
done:
 if(control>=0){(void)allwrite(control,"0\n",2);close(control);}if(fd>=0)close(fd);free(r.ring);return rc;
}
