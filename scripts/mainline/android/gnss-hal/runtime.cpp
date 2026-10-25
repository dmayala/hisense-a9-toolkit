// SPDX-License-Identifier: Apache-2.0
#include "runtime.h"
#include <cmath>
#include <sstream>
#include <cstdio>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/vfs.h>
#include <time.h>
#include <unistd.h>

namespace a9gnss {
int64_t monotonicMs() {
    timespec t{};
    if (clock_gettime(CLOCK_MONOTONIC, &t)) return -1;
    return t.tv_sec * INT64_C(1000) + t.tv_nsec / 1000000;
}
bool parseFix(const std::string& text, Fix& f) {
    long long relative=0; unsigned long long utc=0; unsigned used=0,tech=0; int n=0;
    if (text.empty() || text.size()>255 || text.find('\0')!=std::string::npos ||
        sscanf(text.c_str(), "A9_GNSS_G1_FIX t_ms=%lld lat=%lf lon=%lf hunc_m=%lf sv_used=%u tech=0x%x utc_ms=%llu%n",
               &relative,&f.latitude,&f.longitude,&f.accuracy,&used,&tech,&utc,&n)!=7 || text.substr(n)!="\n") return false;
    if (relative<0 || relative>2100000 || !std::isfinite(f.latitude) || !std::isfinite(f.longitude) ||
        std::abs(f.latitude)>90 || std::abs(f.longitude)>180 || (f.latitude==0 && f.longitude==0) ||
        !std::isfinite(f.accuracy) || f.accuracy<=0 || f.accuracy>=100000 || !(tech&1) || (tech&0x36) || utc>INT64_MAX) return false;
    f.utc=static_cast<int64_t>(utc); return true;
}
bool parseSvs(const std::string& text, std::vector<Sv>& out) {
    std::istringstream in(text); std::string tag; unsigned count=0;
    if (!(in>>tag>>count) || tag!="A9_SV" || count>176) return false;
    out.clear();
    for(unsigned i=0;i<count;++i) {
        unsigned valid,system,id,info; float cn0,elev,azim;
        if(!(in>>valid>>system>>id>>info>>cn0>>elev>>azim)) return false;
        if((valid&3)!=3 || id==0 || !std::isfinite(cn0) || !std::isfinite(elev) || !std::isfinite(azim)) continue;
        int c=0;
        switch(system) {
        case 1: c=1; break;
        case 2: c=6; if(id>=301 && id<=336) id-=300; break;
        case 3: c=2; if(id>=33 && id<=64) id+=87; break;
        case 4: case 6: c=5; if(id>=201 && id<=263) id-=200; break;
        case 5: c=3; if(id>=65 && id<=96) id-=64; break;
        case 7: c=4; break;
        default: continue;
        }
        out.push_back({static_cast<int32_t>(id),c,
            (valid&128) && cn0>0 && cn0<70?cn0:0,
            (valid&32) && elev>=-90 && elev<=90?elev:0,
            (valid&64) && azim>=0 && azim<360?azim:0,
            static_cast<int32_t>((valid&16)?info&3:0)});
    }
    in>>std::ws; return in.eof();
}
bool parseUsed(const std::string& text, std::vector<Sv>& out) {
    std::istringstream in(text); std::string tag; unsigned count;
    if(!(in>>tag>>count) || tag!="A9_USED" || count>176) return false;
    out.clear();
    for(unsigned i=0;i<count;++i) {
        unsigned id; int c=0;
        if(!(in>>id) || id>65535) return false;
        if(id>=1 && id<=32) c=1;
        else if(id>=33 && id<=64) { c=2; id+=87; }
        else if(id>=65 && id<=96) { c=3; id-=64; }
        else if(id>=193 && id<=200) c=4;
        else if(id>=201 && id<=263) { c=5; id-=200; }
        else if(id>=301 && id<=336) { c=6; id-=300; }
        if(c) out.push_back({static_cast<int32_t>(id),c,0,0,0,4});
    }
    in>>std::ws; return in.eof();
}

}
