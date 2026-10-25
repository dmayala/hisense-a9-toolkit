// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <cstdint>
#include <string>
#include <vector>

namespace a9gnss {
struct Sv { int32_t id, constellation; float cn0, elevation, azimuth; int32_t flags; };
struct Fix { double latitude, longitude, accuracy; int64_t utc; };
int64_t monotonicMs();
bool parseSvs(const std::string&, std::vector<Sv>&);
bool parseUsed(const std::string&, std::vector<Sv>&);
bool parseFix(const std::string&, Fix&);
}
