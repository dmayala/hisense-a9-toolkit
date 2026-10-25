/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <stdio.h>
#include "../driver/a9-qsee-log-ring.h"
int main(void)
{
    struct a9_qsee_log_span s;
    struct a9_qsee_log_cursor c={0,0}, end={0,5};
    assert(a9_qsee_log_span(c,end,12,3,&s)==1 && s.start==0 && s.length==3);
    c=s.next;
    assert(a9_qsee_log_span(c,end,12,8,&s)==1 && s.start==3 && s.length==2);
    c=s.next;
    assert(a9_qsee_log_span(c,end,12,8,&s)==0);
    c=(struct a9_qsee_log_cursor){0,10};end=(struct a9_qsee_log_cursor){1,4};
    assert(a9_qsee_log_span(c,end,12,12,&s)==1 && s.start==10 && s.length==2);
    assert(s.next.wrap==1 && s.next.offset==0);
    assert(a9_qsee_log_span(s.next,end,12,12,&s)==1 && s.length==4);
    c=(struct a9_qsee_log_cursor){0,0};end=(struct a9_qsee_log_cursor){3,11};
    assert(a9_qsee_log_span(c,end,12,12,&s)==1 && s.start==0 && s.length==11 && s.next.wrap==3);
    c=(struct a9_qsee_log_cursor){65535,10};end=(struct a9_qsee_log_cursor){0,4};
    assert(a9_qsee_log_span(c,end,12,12,&s)==1 && s.length==2 && s.next.wrap==0);
    end.offset=12;
    assert(a9_qsee_log_span(c,end,12,12,&s)==-1);
    end=(struct a9_qsee_log_cursor){0,2};c=(struct a9_qsee_log_cursor){0,4};
    assert(a9_qsee_log_span(c,end,12,12,&s)==-1);
    assert(a9_qsee_log_span(c,end,0,12,&s)==-1);
    puts("PASS QSEE ring: incremental, wrap, overrun, counter-wrap and malformed bounds");
}
