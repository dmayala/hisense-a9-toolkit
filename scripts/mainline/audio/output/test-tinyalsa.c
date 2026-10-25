// SPDX-License-Identifier: GPL-2.0-only
#include <stdlib.h>
#include <errno.h>
struct pcm { int ready; unsigned card, device, flags; };
struct pcm_config;
extern int observe_open(unsigned, unsigned, unsigned);
extern void observe_params(unsigned, unsigned, unsigned);
extern void observe_close(unsigned, unsigned, unsigned);
extern int write_result, write_errno;
extern unsigned write_calls;
struct pcm *pcm_open(unsigned card,unsigned device,unsigned flags,const struct pcm_config *config){(void)config;struct pcm *p=malloc(sizeof(*p));p->card=card;p->device=device;p->flags=flags;p->ready=observe_open(card,device,flags);return p;}
int pcm_is_ready(const struct pcm *p){return p && p->ready;}
int pcm_close(struct pcm *p){observe_close(p->card,p->device,p->flags);free(p);return 0;}
void *pcm_params_get(unsigned c,unsigned d,unsigned f){observe_params(c,d,f);return (void*)1;}
int pcm_write(struct pcm *p,const void *d,unsigned n){(void)p;(void)d;(void)n;write_calls++;errno=write_errno;return write_result;}
int pcm_read(struct pcm *p,void *d,unsigned n){(void)p;(void)d;(void)n;errno=EAGAIN;return -1;}
const char *pcm_get_error(const struct pcm *p){(void)p;return "injected write failure";}
int pcm_get_file_descriptor(const struct pcm *p){(void)p;return 902;}
