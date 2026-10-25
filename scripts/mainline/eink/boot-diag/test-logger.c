// SPDX-License-Identifier: GPL-2.0-only
/* Host-only test: use a temporary regular file, never logger_main(). */
#define main logger_main
#include "../../boot-deadline/logger.c"
#undef main
#include <assert.h>

static unsigned sync_calls;
static bool sync_error;
static bool ring_test;
int __wrap_fsync(int fd)
{
    char saved[256] = {0};
    ++sync_calls;
    /* The complete critical record must already have reached the file. */
    assert(pread(fd,saved,sizeof(saved)-1,0)>0);
    assert(ring_test || strstr(saved,"phase=") || strstr(saved,"MSM_INSMOD") ||
           strstr(saved,"EXPIRED") || strstr(saved,"A9_VENUS_ENC") ||
           strstr(saved,"A9_VENUS_RECOVERY") || strstr(saved,"A9_HANG_DIAG") || strstr(saved,"A9_RPMB_") || strstr(saved,"A9_ENC_TRACE") || strstr(saved,"ENCODE") || strstr(saved,"HVENC_") || strstr(saved,"context fault") ||
           strstr(saved,"sys.powerctl") || strstr(saved,"shutdown_command") ||
           strstr(saved,"critical process") || strstr(saved,"reboot_on_failure") ||
           strstr(saved,"InitFatalReboot") || strstr(saved,"Reboot start, reason:") ||
           strstr(saved,"a9ml-deadman: firing") || strstr(saved,"a9ml: shutdown"));
    if (sync_error) {errno=EIO;return -1;}
    return 0;
}

