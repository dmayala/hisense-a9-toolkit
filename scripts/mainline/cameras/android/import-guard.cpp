// SPDX-License-Identifier: Apache-2.0
// Camera-service-only ABI adapter for the stock Android11 HandleImporter.
// Failed imports can leave borrowed HIDL handles in the session's cache. The
// stock freeBuffer dereferences a null mapper in that case. Only release handles
// whose import succeeded, exactly once; mapper import/free still own the FDs.
#include <dlfcn.h>
#include <mutex>
#include <unordered_map>
#ifdef A9_IMPORT_HOST_TEST
#include <cstdio>
#define REPORT(message) std::fprintf(stderr, "A9CameraImport: %s\n", message)
#else
#include <android/log.h>
#define REPORT(message) __android_log_write(ANDROID_LOG_ERROR, "A9CameraImport", message)
#endif

struct native_handle;
using Handle = const native_handle *;
static constexpr char importSymbol[] = "_ZN7android8hardware6camera6common4V1_06helper14HandleImporter12importBufferERPK13native_handle";
static constexpr char freeSymbol[] = "_ZN7android8hardware6camera6common4V1_06helper14HandleImporter10freeBufferEPK13native_handle";

static std::mutex mutex;
static std::unordered_map<Handle, void *> owners;

static void *original(const char *symbol)
{
    // Resolve within the original DSO, including when it was loaded RTLD_LOCAL.
    // Keep this reference for the provider's lifetime; never load a new engine.
    void *library = dlopen("camera.device@1.0-impl.so", RTLD_NOW | RTLD_NOLOAD);
    return library ? dlsym(library, symbol) : nullptr;
}

// Export the two exact C++ ABI entrypoints without declaring the stock class
// or depending on its private object layout. 'self' is the original this pointer.
extern "C" __attribute__((visibility("default")))
bool a9_import(void *self, Handle &handle) asm("_ZN7android8hardware6camera6common4V1_06helper14HandleImporter12importBufferERPK13native_handle");
extern "C" __attribute__((visibility("default")))
void a9_free(void *self, Handle handle) asm("_ZN7android8hardware6camera6common4V1_06helper14HandleImporter10freeBufferEPK13native_handle");

bool a9_import(void *self, Handle &handle)
{
    if (!handle)
        return true;
    using Import = bool (*)(void *, Handle &);
    using Free = void (*)(void *, Handle);
    static auto import = reinterpret_cast<Import>(original(importSymbol));
    static auto release = reinterpret_cast<Free>(original(freeSymbol));
    if (!import || !release || !import(self, handle)) {
        // Do not cache a borrowed handle whose FDs die with the HIDL request.
        handle = nullptr;
        REPORT("buffer import failed; borrowed handle not retained");
        return false;
    }
    if (handle) {
        std::lock_guard<std::mutex> lock(mutex);
        owners.emplace(handle, self);
    }
    return true;
}

void a9_free(void *self, Handle handle)
{
    if (!handle)
        return;
    using Free = void (*)(void *, Handle);
    static auto release = reinterpret_cast<Free>(original(freeSymbol));
    {
        std::lock_guard<std::mutex> lock(mutex);
        auto owner = owners.find(handle);
        if (!release || owner == owners.end() || owner->second != self) {
            REPORT("ignored unowned or already released buffer");
            return;
        }
        owners.erase(owner);
    }
    release(self, handle);
}
