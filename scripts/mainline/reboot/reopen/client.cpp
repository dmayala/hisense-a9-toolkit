// SPDX-License-Identifier: Apache-2.0
// Same-key GCM reopen through the already-running stock HIDL TEE provider.
// stdin only; no key files, generation, upgrade, deletion or property writes.
// Platform HIDL uses libc++'s __1 ABI; NDK __config_site forces __ndk1.
#include <__config>
#undef _LIBCPP_ABI_NAMESPACE
#define _LIBCPP_ABI_NAMESPACE __1
#include <android/hardware/keymaster/4.0/IKeymasterDevice.h>
#include <cstdio>
#include <cstring>
#include <vector>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <unistd.h>
extern "C" unsigned char *SHA512(const unsigned char *, size_t, unsigned char *);
namespace km = android::hardware::keymaster::V4_0;
using android::hardware::hidl_vec;
using Bytes = std::vector<uint8_t>;
static void wipe(void *p, size_t n) { volatile uint8_t *v=(volatile uint8_t *)p; while(n--) *v++=0; }
struct Input {
    Bytes key, app, nonce, cipher;
    ~Input() { for (auto p : {&key,&app,&nonce,&cipher}) wipe(p->data(),p->size()); }
};
static uint32_t word(const uint8_t *p) { return uint32_t(p[0])|(uint32_t(p[1])<<8)|(uint32_t(p[2])<<16)|(uint32_t(p[3])<<24); }
static bool decode(const Bytes &packet, Input &in) {
    if(packet.size()<16 || word(packet.data())!=0x31524246) return false;
    size_t k=word(packet.data()+4), s=word(packet.data()+8), e=word(packet.data()+12);
    if(k<9 || k>65536 || s!=16384 || e<29 || e>65536 || packet.size()!=16+k+s+e) return false;
    const uint8_t *key=packet.data()+16, *sec=key+k, *encrypted=sec+s;
    if(memcmp(key,"pKMblob\0",8)) return false; // Explicit hardware provider only.
    in.key.assign(key+8,key+k);
    Bytes material(128+s,0);
    constexpr char prefix[]="Android secdiscardable SHA512";
    memcpy(material.data(),prefix,sizeof(prefix)-1); memcpy(material.data()+128,sec,s);
    in.app.resize(64); SHA512(material.data(),material.size(),in.app.data());
    wipe(material.data(),material.size());
    in.nonce.assign(encrypted,encrypted+12); in.cipher.assign(encrypted+12,encrypted+e);
    return true;
}
struct Result { int tee=0, chars=-1000, begin=-1000, finish=-1000, exported=-1000; size_t bytes=0; };
#ifndef REOPEN_FAKE
static hidl_vec<uint8_t> hv(const Bytes &b) { hidl_vec<uint8_t> v; v.setToExternal(const_cast<uint8_t *>(b.data()),b.size()); return v; }
static Result reopen(const Input &in) {
    Result r;
    auto dev=km::IKeymasterDevice::tryGetService();
    if(!dev) return r;
    auto info=dev->getHardwareInfo([&](km::SecurityLevel level, const auto &, const auto &) {
        r.tee=level==km::SecurityLevel::TRUSTED_ENVIRONMENT;
    });
    if(!info.isOk() || !r.tee) return r;
    auto chars=dev->getKeyCharacteristics(hv(in.key),hv(in.app),{},[&](km::ErrorCode rc,const auto &) { r.chars=int(rc); });
    if(!chars.isOk()) return r;
    hidl_vec<km::KeyParameter> p; p.resize(5);
    p[0].tag=km::Tag::BLOCK_MODE; p[0].f.blockMode=km::BlockMode::GCM;
    p[1].tag=km::Tag::PADDING; p[1].f.paddingMode=km::PaddingMode::NONE;
    p[2].tag=km::Tag::MAC_LENGTH; p[2].f.integer=128;
    p[3].tag=km::Tag::APPLICATION_ID; p[3].blob=hv(in.app);
    p[4].tag=km::Tag::NONCE; p[4].blob=hv(in.nonce);
    uint64_t handle=0;
    auto begin=dev->begin(km::KeyPurpose::DECRYPT,hv(in.key),p,{},[&](km::ErrorCode rc,const auto &,uint64_t h) { r.begin=int(rc); handle=h; });
    if(!begin.isOk() || r.begin) return r;
    Bytes recovered;
    auto finish=dev->finish(handle,{},hv(in.cipher),{},{},{},[&](km::ErrorCode rc,const auto &,const hidl_vec<uint8_t> &out) {
        r.finish=int(rc);
        // This is a persistent storage-key blob, not the 68-byte ephemeral
        // hardware-wrapped key. Preserve it only until the TEE exports it.
        if(!r.finish) recovered.assign(out.begin(),out.end());
    });
    if(!finish.isOk()) { r.finish=-1000; auto aborted=dev->abort(handle); (void)aborted.isOk(); }
    if(finish.isOk() && !r.finish && recovered.size()>8 && !memcmp(recovered.data(),"pKMblob\0",8)) {
        Bytes storage(recovered.begin()+8,recovered.end());
        auto exported=dev->exportKey(km::KeyFormat::RAW,hv(storage),{},{},[&](km::ErrorCode rc,const hidl_vec<uint8_t> &out) {
            r.exported=int(rc); r.bytes=out.size();
        });
        if(!exported.isOk()) r.exported=-1000;
        wipe(storage.data(),storage.size());
    }
    wipe(recovered.data(),recovered.size());
    return r;
}
#else
#include <cstdlib>
static Result reopen(const Input &in) {
    // Separate synthetic binary; never shipped to the phone.
    const char *expected=getenv("REOPEN_APP_HASH");
    if(!expected || strlen(expected)!=128) return {};
    const char hex[]="0123456789abcdef";
    for(size_t i=0;i<in.app.size();++i)
        if(expected[2*i]!=hex[in.app[i]>>4] || expected[2*i+1]!=hex[in.app[i]&15]) return {};
    Result r; r.tee=1; r.chars=0; r.begin=0; r.finish=0; r.exported=0; r.bytes=68;
    const char *mode=getenv("REOPEN_CASE");
    if(mode && !strcmp(mode,"reject")) { r.chars=-33;r.begin=-8;r.finish=-1000;r.exported=-1000;r.bytes=0; }
    if(mode && !strcmp(mode,"badmac")) { r.finish=-30;r.exported=-1000;r.bytes=0; }
    if(mode && !strcmp(mode,"plain")) r.bytes=32;
    return r;
}
#endif
int main(int argc,char **argv) {
    struct rlimit core={0,0}; setrlimit(RLIMIT_CORE,&core); prctl(PR_SET_DUMPABLE,0);
    if(argc==2 && !strcmp(argv[1],"--self-test")) {
        Input in; Bytes bad(16,0); if(decode(bad,in)) return 3;
        puts("PASS client_self_test=1"); return 0;
    }
    if(argc!=1) return 2;
    Bytes packet; packet.reserve(147472); uint8_t block[4096]; ssize_t n;
    while((n=read(STDIN_FILENO,block,sizeof(block)))>0) {
        if(packet.size()+size_t(n)>147472) { wipe(packet.data(),packet.size()); puts("FAIL reopen_input=0"); return 2; }
        packet.insert(packet.end(),block,block+n); wipe(block,sizeof(block));
    }
    Input in; bool valid=n==0 && decode(packet,in); wipe(packet.data(),packet.size());
    if(!valid) { puts("FAIL reopen_input=0"); return 2; }
    Result r=reopen(in);
    printf("A9_REOPEN_TEE tee=%d chars_rc=%d begin_rc=%d finish_rc=%d export_rc=%d wrapped_bytes=%zu\n",r.tee,r.chars,r.begin,r.finish,r.exported,r.bytes);
    bool ok=r.tee && !r.chars && !r.begin && !r.finish && !r.exported && r.bytes==68;
    printf("%s stock_reopen=%d wrapped_bytes=%zu\n",ok?"PASS":"FAIL",ok?1:0,r.bytes);
    return ok?0:1;
}
