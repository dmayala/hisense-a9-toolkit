// SPDX-License-Identifier: Apache-2.0
// Minimal standalone GNSS HAL. Stable AIDL over libbinder_ndk; no radio power,
// store access, location logs, NMEA callbacks or network access. UTC time only.
#include "wire.h"
#include "time-inject.h"
#include <android/binder_ibinder_platform.h>
#include <android/binder_manager.h>
#include <android/binder_process.h>
#include <android/binder_status.h>
#include <android/binder_stability.h>
#include <atomic>
#include <cerrno>
#include <fcntl.h>
#include <mutex>
#include <signal.h>
#include <spawn.h>
#include <sys/prctl.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <thread>
#include <time.h>
#include <unistd.h>

using namespace a9gnss;
namespace {
binder_status_t reply(AParcel* p,int exception=0) {
    AStatus* s=exception?AStatus_fromExceptionCode(exception):AStatus_newOk();
    binder_status_t r=AParcel_writeStatusHeader(p,s); AStatus_delete(s); return r;
}
void* created(void* p) { return p; }
void destroyed(void*) {}
binder_status_t noTransactions(AIBinder*,transaction_code_t,const AParcel*,AParcel*) { return STATUS_UNKNOWN_TRANSACTION; }
// The remote callback's class supplies its descriptor to prepareTransaction.
AIBinder_Class* cbClass=AIBinder_Class_define(callbackDescriptor,created,destroyed,noTransactions);

class Service {
    std::mutex mutex_;
    AIBinder* callback_=nullptr;
    AIBinder_DeathRecipient* death_;
    std::atomic<bool> stop_{false},running_{false};
    std::mutex lifecycle_;
    std::thread worker_;
    std::atomic<bool> svEnabled_{false};
    std::atomic<unsigned> svReports_{0},svMax_{0};
    int timeWrite_=-1;
    a9_time_sample timeSample_{}; bool haveTime_=false;
    std::atomic<unsigned> timeRequests_{0},timeCallbacks_{0},timeQueued_{0},timeAck_{0},timeRejected_{0},timeResults_{0};
    std::atomic<int> timeResult_{-1},timeError_{0},engineState_{0},sessionState_{0};
    std::atomic<unsigned> svRawMax_{0},svWithCn0Max_{0},svUsedMax_{0};
    std::atomic<float> cn0Max_{0};
    int64_t lastTimeCallback_=-10000;
    int32_t interval_=1000;
    bool single_=false;
    std::atomic<unsigned> delivered_{0};
    std::atomic<bool> stopAcknowledged_{false};
    std::atomic<int> clientExit_{-1},clientStage_{0},qmiError_{0};
    static void died(void* p) { static_cast<Service*>(p)->stop_=true; }
    bool call(transaction_code_t code,const Fix* fix=nullptr,int32_t value=0,const std::vector<Sv>* svs=nullptr) {
        AIBinder* cb;
        { std::lock_guard<std::mutex> l(mutex_); cb=callback_; if(cb) AIBinder_incStrong(cb); }
        if(!cb) return false;
        AParcel *in=nullptr,*out=nullptr;
        binder_status_t s=AIBinder_prepareTransaction(cb,&in);
        if(!s && svs) s=writeSvs(in,*svs);
        else if(!s && fix) {
            timespec ts{},utc{}; clock_gettime(CLOCK_BOOTTIME,&ts);
            Fix f=*fix;
            if(!f.utc && !clock_gettime(CLOCK_REALTIME,&utc)) f.utc=utc.tv_sec*INT64_C(1000)+utc.tv_nsec/1000000;
            s=writeLocation(in,f,ts.tv_sec*INT64_C(1000000000)+ts.tv_nsec);
        } else if(!s && code!=9) s=AParcel_writeInt32(in,value);
        if(!s) s=AIBinder_transact(cb,code,&in,&out,0);
        AStatus* status=nullptr;
        if(!s) s=AParcel_readStatusHeader(out,&status);
        bool ok=!s && status && AStatus_isOk(status);
        AStatus_delete(status); AParcel_delete(in); AParcel_delete(out); AIBinder_decStrong(cb);
        if(!ok) stop_=true;
        return ok;
    }
    void requestTime() {
        int64_t now=monotonicMs();
        if(now>=0 && now-lastTimeCallback_>=10000) {
            lastTimeCallback_=now; ++timeCallbacks_;
            call(9); // gnssRequestTimeCb(): NO arguments; framework supplies NTP
        }
    }
    void collect(int readFd,int statusFd,pid_t child,int32_t interval,bool single) {
        requestTime(); // also request on START, even if modem never asks
        std::vector<Sv> used; int64_t usedAt=-1;
        std::string pending;
        int64_t next=0,stopAt=0,end=monotonicMs()+2100000;
        bool signalled=false,reaped=false;
        int result=0;
        for(;;) {
            // Reap only on this thread. A signalled PID is still our unreaped
            // child, never a potentially reused process number.
            if(!reaped) {
                pid_t r=waitpid(child,&result,WNOHANG);
                if(r==child || (r<0 && errno!=EINTR)) reaped=true;
            }
            int64_t now=monotonicMs();
            if(now<0 || now>=end-5000) stop_=true;
            if(!reaped && stop_ && !signalled) { kill(child,SIGUSR1); signalled=true; stopAt=now; }
            if(!reaped && signalled && (now<0 || now-stopAt>=7000)) {
                kill(child,SIGKILL); while(waitpid(child,&result,0)<0 && errno==EINTR) {}
                break; // Outer coordinator must still prove MPSS offline.
            }
            char buf[4096]; ssize_t n=read(readFd,buf,sizeof(buf));
            if(n>0) {
                pending.append(buf,static_cast<size_t>(n));
                size_t end;
                while((end=pending.find('\n'))!=std::string::npos) {
                    std::string line=pending.substr(0,end+1); pending.erase(0,end+1);
                    if(line.rfind("A9_USED ",0)==0) {
                        if(!parseUsed(line,used)) { stop_=true; break; }
                        usedAt=now; continue;
                    }
                    if(line.rfind("A9_SV ",0)!=0 && line.rfind("A9_GNSS_G1_FIX ",0)!=0) {
                        char tag[32]{}; unsigned value=0; int n=0;
                        if(sscanf(line.c_str(),"%31s %u%n",tag,&value,&n)!=2 || line.substr(n)!="\n") { stop_=true; break; }
                        std::string event(tag);
                        if(event=="A9_ENGINE" && (value==1 || value==2)) { engineState_=value; call(2,nullptr,value==1?3:4); }
                        else if(event=="A9_SESSION" && (value==1 || value==2)) { sessionState_=value; call(2,nullptr,value==1?1:2); }
                        else if(event=="A9_TIME_REQUEST" && value==0) { ++timeRequests_; requestTime(); }
                        else if(event=="A9_TIME_ACK" && value<=1) { if(value) ++timeRejected_; else ++timeAck_; }
                        else if(event=="A9_TIME_ERROR" && value<=65535) timeError_=value;
                        else if(event=="A9_TIME_RESULT" && value<=INT32_MAX) { ++timeResults_; timeResult_=value; }
                        else stop_=true;
                        continue;
                    }
                    if(line.rfind("A9_SV ",0)==0) {
                        std::vector<Sv> svs;
                        if(!parseSvs(line,svs)) { stop_=true; break; }
                        unsigned raw=0,signal=0,inFix=0;
                        sscanf(line.c_str(),"A9_SV %u",&raw);
                        if(raw>svRawMax_) svRawMax_=raw;
                        for(auto& sv:svs) {
                            if(usedAt>=0 && now-usedAt<=5000) for(const auto& u:used)
                                if(u.id==sv.id && u.constellation==sv.constellation) sv.flags|=4;
                            if(sv.cn0>0) ++signal;
                            if(sv.flags&4) ++inFix;
                            if(sv.cn0>cn0Max_) cn0Max_=sv.cn0;
                        }
                        if(signal>svWithCn0Max_) svWithCn0Max_=signal;
                        if(inFix>svUsedMax_) svUsedMax_=inFix;
                        ++svReports_; if(svs.size()>svMax_) svMax_=svs.size();
                        if(!stop_ && svEnabled_) call(3,nullptr,0,&svs);
                        continue;
                    }
                    Fix f{};
                    if(!parseFix(line,f)) { stop_=true; break; }
                    if(!stop_ && now>=next) {
                        if(call(4,&f)) { ++delivered_; next=now+interval; }
                        if(single) stop_=true;
                    }
                    // Keep no location cache; only this stack/parcel carries it.
                }
                if(pending.size()>32768) stop_=true;
            } else if(n<0 && errno!=EINTR) stop_=true;
            // Reap first, then drain the anonymous file through EOF. Otherwise
            // the final engine/time result could disappear with child exit.
            if(reaped && n==0) { if(!pending.empty()) stop_=true; break; }
            if(reaped && n<0 && errno!=EINTR) break;
            timespec delay{0,50000000}; nanosleep(&delay,nullptr);
        }
        stop_=true;
        clientExit_=WIFEXITED(result)?WEXITSTATUS(result):-1;
        // Read the bounded, private G1 transcript only to attest STOP. Never
        // forward it to Binder, logcat or dumpsys. It has no coordinates/SV
        // detail; positions use the separate private descriptor exclusively.
        std::string transcript; char chunk[4096]; ssize_t count;
        while((count=read(statusFd,chunk,sizeof(chunk)))>0 && transcript.size()<262144)
            transcript.append(chunk,static_cast<size_t>(count));
        // Fixed diagnostic categories only; never echo the transcript/payload.
        int stage=0;
        if(transcript.find("fault socket")!=std::string::npos) stage=1;
        else if(transcript.find("fault lookup")!=std::string::npos) stage=2;
        else if(transcript.find("fault confine")!=std::string::npos) stage=3;
        else if(transcript.find("response start result=success")!=std::string::npos) stage=6;
        else if(transcript.find("response start result=failure")!=std::string::npos) stage=5;
        else if(transcript.find("confined uid=")!=std::string::npos) stage=4;
        clientStage_=stage;
        for(const char* name:{"register-master","reg-events","get-engine-lock","set-operation-mode","start"}) {
            std::string pattern=std::string("A9_GNSS_G1 response ")+name+" result=failure error=";
            size_t at=transcript.find(pattern);
            if(at!=std::string::npos) { unsigned value=0;
                if(sscanf(transcript.c_str()+at+pattern.size(),"%u",&value)==1 && value<=65535) qmiError_=value;
            }
        }
        stopAcknowledged_=transcript.find(" stop=acknowledged\n")!=std::string::npos;
        close(statusFd); close(readFd);
        { std::lock_guard<std::mutex> l(mutex_);
          if(timeWrite_>=0) { close(timeWrite_); timeWrite_=-1; } }
        // Binder session ended; engineState_ retains the last actual modem
        // indication. Child exit does not prove the physical engine powered off.
        call(2,nullptr,2);
        running_=false;
    }
public:
    Service():death_(AIBinder_DeathRecipient_new(died)) {}
    int setCallback(AIBinder* cb) {
        if(!cb || !AIBinder_associateClass(cb,cbClass)) { if(cb) AIBinder_decStrong(cb); return EX_ILLEGAL_ARGUMENT; }
        { std::lock_guard<std::mutex> l(mutex_);
          if(running_) { AIBinder_decStrong(cb); return EX_ILLEGAL_STATE; }
          if(callback_) { AIBinder_unlinkToDeath(callback_,death_,this); AIBinder_decStrong(callback_); }
          callback_=cb;
          if(AIBinder_linkToDeath(cb,death_,this)!=STATUS_OK) { callback_=nullptr; AIBinder_decStrong(cb); return EX_ILLEGAL_STATE; }
        }
        // SCHEDULING | ON_DEMAND_TIME (Android interprets the latter as periodic).
        return call(1,nullptr,0x11)?0:EX_ILLEGAL_STATE;
    }
    int injectTime(const AParcel* p) {
        a9_time_sample sample{}; int32_t uncertainty;
        if(AParcel_readInt64(p,&sample.utc_ms) || AParcel_readInt64(p,&sample.ref_ms) ||
           AParcel_readInt32(p,&uncertainty) || uncertainty<0) return EX_ILLEGAL_ARGUMENT;
        sample.magic=A9_TIME_MAGIC; sample.uncertainty_ms=static_cast<uint32_t>(uncertainty);
        uint64_t utc; uint32_t unc;
        if(!a9_time_current(&sample,a9_boot_ms(),&utc,&unc)) return EX_ILLEGAL_ARGUMENT;
        std::lock_guard<std::mutex> l(mutex_);
        if(timeWrite_>=0 && write(timeWrite_,&sample,sizeof(sample))!=sizeof(sample)) return EX_ILLEGAL_STATE;
        timeSample_=sample; haveTime_=true; ++timeQueued_;
        return 0;
    }
    int mode(const AParcel* p) {
        int32_t interval; bool single;
        if(!readMode(p,interval,single)) return EX_ILLEGAL_ARGUMENT;
        std::lock_guard<std::mutex> l(mutex_);
        if(running_) return EX_ILLEGAL_STATE;
        interval_=interval; single_=single; return 0;
    }
    int start() {
        std::lock_guard<std::mutex> life(lifecycle_);
        if(running_) return 0;
        if(worker_.joinable()) worker_.join();
        std::lock_guard<std::mutex> l(mutex_);
        if(!callback_) return EX_ILLEGAL_STATE;
        int fix=memfd_create("gnss-events",MFD_CLOEXEC),status=memfd_create("gnss-status",MFD_CLOEXEC);
        if(fix<0 || status<0) { if(fix>=0) close(fix); if(status>=0) close(status); return EX_ILLEGAL_STATE; }
        fchmod(fix,0600); fchmod(status,0600);
        // Independent open descriptions: reader offsets must not follow writes.
        std::string path="/proc/self/fd/"+std::to_string(fix);
        int rd=open(path.c_str(),O_RDONLY|O_CLOEXEC);
        int writer=open(path.c_str(),O_WRONLY|O_APPEND|O_CLOEXEC);
        path="/proc/self/fd/"+std::to_string(status);
        int statusRd=open(path.c_str(),O_RDONLY|O_CLOEXEC);
        int null=open("/dev/null",O_RDWR|O_CLOEXEC);
        close(fix);
        // Lift dup2 sources to avoid collisions with child descriptors 0..4.
        int f=writer<0?-1:fcntl(writer,F_DUPFD_CLOEXEC,10);
        int n=null<0?-1:fcntl(null,F_DUPFD_CLOEXEC,10);
        int st=fcntl(status,F_DUPFD_CLOEXEC,10);
        if(writer>=0) close(writer); if(null>=0) close(null); close(status);
        if(rd<0 || statusRd<0 || f<0 || n<0 || st<0) {
            for(int fd:{rd,statusRd,f,n,st}) if(fd>=0) close(fd);
            return EX_ILLEGAL_STATE;
        }
        int command[2];
        if(pipe2(command,O_NONBLOCK|O_CLOEXEC)) {
            for(int fd:{rd,statusRd,f,n,st}) close(fd);
            return EX_ILLEGAL_STATE;
        }
        if(fchmod(command[0],0600)) {
            for(int fd:{command[0],command[1],rd,statusRd,f,n,st}) close(fd);
            return EX_ILLEGAL_STATE;
        }
        int cr=fcntl(command[0],F_DUPFD_CLOEXEC,10);
        close(command[0]);
        if(cr<0) { for(int fd:{command[1],rd,statusRd,f,n,st}) close(fd); return EX_ILLEGAL_STATE; }
        timeWrite_=command[1];
        // Cache permits framework NTP arriving while the provider is idle.
        uint64_t utc; uint32_t unc;
        if(haveTime_ && a9_time_current(&timeSample_,a9_boot_ms(),&utc,&unc))
            if(write(timeWrite_,&timeSample_,sizeof(timeSample_))!=sizeof(timeSample_)) {
                for(int fd:{cr,timeWrite_,rd,statusRd,f,n,st}) close(fd); timeWrite_=-1; return EX_ILLEGAL_STATE;
            }
        const char* args[]={"/vendor/bin/a9-gnss-loc","--discover","--seconds","2100","--fix-fd","4","--time-fd","5",nullptr};
        char* env[]={nullptr};
        posix_spawn_file_actions_t actions;
        posix_spawn_file_actions_init(&actions);
        for(int fd=0;fd<3;++fd) posix_spawn_file_actions_adddup2(&actions,n,fd);
        posix_spawn_file_actions_adddup2(&actions,st,1);
        posix_spawn_file_actions_adddup2(&actions,f,4);
        posix_spawn_file_actions_adddup2(&actions,cr,5);
        pid_t child=-1; stop_=false; running_=true; stopAcknowledged_=false; clientStage_=0; qmiError_=0;
        engineState_=0; sessionState_=0; timeResult_=-1; timeError_=0; lastTimeCallback_=-10000;
        int rc=posix_spawn(&child,args[0],&actions,nullptr,const_cast<char* const*>(args),env);
        posix_spawn_file_actions_destroy(&actions); close(f); close(n); close(st); close(cr);
        if(rc) { running_=false; close(rd); close(statusRd); close(timeWrite_); timeWrite_=-1; return EX_ILLEGAL_STATE; }
        worker_=std::thread(&Service::collect,this,rd,statusRd,child,interval_,single_);
        return 0;
    }
    int stop(bool clear=false) {
        std::lock_guard<std::mutex> life(lifecycle_);
        stop_=true;
        if(worker_.joinable()) worker_.join();
        if(clear) {
            std::lock_guard<std::mutex> l(mutex_);
            if(callback_) { AIBinder_unlinkToDeath(callback_,death_,this); AIBinder_decStrong(callback_); callback_=nullptr; }
            haveTime_=false; timeSample_={};
        }
        return 0;
    }
    int satelliteStatus(bool enabled) { svEnabled_=enabled; return 0; }
    binder_status_t dump(int fd) {
        // Counts/state only; never cache, format or dump a position or SV.
        dprintf(fd,"A9 standalone GNSS: running=%d fixes_delivered=%u client_exit=%d stop_ack=%d sv_reports=%u sv_max=%u client_stage=%d qmi_error=%d\n",
                running_.load(),delivered_.load(),clientExit_.load(),stopAcknowledged_.load(),svReports_.load(),svMax_.load(),clientStage_.load(),qmiError_.load());
        dprintf(fd,"UTC injection: modem_time_requests=%u framework_time_callbacks=%u time_queued=%u time_ack=%u time_rejected=%u time_results=%u last_time_result=%d last_time_error=%d engine_state=%d session_state=%d sv_raw_max=%u sv_with_cn0_max=%u cn0_max=%.2f sv_used_max=%u\n",
                timeRequests_.load(),timeCallbacks_.load(),timeQueued_.load(),timeAck_.load(),timeRejected_.load(),timeResults_.load(),timeResult_.load(),timeError_.load(),engineState_.load(),sessionState_.load(),svRawMax_.load(),svWithCn0Max_.load(),static_cast<double>(cn0Max_.load()),svUsedMax_.load());
        return STATUS_OK;
    }
};
Service service;
binder_status_t dispatch(AIBinder*,transaction_code_t code,const AParcel* in,AParcel* out) {
    // Android system_server is the only data-plane client.
    if(AIBinder_getCallingUid()!=1000) return STATUS_PERMISSION_DENIED;
    switch(code) {
    case 1: { AIBinder* cb=nullptr; binder_status_t r=AParcel_readStrongBinder(in,&cb); return r?r:reply(out,service.setCallback(cb)); }
    case 2: return reply(out,service.stop(true));
    // Android 16 JNI constructs extension wrappers on an OK status, even for
    // a null Binder (AGnssRil/AGnss then assert during init_once, before
    // setCallback). Unsupported extensions must fail the getter so JNI leaves
    // the wrapper absent. No assistance or other extension is implemented.
    case 3: case 4: case 5: case 6: case 7: case 8: case 9: case 10: case 11: case 12: case 13: case 21: case 22: case 27: {
        return reply(out,EX_UNSUPPORTED_OPERATION);
    }
    case 14: return reply(out,service.start());
    case 15: return reply(out,service.stop());
    case 16: return reply(out,service.injectTime(in));
    // Position injection and assistance deletion remain outside the POC.
    case 17: case 18: case 19: return reply(out,EX_UNSUPPORTED_OPERATION);
    case 20: return reply(out,service.mode(in));
    case 23: return reply(out,service.satelliteStatus(true));
    case 24: return reply(out,service.satelliteStatus(false));
    case 25: return reply(out,EX_UNSUPPORTED_OPERATION);
    case 26: return reply(out);
    case 16777215: { binder_status_t r=reply(out); return r?r:AParcel_writeInt32(out,version); }
    case 16777214: { binder_status_t r=reply(out); return r?r:AParcel_writeString(out,hash,40); }
    default: return STATUS_UNKNOWN_TRANSACTION;
    }
}
}
int main() {
    umask(0077);
    signal(SIGPIPE,SIG_IGN); // a dead child pipe must return EPIPE, never kill Binder

    rlimit core{0,0}; if(setrlimit(RLIMIT_CORE,&core) || prctl(PR_SET_DUMPABLE,0)) return 1;
    ABinderProcess_setThreadPoolMaxThreadCount(4);
    AIBinder_Class* cls=AIBinder_Class_define(descriptor,created,destroyed,dispatch);
    AIBinder_Class_setOnDump(cls,[](AIBinder*,int fd,const char**,uint32_t){return service.dump(fd);});
    AIBinder* binder=AIBinder_new(cls,&service);
    AIBinder_markVintfStability(binder);
    if(AServiceManager_addService(binder,"android.hardware.gnss.IGnss/default")!=STATUS_OK) return 1;
    ABinderProcess_joinThreadPool(); return 1;
}
