/* a9-eink-ctl: send one command line to the abstract socket a9_eink_socket.
 * Framing (see eink-compat.c): printable ASCII line < 512 bytes ended by '\n'.
 * Only "sct..." commands get a reply line ("OK"/"NO"); we wait up to 2 s for it. */
#include <errno.h>
#include <poll.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#define NAME_DEFAULT "a9_eink_socket"
#define LINE 512

static int usage(void)
{
    fputs("usage: a9-eink-ctl [--socket NAME] <cmd>\n"
          "  c|b|s|p  mode Clear/Balanced/Smooth/Speed;  r  manual clear\n"
          "  sctNNNN  night display (prints the reply line)\n", stderr);
    return 1;
}

int main(int argc, char **argv)
{
    const char *name = NAME_DEFAULT;
    int i = 1;
    if (argc >= 4 && !strcmp(argv[1], "--socket")) { name = argv[2]; i = 3; }
    if (argc - i != 1) return usage();
    const char *cmd = argv[i];
    size_t cl = strlen(cmd), nl = strlen(name);
    if (!cl || cl > LINE - 2 || !nl || nl > sizeof(((struct sockaddr_un *)0)->sun_path) - 1) return usage();
    for (size_t k = 0; k < cl; k++)
        if ((unsigned char)cmd[k] < 32 || (unsigned char)cmd[k] > 126) return usage();

    struct sockaddr_un a = {.sun_family = AF_UNIX};
    memcpy(a.sun_path + 1, name, nl);
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) { perror("socket"); return 1; }
    if (connect(fd, (struct sockaddr *)&a, (socklen_t)(offsetof(struct sockaddr_un, sun_path) + 1 + nl))) {
        perror("connect"); return 1;
    }
    char buf[LINE + 1];
    memcpy(buf, cmd, cl);
    buf[cl] = '\n';
    size_t off = 0;
    while (off < cl + 1) {
        ssize_t n = send(fd, buf + off, cl + 1 - off, MSG_NOSIGNAL);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { perror("send"); return 1; }
        off += (size_t)n;
    }
    if (strncmp(cmd, "sct", 3)) { close(fd); return 0; }
    char r[64];
    size_t used = 0;
    while (used < sizeof(r) - 1) {
        struct pollfd p = {.fd = fd, .events = POLLIN};
        int pr = poll(&p, 1, 2000);
        if (pr < 0 && errno == EINTR) continue;
        if (pr <= 0) { fputs("a9-eink-ctl: no reply within 2 s\n", stderr); return 1; }
        ssize_t n = read(fd, r + used, sizeof(r) - 1 - used);
        if (n <= 0) break;
        used += (size_t)n;
        if (memchr(r, '\n', used)) break;
    }
    r[used] = 0;
    fputs(r, stdout);
    if (used && r[used - 1] != '\n') putchar('\n');
    close(fd);
    return !strncmp(r, "OK", 2) ? 0 : 1;
}
