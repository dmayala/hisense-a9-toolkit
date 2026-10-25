// SPDX-License-Identifier: Apache-2.0
// A9 POC: never emit inherited QMI payload, APN or address logging.
#include <a6lqmi/log.h>
namespace a6l::qmi {
int gLogLevel = -1;
void logWrite(int, const char*, ...) {}
}
