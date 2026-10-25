// SPDX-License-Identifier: Apache-2.0
// UIM diagnostics; --power-up SLOT explicitly sends one telecom-mode power-up.
// Default is read-only. Never print AIDs, ICCIDs, IMSIs, ATRs or raw TLVs.
#include <a6lqmi/client.h>
#include <a6lqmi/services.h>

#include <cstdio>
#include <cstring>
#include <thread>
#include <chrono>

using namespace a6l::qmi;

int main(int argc, char** argv) {
    unsigned powerSlot = 0;
    if (argc == 3 && std::strcmp(argv[1], "--power-up") == 0 &&
        (std::strcmp(argv[2], "1") == 0 || std::strcmp(argv[2], "2") == 0)) {
        powerSlot = unsigned(argv[2][0] - '0');
    } else if (argc != 1) {
        std::puts("usage: a9-uim-status [--power-up 1|2] (default read-only; explicit option powers up one slot)");
        return 2;
    }
    Client client(makeQrtrTransport(), "a9-uim-status");
    if (!client.start({kSvcUim})) {
        std::puts("uim transport_open=failed");
        return 1;
    }
    if (!client.waitForServices({kSvcUim}, 10000).empty()) {
        std::puts("uim reachable=0");
        return 1;
    }
    uim::CardStatus cs;
    auto result = uim::getCardStatus(client, &cs);
    std::printf("uim reachable=1 card_status_result=%d qmi_error=%u\n",
                int(result.status), unsigned(result.qmiError));
    if (!result.ok()) return 1;
    std::printf("cards=%zu gw_primary=%u gw_secondary=%u\n", cs.cards.size(),
                unsigned(cs.indexGwPrimary), unsigned(cs.indexGwSecondary));
    for (size_t i = 0; i < cs.cards.size(); ++i) {
        const auto& c = cs.cards[i];
        std::printf("slot=%zu state=%u error=%u upin_state=%u apps=%zu\n", i + 1,
                    unsigned(c.state), unsigned(c.error), unsigned(c.upinState), c.apps.size());
        for (size_t j = 0; j < c.apps.size(); ++j) {
            const auto& a = c.apps[j];
            std::printf("slot=%zu app=%zu type=%u state=%u pin1_state=%u\n", i + 1, j,
                        unsigned(a.type), unsigned(a.state), unsigned(a.pin1State));
        }
    }
    // Physical/logical slot mapping; skip the returned personal ICCID field.
    auto slots = client.request(kSvcUim, Message::request(uim::kGetSlotStatus));
    std::printf("slot_status_result=%d qmi_error=%u\n", int(slots.status), unsigned(slots.qmiError));
    if (slots.ok()) {
        if (auto* tlv = slots.msg.get(0x10)) {
            Reader r(*tlv);
            unsigned count = r.u8();
            for (unsigned i = 0; i < count && r.good(); ++i) {
                auto card = r.u32(), active = r.u32();
                auto logical = r.u8();
                r.bytes8();
                if (!r.good()) break;
                // PhysicalCardState is NOT CardState: 1=absent, 2=present.
                const char* name = card == 1 ? "absent" : card == 2 ? "present" : "unknown";
                std::printf("physical_slot=%u physical_state=%u physical_state_name=%s active=%u logical_slot=%u\n",
                            i + 1, card, name, active, unsigned(logical));
            }
        }
    }
    if (powerSlot) {
        // One explicit request, no power-down, retries, PINs or storage setup.
        auto power = uim::setCardPower(client, uint8_t(powerSlot), true);
        std::printf("power_up_slot=%u mode=0 result=%d qmi_error=%u\n",
                    powerSlot, int(power.status), unsigned(power.qmiError));
        std::this_thread::sleep_for(std::chrono::seconds(2));
        auto after = uim::getCardStatus(client, &cs);
        std::printf("after_power_up_card_status_result=%d qmi_error=%u\n",
                    int(after.status), unsigned(after.qmiError));
        if (after.ok()) for (size_t i = 0; i < cs.cards.size(); ++i) {
            const auto& c = cs.cards[i];
            std::printf("after_power_up_slot=%zu state=%u error=%u apps=%zu\n", i + 1,
                        unsigned(c.state), unsigned(c.error), c.apps.size());
        }
        return power.ok() && after.ok() ? 0 : 1;
    }
    return 0;
}
