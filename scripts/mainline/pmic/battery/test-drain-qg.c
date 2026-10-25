/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include "drain-qg.h"
int main(void)
{
 struct a9_qg_config c; short fifo[8]={1000,2000,-1000}; long long raw; unsigned int ms;
 assert(!a9_qg_config((7<<3)|6,10,2,&c));
 assert(c.length==8 && c.samples==128 && c.interval_ms==100 && c.full_ms==102400 && c.wake_ms==19200);
 assert(!a9_qg_batch(&c,3,fifo,32,32000,41600,&raw,&ms));
 assert(ms==41600 && raw==28800000);
 /* Long CPU sleep is valid when represented by actual hardware samples. */
 assert(!a9_qg_batch(&c,3,fifo,0,0,38400,&raw,&ms));
 assert(raw==25600000 && ms==38400);
 assert(a9_qg_batch(&c,0,fifo,0,0,38400,&raw,&ms)==-ENODATA);
 assert(a9_qg_batch(&c,8,fifo,0,0,102400,&raw,&ms)==-EOVERFLOW);
 assert(a9_qg_batch(&c,0,fifo,128,0,1000,&raw,&ms)==-EINVAL);
 assert(a9_qg_batch(&c,0,fifo,10,327671,1000,&raw,&ms)==-ERANGE);
 fifo[0]=-32768;assert(a9_qg_batch(&c,1,fifo,0,0,12800,&raw,&ms)==-ENODATA);
 assert(a9_qg_config(0,1,0,&c)==-EOPNOTSUPP);
 assert(a9_qg_config(63,0,7,&c)==-EINVAL);
 puts("QG batches PASS: hardware coverage across suspend, signed integral, missing data/overflow/refusals");
}
