/* RTP-only compatibility for the stock minijail EOF/errno check.
 * Keep the seccomp policy and all genuine getline/getdelim errors intact.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <sys/types.h>

ssize_t getline(char **line, size_t *capacity, FILE *stream)
{
    errno = 0;
    return getdelim(line, capacity, '\n', stream);
}
