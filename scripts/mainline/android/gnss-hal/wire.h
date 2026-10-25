// SPDX-License-Identifier: Apache-2.0
// Stable AIDL parcel encoding, checked against cached GNSS V7 NDK serializers.
// No C++ ABI dependency on the cached library; it is used only by offline tests.
#pragma once
#include <android/binder_parcel.h>
#include "runtime.h"
namespace a9gnss {
constexpr const char* descriptor="android.hardware.gnss.IGnss";
constexpr const char* callbackDescriptor="android.hardware.gnss.IGnssCallback";
constexpr const char* hash="da99c936e327ce4b93e04fdfa4c7770913667f21";
constexpr int version=7;
inline binder_status_t writeLocation(AParcel* p,const Fix& f,int64_t realtimeNs) {
    // Nonnullable structured parcelable marker, then sized GnssLocation.
    // flags: HAS_LAT_LONG | HAS_HORIZONTAL_ACCURACY, every other field absent.
    binder_status_t s;
#define WRITE(call) do { s=(call); if(s!=STATUS_OK) return s; } while(0)
    WRITE(AParcel_writeInt32(p,1));
    int32_t begin=AParcel_getDataPosition(p);
    WRITE(AParcel_writeInt32(p,0));
    WRITE(AParcel_writeInt32(p,0x11));
    WRITE(AParcel_writeDouble(p,f.latitude));
    WRITE(AParcel_writeDouble(p,f.longitude));
    WRITE(AParcel_writeDouble(p,0)); // altitude
    WRITE(AParcel_writeDouble(p,0)); // speed
    WRITE(AParcel_writeDouble(p,0)); // bearing
    WRITE(AParcel_writeDouble(p,f.accuracy));
    WRITE(AParcel_writeDouble(p,0)); // vertical accuracy
    WRITE(AParcel_writeDouble(p,0)); // speed accuracy
    WRITE(AParcel_writeDouble(p,0)); // bearing accuracy
    WRITE(AParcel_writeInt64(p,f.utc));
    WRITE(AParcel_writeInt32(p,1)); // nonnullable ElapsedRealtime
    int32_t elapsed=AParcel_getDataPosition(p);
    WRITE(AParcel_writeInt32(p,24));
    WRITE(AParcel_writeInt32(p,1)); // HAS_TIMESTAMP_NS
    WRITE(AParcel_writeInt64(p,realtimeNs));
    WRITE(AParcel_writeDouble(p,0));
    int32_t end=AParcel_getDataPosition(p);
    if(end-elapsed!=24) return STATUS_BAD_VALUE;
    WRITE(AParcel_setDataPosition(p,begin));
    WRITE(AParcel_writeInt32(p,end-begin));
    WRITE(AParcel_setDataPosition(p,end));
    return STATUS_OK;
#undef WRITE
}
inline binder_status_t writeSvs(AParcel* p,const std::vector<Sv>& svs) {
    binder_status_t s=AParcel_writeInt32(p,static_cast<int32_t>(svs.size()));
    if(s) return s;
    for(const auto& v:svs) {
#define W(call) do { s=(call); if(s) return s; } while(0)
        W(AParcel_writeInt32(p,1)); // nonnull array element
        W(AParcel_writeInt32(p,40)); // sized GnssSvInfo
        W(AParcel_writeInt32(p,v.id));
        W(AParcel_writeInt32(p,v.constellation));
        W(AParcel_writeFloat(p,v.cn0));
        W(AParcel_writeFloat(p,0)); // baseband not supplied by LOC
        W(AParcel_writeFloat(p,v.elevation));
        W(AParcel_writeFloat(p,v.azimuth));
        W(AParcel_writeInt64(p,0)); // no carrier frequency claim
        W(AParcel_writeInt32(p,v.flags));
#undef W
    }
    return STATUS_OK;
}
inline bool readMode(const AParcel* p,int32_t& interval,bool& single) {
    int32_t present=0,size=0,mode=0,recurrence=0,accuracy=0,time=0;
    bool low=false;
    if(AParcel_readInt32(p,&present) || present!=1) return false;
    int32_t begin=AParcel_getDataPosition(p);
    if(AParcel_readInt32(p,&size) || size<28 || size>128 || begin<0 || begin>INT32_MAX-size ||
       size>AParcel_getDataSize(p)-begin ||
       AParcel_readInt32(p,&mode) || AParcel_readInt32(p,&recurrence) ||
       AParcel_readInt32(p,&interval) || AParcel_readInt32(p,&accuracy) ||
       AParcel_readInt32(p,&time) || AParcel_readBool(p,&low) ||
       AParcel_setDataPosition(p,begin+size)) return false;
    // Framework capabilities omit MSB/MSA. Modem always stays standalone and
    // periodic at the proven 1000 ms; only delivery is throttled/one-shot.
    if(mode!=0 || recurrence<0 || recurrence>1 || interval<0 || accuracy<0 || time<0) return false;
    interval=interval<1000?1000:interval; single=recurrence==1;
    return true;
}
}
