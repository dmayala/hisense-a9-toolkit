// SPDX-License-Identifier: Apache-2.0
// Focused host replay of the observed failed-import/close ownership boundary.
#include <cassert>
#include <cstdlib>
#include <fcntl.h>
#include <unistd.h>
struct native_handle { int fd; };
using Handle = const native_handle *;
extern "C" bool import(void *, Handle &) asm("_ZN7android8hardware6camera6common4V1_06helper14HandleImporter12importBufferERPK13native_handle");
extern "C" void release(void *, Handle) asm("_ZN7android8hardware6camera6common4V1_06helper14HandleImporter10freeBufferEPK13native_handle");
extern "C" { int imports = 0, frees = 0; bool reject = false; }

#ifdef FAKE_IMPORTER
bool import(void *, Handle &handle)
{
    ++imports;
    if (reject) return false;
    auto copy = new native_handle{dup(handle->fd)};
    assert(copy->fd >= 0);
    handle = copy;
    return true;
}
void release(void *, Handle handle)
{
    ++frees;
    assert(close(handle->fd) == 0);
    delete handle;
}
#else
#include <cstdio>
int main()
{
    void *owner = reinterpret_cast<void *>(1), *other = reinterpret_cast<void *>(2);
    int pipefd[2]; assert(pipe(pipefd) == 0);
    native_handle raw{pipefd[0]};
    Handle handle = &raw;
    // Successful import owns a duplicate; duplicate close must not run twice.
    assert(import(owner, handle) && handle != &raw);
    int importedfd = handle->fd;
    release(owner, handle);
    assert(frees == 1 && fcntl(importedfd, F_GETFD) == -1);
    // Android's diagnostic logger may open a socket reusing the released FD.
    // Check closure before exercising the duplicate-release diagnostic.
    release(owner, handle); assert(frees == 1);
    assert(fcntl(raw.fd, F_GETFD) >= 0);
    // Failed import leaves no cached borrowed pointer; close can't free the raw FD.
    reject = true; handle = &raw;
    assert(!import(owner, handle) && handle == nullptr);
    release(owner, &raw); release(owner, handle);
    assert(frees == 1 && fcntl(raw.fd, F_GETFD) >= 0);
    // Null handles need neither mapper import nor release.
    int calls = imports;
    assert(import(owner, handle)); release(owner, handle);
    assert(imports == calls && frees == 1);
    // One importer cannot release another importer's buffer.
    reject = false; handle = &raw; assert(import(owner, handle));
    release(other, handle); assert(frees == 1 && fcntl(handle->fd, F_GETFD) >= 0);
    release(owner, handle); assert(frees == 2);
    close(pipefd[0]); close(pipefd[1]);
    std::puts("PASS: successful/duplicate close, failed import/borrowed FD, null, importer ownership");
}
#endif
