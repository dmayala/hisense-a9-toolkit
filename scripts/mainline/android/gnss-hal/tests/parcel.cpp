// SPDX-License-Identifier: Apache-2.0
// Offline interoperability oracle: use the cached AOSP GNSS V7 serializers,
// not a duplicate handwritten parcel decoder. No binder driver registration.
#include "../wire.h"
#include <android/binder_ibinder.h>
#include <android/binder_parcel_platform.h>
#include <cassert>
#include <cstdio>
#include <string>

namespace aidl::android::hardware::gnss {
struct ElapsedRealtime { int32_t flags; int64_t timestampNs; double uncertaintyNs; };
struct GnssLocation {
    int32_t flags;
    double latitudeDegrees,longitudeDegrees,altitudeMeters,speedMetersPerSec,bearingDegrees,
           horizontalAccuracyMeters,verticalAccuracyMeters,speedAccuracyMetersPerSecond,bearingAccuracyDegrees;
    int64_t timestampMillis;
    ElapsedRealtime elapsedRealtime;
    binder_status_t readFromParcel(const AParcel*);
};
struct IGnssCallback {
    struct GnssSvInfo {
        int32_t svid, constellation; float cn0,baseband,elevation,azimuth;
        int64_t carrier; int32_t flags;
        binder_status_t readFromParcel(const AParcel*);
    };
};
struct IGnss {
    static const std::string hash;
    struct PositionModeOptions {
        int32_t mode,recurrence,minIntervalMs,preferredAccuracyMeters,preferredTimeMs; bool lowPowerMode;
        binder_status_t writeToParcel(AParcel*) const;
    };
};
}
static_assert(sizeof(aidl::android::hardware::gnss::GnssLocation)==112);
static_assert(sizeof(aidl::android::hardware::gnss::IGnss::PositionModeOptions)==24);

void* create(void* p) { return p; }
void destroy(void*) {}
binder_status_t transact(AIBinder*,transaction_code_t,const AParcel*,AParcel*) { return STATUS_UNKNOWN_TRANSACTION; }
int main() {
    using namespace a9gnss;
    using aidl::android::hardware::gnss::GnssLocation;
    using aidl::android::hardware::gnss::IGnss;
    assert(IGnss::hash==hash);
    AParcel* p=AParcel_create(); assert(p);
    int32_t start=AParcel_getDataPosition(p);
    Fix f{(double)(17+2)/3, (double)(23+7)/4, 8.5, 1800000000000LL};
    assert(writeLocation(p,f,123456789)==0);
    assert(AParcel_setDataPosition(p,start)==0);
    int32_t present=0; assert(AParcel_readInt32(p,&present)==0 && present==1);
    GnssLocation decoded{}; assert(decoded.readFromParcel(p)==0);
    assert(decoded.flags==0x11 && decoded.latitudeDegrees==f.latitude && decoded.longitudeDegrees==f.longitude);
    assert(decoded.horizontalAccuracyMeters==f.accuracy && decoded.timestampMillis==f.utc);
    assert(decoded.elapsedRealtime.flags==1 && decoded.elapsedRealtime.timestampNs==123456789);
    AParcel_delete(p);
    p=AParcel_create();
    std::vector<Sv> svs{{5,1,31.4f,30,90,3}};
    assert(writeSvs(p,svs)==0 && AParcel_setDataPosition(p,0)==0);
    int32_t count=0; assert(AParcel_readInt32(p,&count)==0 && count==1);
    assert(AParcel_readInt32(p,&present)==0 && present==1);
    aidl::android::hardware::gnss::IGnssCallback::GnssSvInfo sv{};
    assert(sv.readFromParcel(p)==0);
    assert(sv.svid==5 && sv.constellation==1 && sv.cn0==31.4f && sv.elevation==30 && sv.azimuth==90 && sv.flags==3);
    AParcel_delete(p);
    unsigned cases=0;
    for(int mode: {0,1,2}) for(int recurrence: {0,1,2}) for(int interval: {0,999,1000,9000}) {
        p=AParcel_create(); assert(p);
        start=AParcel_getDataPosition(p);
        assert(AParcel_writeInt32(p,1)==0);
        IGnss::PositionModeOptions opts{mode,recurrence,interval,0,0,false};
        assert(opts.writeToParcel(p)==0);
        assert(AParcel_setDataPosition(p,start)==0);
        int32_t got=0; bool single=false;
        bool ok=readMode(p,got,single);
        assert(ok==(mode==0 && recurrence<=1));
        if(ok) assert(got==(interval<1000?1000:interval) && single==(recurrence==1));
        AParcel_delete(p); ++cases;
    }
    printf("PARCEL PASS: original V7 hash, original location decoder, %u original mode serializer cases\n",cases);
}
