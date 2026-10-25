// SPDX-License-Identifier: Apache-2.0
#include "../runtime.h"
#include <cassert>
#include <cstdio>
int main() {
    std::vector<a9gnss::Sv> svs;
    assert(a9gnss::parseSvs("A9_SV 3 255 1 3 3 31.4 30 90 255 5 70 1 20 15 180 255 2 310 0 18 45 270\n",svs));
    assert(svs.size()==3 && svs[0].id==3 && svs[0].constellation==1 && svs[0].flags==3);
    assert(svs[1].id==6 && svs[1].constellation==3 && svs[2].id==10 && svs[2].constellation==6);
    assert(a9gnss::parseSvs("A9_SV 0\n",svs) && svs.empty());
    assert(!a9gnss::parseSvs("A9_SV 177\n",svs));
    assert(!a9gnss::parseSvs("A9_SV 1 255 1\n",svs));
    assert(!a9gnss::parseSvs("A9_SV 0 trailing\n",svs));
    assert(a9gnss::parseSvs("A9_SV 3 0 1 3 3 31 0 0 3 1 4 0 55 0 0 255 1 5 3 31 0 0\n",svs));
    assert(svs.size()==2 && svs[0].cn0==0 && svs[0].flags==0 && svs[1].cn0==31 && svs[1].flags==3);
    assert(a9gnss::parseUsed("A9_USED 6 3 35 70 195 205 315\n",svs));
    assert(svs.size()==6 && svs[0].id==3 && svs[1].id==122 && svs[2].id==6 && svs[3].id==195 &&
           svs[4].id==5 && svs[5].id==15 && svs[5].constellation==6 && svs[0].flags==4);
    assert(a9gnss::parseUsed("A9_USED 0\n",svs) && svs.empty());
    assert(!a9gnss::parseUsed("A9_USED 1 65536\n",svs));
    assert(!a9gnss::parseUsed("A9_USED 177\n",svs));
    assert(!a9gnss::parseUsed("A9_USED 0 trailing\n",svs));
    puts("RUNTIME PASS: satellite mapping/CN0 validity, used-list mapping, empty/malformed reports");
}