int main(void)
{
    const char *const critical[] = {
        "6,12,50000,-;A9_MSM_TRACE phase=gpu.request_irq.begin rc=0\n",
        "6,13,50001,-;a9ml-eink: MSM_INSMOD begin\n",
        "6,14,50002,-;a9ml-eink: MSM_INSMOD end rc=-1\n",
        "0,15,300000,-;A9_BOOT_DEADLINE EXPIRED seconds=300\n",
        "6,16,300001,-;A9_VENUS_ENC op=buffer.begin type=0x1 alloc=1\n",
        "3,17,300002,-;A9_VENUS_RECOVERY event=1 stage=0 rc=0 irq=0x10\n",
        "6,17,300002,-;A9_HANG_DIAG knob=hung_task_panic actual=1\n",
        "6,18,300003,-;A9_RPMB_SCM check=smc_return secure_status=-2 mapped_rc=-22\n",
        "6,19,300004,-;A9_RPMB_REGISTER phase=4 rc=-22\n",
        "3,18,300003,-;arm-smmu: Unhandled context fault: fsr=0x1\n",
    };
    FILE *file=tmpfile(); assert(file);
    int fd=fileno(file);
    for (unsigned i=0;i<sizeof(critical)/sizeof(critical[0]);++i) {
        assert(ftruncate(fd,0)==0); assert(lseek(fd,0,SEEK_SET)==0);
        unsigned before=sync_calls;
        assert(save_record(fd,critical[i],strlen(critical[i]),true)==0);
        assert(sync_calls==before+1);
    }
    unsigned before=sync_calls;
    const char *normal="6,16,50003,-;ordinary probe\n";
    assert(save_record(fd,normal,strlen(normal),true)==0);
    assert(save_record(fd,critical[0],strlen(critical[0]),false)==0);
    assert(!critical_kmsg("A9_MSM_TRACE phase=",5)); /* bounded record */
    assert(sync_calls==before);
    const char *codec="A9_ENC_TRACE op=output_pool phase=begin\n";
    assert(save_record(fd,codec,strlen(codec),false)==0);
    assert(sync_calls==before+1); /* userspace boundary is durable too */
    const char *probe[] = {
        "A9Encode: ENCODE_IO codec=1 op=first_input.begin inputs=0 outputs=0 status=0\n",
        "A9Encode: ENCODE_STAGE codec=1 stage=4\n",
        "A9Encode: ENCODE codec=1 stage=4 status=0 inputs=30 outputs=30 bytes=100 eos=1\n",
    };
    for (unsigned i=0;i<sizeof(probe)/sizeof(probe[0]);++i) {
        assert(ftruncate(fd,0)==0);assert(lseek(fd,0,SEEK_SET)==0);
        before=sync_calls;
        assert(save_record(fd,probe[i],strlen(probe[i]),false)==0);
        assert(sync_calls==before+1);
    }
    const char *hvenc[] = {"A9HVEnc: HVENC_QUAL op=6 phase=0 rc=0 frames=0\n",
        "A9HVEnc: HVENC_RESULT status=0 avc_frames=20\n"};
    for (unsigned i=0;i<2;++i) for (unsigned kernel=0;kernel<2;++kernel) {
        assert(ftruncate(fd,0)==0);assert(lseek(fd,0,SEEK_SET)==0);
        before=sync_calls;
        assert(save_record(fd,hvenc[i],strlen(hvenc[i]),kernel)==0);
        assert(sync_calls==before+1);
    }
    const char *reboots[] = {
        "init: Received sys.powerctl='reboot,bootloader' from pid: 42 (/system/bin/example)\n",
        "init: Got shutdown_command 'reboot,bootloader' Calling HandlePowerctlMessage()\n",
        "init: critical process 'example' exited 4 times in 4 minutes\n",
        "init: Service example has 'reboot_on_failure' option and failed, shutting down system.\n",
        "init: InitFatalReboot: signal 6\n",
        "init: Reboot start, reason: reboot,bootloader, reboot_target: bootloader\n",
        "a9ml-deadman: firing (hold-expired) at 6000s: reboot to bootloader\n",
        "a9ml: shutdown (reboot,bootloader)\n",
    };
    for (unsigned i=0;i<sizeof(reboots)/sizeof(reboots[0]);++i) {
        for (unsigned kernel=0;kernel<2;++kernel) {
            assert(ftruncate(fd,0)==0);assert(lseek(fd,0,SEEK_SET)==0);
            before=sync_calls;
            assert(save_record(fd,reboots[i],strlen(reboots[i]),kernel)==0);
            assert(sync_calls==before+1);
        }
    }
    before=sync_calls;
    const char *ordinary_reboot="JobInfo: scheduling a task after reboot; sys.powerctl unchanged\n";
    assert(save_record(fd,ordinary_reboot,strlen(ordinary_reboot),true)==0);
    assert(save_record(fd,ordinary_reboot,strlen(ordinary_reboot),false)==0);
    assert(!reboot_record(reboots[0],8)); /* bounded, not NUL-dependent */
    assert(sync_calls==before);
    sync_error=true;
    assert(save_record(fd,critical[0],strlen(critical[0]),true)==0);
    assert(sync_calls==before+1); /* future records survive a failed sync */
    assert(save_record(-1,critical[0],strlen(critical[0]),true)==-1);
    assert(sync_calls==before+1); /* no sync after a failed write */
    /* Early records survive normal log rotation in their own durable file.
     * Exercise both sides of the circular buffer, plus an oversized append. */
    sync_error=false;ring_test=true;
    assert(ftruncate(fd,0)==0); assert(lseek(fd,0,SEEK_SET)==0);
    early_pos=early_len=0;
    early_append("early\n",6);
    assert(early_save(fd)==0);
    char prefix[16]={0};assert(pread(fd,prefix,6,0)==6);
    assert(!memcmp(prefix,"early\n",6));
    char *large=malloc(KMSG_BUDGET+17);assert(large);
    memset(large,'a',KMSG_BUDGET+17);
    early_append(large,KMSG_BUDGET+17);
    early_append("tail\n",5);
    assert(early_len==KMSG_BUDGET);
    assert(ftruncate(fd,0)==0); assert(lseek(fd,0,SEEK_SET)==0);
    assert(early_save(fd)==0);
    struct stat st;assert(fstat(fd,&st)==0 && st.st_size==KMSG_BUDGET);
    assert(pread(fd,prefix,5,KMSG_BUDGET-5)==5 && !memcmp(prefix,"tail\n",5));
    assert(pread(fd,prefix,1,0)==1 && prefix[0]=='a');
    free(large);fclose(file);
    puts("test-logger: PASS (write-before-sync, encoder/fault scope, errors, early ring wrap)");
    return 0;
}
