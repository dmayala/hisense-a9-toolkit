// SPDX-License-Identifier: MIT
// Read-only Linux HCI identity gate. Never programs an address or powers HCI.
// ABI: Linux include/net/bluetooth/hci_sock.h, HCIGETDEVINFO.
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

struct hci_dev_info_abi {
    uint16_t dev_id;
    char name[8];
    uint8_t address[6];
    uint32_t flags;
    uint8_t type;
    uint8_t features[8];
    uint32_t pkt_type, link_policy, link_mode;
    uint16_t acl_mtu, acl_pkts, sco_mtu, sco_pkts;
    uint32_t stats[10];
};
_Static_assert(sizeof(struct hci_dev_info_abi) == 92, "Linux HCI ioctl ABI");
_Static_assert(__builtin_offsetof(struct hci_dev_info_abi, address) == 10,
               "Linux bdaddr offset");

static int valid(const uint8_t a[6]) {
    static const uint8_t zero[6], ones[6] = {255,255,255,255,255,255};
    // bdaddr_t and local-bd-address both store canonical bytes in reverse.
    return !(a[5] & 1) && memcmp(a, zero, 6) && memcmp(a, ones, 6);
}

static int read_address(const char *path, uint8_t a[6]) {
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return -1;
    uint8_t buffer[7];
    ssize_t n = read(fd, buffer, sizeof(buffer));
    close(fd);
    if (n != 6 || !valid(buffer)) return -1;
    memcpy(a, buffer, 6);
    return 0;
}

static int matches(const struct hci_dev_info_abi *info, const uint8_t expected[6]) {
    return !memcmp(info->name, "hci0\0", 5) &&
        (info->type & 15) == 3 /* HCI_UART */ && valid(info->address) &&
        !memcmp(info->address, expected, 6) &&
        !(info->flags & ((1u << 0) | (1u << 1) | (1u << 8)) /* UP/INIT/RAW */);
}

static uint16_t le16(const uint8_t *p) { return p[0] | ((uint16_t)p[1] << 8); }

static int ready_reply(const uint8_t *p, size_t n) {
    // MGMT_EV_CMD_COMPLETE(READ_INDEX_LIST), status success, exactly hci0.
    return n == 13 && le16(p) == 1 && le16(p+2) == 0xffff &&
        le16(p+4) == 7 && le16(p+6) == 3 && p[8] == 0 &&
        le16(p+9) == 1 && le16(p+11) == 0;
}

static int controller_ready(void) {
    // READ_INDEX_LIST excludes SETUP/CONFIG/UNCONFIGURED/USER_CHANNEL devices;
    // HCIGETDEVINFO alone does not expose all these kernel-private flags.
    int fd = socket(AF_BLUETOOTH, SOCK_RAW | SOCK_CLOEXEC | SOCK_NONBLOCK, 1);
    if (fd < 0) return 0;
    struct { sa_family_t family; uint16_t device, channel; } address =
        { AF_BLUETOOTH, 0xffff, 3 /* HCI_CHANNEL_CONTROL */ };
    const uint8_t request[6] = {3, 0, 255, 255, 0, 0};
    uint8_t response[64];
    struct pollfd wait = { .fd = fd, .events = POLLIN };
    int ok = 0;
    if (!bind(fd, (struct sockaddr *)&address, sizeof(address)) &&
        send(fd, request, sizeof(request), MSG_DONTWAIT) == (ssize_t)sizeof(request) &&
        poll(&wait, 1, 100) == 1 && (wait.revents & POLLIN)) {
        ssize_t n = recv(fd, response, sizeof(response), MSG_DONTWAIT);
        if (n >= 0) ok = ready_reply(response, (size_t)n);
    }
    close(fd);
    return ok;
}

static int selftest(void) {
    uint8_t a[6] = {3,4,5,6,7,2}, z[6] = {0};
    if (!valid(a) || valid(z)) return 1;
    struct hci_dev_info_abi info = { .name = "hci0", .type = 3 };
    memcpy(info.address, a, 6);
    if (!matches(&info, a)) return 1;
    info.flags = 1;
    if (matches(&info, a)) return 1;
    info.flags = 0;
    info.flags = 2;
    if (matches(&info, a)) return 1;
    info.flags = 256;
    if (matches(&info, a)) return 1;
    info.flags = 0;
    info.type = 1;
    if (matches(&info, a)) return 1;
    info.type = 3;
    info.address[0] ^= 1;
    if (matches(&info, a)) return 1;
    info.address[0] ^= 1;
    info.name[3] = '1';
    if (matches(&info, a)) return 1;
    uint8_t reply[13] = {1,0,255,255,7,0,3,0,0,1,0,0,0};
    if (!ready_reply(reply, sizeof(reply)) || ready_reply(reply, 12)) return 1;
    reply[8] = 1;
    if (ready_reply(reply, sizeof(reply))) return 1;
    reply[8] = 0;
    reply[11] = 1;
    if (ready_reply(reply, sizeof(reply))) return 1;
    a[5] = 3;
    if (valid(a)) return 1;
    memset(a, 255, sizeof(a));
    return valid(a) ? 1 : 0;
}

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "--self-test")) return selftest();
    if (argc != 3) {
        fputs("usage: a9-bt-identity EXPECTED_LE_FILE DT_LE_PROPERTY\n", stderr);
        return 2;
    }
    uint8_t expected[6], dt[6];
    if (read_address(argv[1], expected) || read_address(argv[2], dt) ||
        memcmp(expected, dt, 6)) {
        fputs("Bluetooth identity inputs invalid or mismatched\n", stderr);
        return 2;
    }
    if (!controller_ready()) return 1;
    int fd = socket(AF_BLUETOOTH, SOCK_RAW | SOCK_CLOEXEC, 1 /* BTPROTO_HCI */);
    if (fd < 0) return 1;
    struct hci_dev_info_abi info = { .dev_id = 0 };
    int rc = ioctl(fd, _IOR('H', 211, int), &info);
    close(fd);
    // HCI_CHANNEL_USER requires the ordinary kernel device to be down.
    return rc < 0 || !matches(&info, expected) ? 1 : 0;
}
