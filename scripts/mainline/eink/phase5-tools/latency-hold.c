/* SPDX-License-Identifier: MIT
 * User-run bounded CPU idle latency experiment. No E Ink/audio/device control.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/timerfd.h>
#include <time.h>
#include <unistd.h>
static volatile sig_atomic_t stop;
static void stopped(int sig) { (void)sig; stop=1; }
int main(int argc,char **argv)
{
 char *end;long seconds;int fd,rc=1,timer=-1;int32_t value=0,effective=-1;
 struct stat st;struct itimerspec expiry={0};sigset_t blocked,waitmask;struct sigaction sa={.sa_handler=stopped};
 if(argc!=2){fprintf(stderr,"usage: latency-hold SECONDS (1..900)\n");return 2;}
 errno=0;seconds=strtol(argv[1],&end,10);
 if(errno||!*argv[1]||*end||seconds<1||seconds>900){fprintf(stderr,"invalid duration\n");return 2;}
 sigemptyset(&sa.sa_mask);
 if(sigaction(SIGINT,&sa,0)||sigaction(SIGTERM,&sa,0)||sigaction(SIGHUP,&sa,0)){perror("sigaction");return 1;}
 sigemptyset(&blocked);sigaddset(&blocked,SIGINT);sigaddset(&blocked,SIGTERM);sigaddset(&blocked,SIGHUP);
 if(sigprocmask(SIG_BLOCK,&blocked,&waitmask)){perror("sigprocmask");return 1;}
 sigdelset(&waitmask,SIGINT);sigdelset(&waitmask,SIGTERM);sigdelset(&waitmask,SIGHUP);
 fd=open("/dev/cpu_dma_latency",O_RDWR|O_CLOEXEC|O_NOFOLLOW);
 if(fd<0){perror("open /dev/cpu_dma_latency");return 1;}
 if(fstat(fd,&st)||!S_ISCHR(st.st_mode)){fprintf(stderr,"CPU latency path is not a character device\n");goto out;}
 if(write(fd,&value,sizeof(value))!=(ssize_t)sizeof(value)){fprintf(stderr,"write zero latency did not accept four bytes\n");goto out;}
 if(read(fd,&effective,sizeof(effective))!=(ssize_t)sizeof(effective)||effective!=0){fprintf(stderr,"zero-latency readback failed (effective %d)\n",effective);goto out;}
 timer=timerfd_create(CLOCK_BOOTTIME,TFD_CLOEXEC|TFD_NONBLOCK);
 if(timer<0){perror("timerfd_create");goto out;}
 if(clock_gettime(CLOCK_BOOTTIME,&expiry.it_value)){perror("clock_gettime");goto out;}
 expiry.it_value.tv_sec+=seconds;
 if(timerfd_settime(timer,TFD_TIMER_ABSTIME,&expiry,0)){perror("timerfd_settime");goto out;}
 printf("READY pid=%ld requested_us=0 effective_us=0 seconds=%ld; own FD held until exit\n",(long)getpid(),seconds);fflush(stdout);
 while(!stop){
  struct pollfd p={.fd=timer,.events=POLLIN};
  /* Atomic signal unmask+wait avoids a TERM between loop test and sleep. */
  int n=ppoll(&p,1,0,&waitmask);
  if(n<0&&errno==EINTR)continue;
  if(n<0){perror("ppoll");goto out;}
  if(p.revents&POLLIN)break;
  if(p.revents&(POLLERR|POLLHUP|POLLNVAL)){fprintf(stderr,"expiry timer failed\n");goto out;}
 }
 rc=0;
out:
 if(timer>=0)close(timer);
 if(close(fd)){perror("close latency request");rc=1;}
 if(!rc)puts("RELEASED own CPU latency request");
 return rc;
}
