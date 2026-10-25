// SPDX-License-Identifier: BSD-3-Clause
// Runs the production converter with A16 C2 layouts, no GPU/Binder/phone.
#include <v4l2_codec2/components/SurfaceRgba.h>
#include <algorithm>
#include <array>
#include <cassert>
#include <cstdio>
#include <vector>
using namespace android;
struct Image {
    C2PlanarLayout yuv{}, rgb{};
    std::vector<uint8_t> y, uv, v, rgba;
    std::array<const uint8_t*,4> in{};
    std::array<uint8_t*,4> out{};
    int w,h,stride,scan,rs;
    Image(int width,int height,int pitch,int scanlines,bool planar=false)
      : y(pitch*scanlines,0xee),uv(pitch*((scanlines+1)/2),0xee),
        v(uv.size(),0xee),rgba((width*4+32)*height+32,0xcd),
        w(width),h(height),stride(pitch),scan(scanlines),rs(width*4+32) {
        yuv.type=C2PlanarLayout::TYPE_YUV;yuv.numPlanes=3;yuv.rootPlanes=planar?3:2;
        rgb.type=C2PlanarLayout::TYPE_RGB;rgb.numPlanes=3;rgb.rootPlanes=1;
        const C2PlaneInfo::channel_t yc[]={C2PlaneInfo::CHANNEL_Y,C2PlaneInfo::CHANNEL_CB,C2PlaneInfo::CHANNEL_CR};
        const C2PlaneInfo::channel_t rc[]={C2PlaneInfo::CHANNEL_R,C2PlaneInfo::CHANNEL_G,C2PlaneInfo::CHANNEL_B,C2PlaneInfo::CHANNEL_A};
        for(int i=0;i<3;++i) {
            auto& p=yuv.planes[i];p.channel=yc[i];p.colInc=i?(planar?1:2):1;
            p.rowInc=pitch;p.colSampling=p.rowSampling=i?2:1;p.allocatedDepth=p.bitDepth=8;
        }
        for(int i=0;i<4;++i) {
            auto& p=rgb.planes[i];p.channel=rc[i];p.colInc=4;p.rowInc=rs;
            p.colSampling=p.rowSampling=1;p.allocatedDepth=p.bitDepth=8;
            out[i]=rgba.data()+i;
        }
        in[0]=y.data();in[1]=uv.data();in[2]=planar?v.data():uv.data()+1;
        std::fill(y.begin(),y.end(),81);
        for(int row=0;row<(h+1)/2;++row) for(int x=0;x<(w+1)/2;++x) {
            uv[row*pitch+x*(planar?1:2)]=90;
            (planar?v:uv)[row*pitch+x*(planar?1:2)+(planar?0:1)]=240;
        }
    }
    bool convert(){return surfaceRgbaConvert(yuv,in.data(),rgb,out.data(),w,h);}
    void red() {
        assert(convert());
        for(int row=0;row<h;++row) {
            for(int x=0;x<w;++x) {
                auto* p=rgba.data()+row*rs+x*4;
                assert(p[0]>=250 && p[1]<=3 && p[2]<=3 && p[3]==255);
            }
            for(int x=w*4;x<rs;++x) assert(rgba[row*rs+x]==0xcd);
        }
        for(unsigned x=rs*h;x<rgba.size();++x)assert(rgba[x]==0xcd);
    }
};
int main() {
    Image venus(320,240,384,256);venus.red();
    puts("PASS Venus padded NV12 384x256 -> visible320x240, RGBA bytes/alpha/row canaries");
    Image odd(321,241,384,256,true);odd.red();
    puts("PASS odd/cropped planar420 + independent padded output stride");
    Image bw(3,2,128,32);
    std::fill(bw.uv.begin(),bw.uv.end(),128);
    bw.y[0]=16;bw.y[1]=235;bw.y[2]=0;bw.y[128]=255;
    assert(bw.convert());
    assert(bw.rgba[0]==0 && bw.rgba[1]==0 && bw.rgba[2]==0);
    assert(bw.rgba[4]>=254 && bw.rgba[5]>=254 && bw.rgba[6]>=254);
    assert(bw.rgba[8]==0 && bw.rgba[bw.rs]>=254);
    puts("PASS limited black/white/clipping");
    Image bad(320,240,384,256);const auto before=bad.rgba;
    const auto src=bad.yuv;const auto dst=bad.rgb;
    bad.yuv.planes[2].bitDepth=10;assert(!bad.convert());bad.yuv=src;
    bad.yuv.planes[1].colInc=0;assert(!bad.convert());bad.yuv=src;
    bad.yuv.planes[0].rowInc=319;assert(!bad.convert());bad.yuv=src;
    bad.yuv.planes[2].channel=C2PlaneInfo::CHANNEL_CB;assert(!bad.convert());bad.yuv=src;
    bad.rgb.planes[0].rowInc=1279;assert(!bad.convert());bad.rgb=dst;
    bad.rgb.planes[0].channel=C2PlaneInfo::CHANNEL_B;assert(!bad.convert());bad.rgb=dst;
    bad.out[2]=nullptr;assert(!bad.convert());bad.out[2]=bad.rgba.data()+2;
    bad.w=4097;assert(!bad.convert());bad.w=320;
    assert(bad.rgba==before);
    bad.rgb.type=C2PlanarLayout::TYPE_RGBA;bad.rgb.numPlanes=4;bad.red();
    puts("PASS malformed layouts refused without writes; RGB3/RGBA4 metadata accepted");
}
