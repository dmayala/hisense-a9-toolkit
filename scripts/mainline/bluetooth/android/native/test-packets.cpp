// SPDX-License-Identifier: Apache-2.0
#include "hci_packets.h"
#include <array>
#include <cassert>
#include <vector>
#include <cstdio>
int main() {
    const std::array<uint8_t,3> reset{3,12,0};
    assert(valid_hci_packet(1,reset.data(),reset.size()));
    assert(!valid_hci_packet(1,reset.data(),2));
    assert(!valid_hci_packet(0,reset.data(),3));
    assert(!valid_hci_packet(255,reset.data(),3));
    for (uint8_t type=1;type<=5;++type)
        assert(!valid_hci_packet(type,nullptr,0));
    std::array<uint8_t,6> event{14,4,1,3,12,0};
    assert(valid_hci_packet(4,event.data(),event.size()));
    assert(!valid_hci_packet(4,event.data(),event.size()-1));
    event[1]=255;
    assert(!valid_hci_packet(4,event.data(),event.size()));
    std::array<uint8_t,7> acl{1,0,3,0,1,2,3};
    assert(valid_hci_packet(2,acl.data(),acl.size()));
    acl[3]=1;
    assert(!valid_hci_packet(2,acl.data(),acl.size()));
    std::array<uint8_t,4> sco{1,0,1,0};
    assert(valid_hci_packet(3,sco.data(),sco.size()));
    sco[2]=0;
    assert(!valid_hci_packet(3,sco.data(),sco.size()));
    std::array<uint8_t,5> iso{1,0,1,0xc0,7};
    assert(valid_hci_packet(5,iso.data(),iso.size()));
    iso[2]=2;
    assert(!valid_hci_packet(5,iso.data(),iso.size()));
    std::vector<uint8_t> maximum_acl(65539);
    maximum_acl[2]=255; maximum_acl[3]=255;
    assert(valid_hci_packet(2,maximum_acl.data(),maximum_acl.size()));
    maximum_acl.push_back(0);
    assert(!valid_hci_packet(2,maximum_acl.data(),maximum_acl.size()));
    // Sweep declared lengths and every prefix around each legal boundary.
    unsigned checked=0;
    for (uint8_t type=1;type<=5;++type) {
        std::vector<uint8_t> body(1100);
        for (unsigned declared=0;declared<256;++declared) {
            std::fill(body.begin(),body.end(),0);
            unsigned header=(type==4?2:(type==2||type==5?4:3));
            body[type==4?1:2]=declared;
            for (unsigned n=0;n<header+declared+2;++n) {
                assert(valid_hci_packet(type,body.data(),n)==(n==header+declared));
                ++checked;
            }
        }
    }
    std::printf("HCI packet length/boundary checks PASS (%u swept cases plus explicit cases)\n",checked);
}
