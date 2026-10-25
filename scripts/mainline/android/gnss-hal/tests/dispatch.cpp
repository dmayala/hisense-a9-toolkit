// SPDX-License-Identifier: Apache-2.0
// Direct call of production dispatcher with real Android Binder parcels.
// No Binder driver, registration, QRTR or helper entry point is executed.
#include <sys/types.h>
#include <android/binder_ibinder.h>
#include <android/binder_parcel.h>
#include <cassert>
static uid_t caller=1000;
static uid_t testUid() { return caller; }
#define AIBinder_getCallingUid testUid
#define main gnss_service_unused_main
#include "../service.cpp"
#undef main
#undef AIBinder_getCallingUid
#include <cassert>
#include <cstring>
#include <cstdio>
int main() {
    setvbuf(stdout,nullptr,_IONBF,0);
    unsigned n=0;
    for(unsigned code: {2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,21,22,23,24,25,26,27,16777214,16777215}) {
        AParcel *in=AParcel_create(),*out=AParcel_create();
        assert(dispatch(nullptr,code,in,out)==STATUS_OK);
        assert(AParcel_setDataPosition(out,0)==0);
        AStatus* status=nullptr; assert(AParcel_readStatusHeader(out,&status)==0);
        int ex=AStatus_getExceptionCode(status);
        const bool extension=(code>=3 && code<=13) || code==21 || code==22 || code==27;
        if(extension || (code>=17 && code<=19) || code==25) assert(ex==EX_UNSUPPORTED_OPERATION);
        else if(code==16) assert(ex==EX_ILLEGAL_ARGUMENT); // truncated time
        else if(code==14) assert(ex==EX_ILLEGAL_STATE); // no callback: never opens transport
        else assert(ex==0);
        // A successful null extension makes Android 16 JNI construct a
        // wrapper whose constructor asserts or whose methods dereference null.
        // Unsupported getters carry only the exception, no Binder payload.
        if(extension) assert(AParcel_getDataPosition(out)==AParcel_getDataSize(out));
        AStatus_delete(status); AParcel_delete(in); AParcel_delete(out); ++n;
    }
    for(uid_t uid: {0,2000,10001}) {
        caller=uid;
        AParcel *in=AParcel_create(),*out=AParcel_create();
        assert(dispatch(nullptr,14,in,out)==STATUS_PERMISSION_DENIED);
        AParcel_delete(in); AParcel_delete(out); ++n;
    }
    caller=1000;
    AParcel *in=AParcel_create(),*out=AParcel_create();
    assert(dispatch(nullptr,900,in,out)==STATUS_UNKNOWN_TRANSACTION);
    assert(dispatch(nullptr,20,in,out)==0); // truncated mode -> argument exception
    assert(AParcel_setDataPosition(out,0)==0);
    AStatus* status=nullptr; assert(AParcel_readStatusHeader(out,&status)==0);
    assert(AStatus_getExceptionCode(status)==EX_ILLEGAL_ARGUMENT);
    AStatus_delete(status); AParcel_delete(in); AParcel_delete(out); n+=2;
    // Production AIDL injectTime decoder/cache, while idle: never starts LOC.
    for(int variant=0;variant<7;++variant) {
        in=AParcel_create(); out=AParcel_create();
        int64_t now=a9_boot_ms();
        int64_t utc=1800000000000LL,reference=now; int32_t uncertainty=20;
        if(variant==1) reference=now+60000;
        if(variant==2) reference=-1;
        if(variant==3) utc=INT64_MAX;
        if(variant==4) uncertainty=-1;
        if(variant==5) uncertainty=86400001;
        if(variant==6) { utc=1; reference=0; }
        assert(!AParcel_writeInt64(in,utc) && !AParcel_writeInt64(in,reference) && !AParcel_writeInt32(in,uncertainty));
        assert(!AParcel_setDataPosition(in,0) && dispatch(nullptr,16,in,out)==0);
        assert(!AParcel_setDataPosition(out,0)); status=nullptr;
        assert(!AParcel_readStatusHeader(out,&status));
        assert(AStatus_getExceptionCode(status)==(variant?EX_ILLEGAL_ARGUMENT:0));
        AStatus_delete(status); AParcel_delete(in); AParcel_delete(out); ++n;
    }
    printf("DISPATCH PASS: %u refusal/extension/lifecycle/access cases; no transport\n",n);
}
