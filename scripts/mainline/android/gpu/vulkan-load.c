/* Host/QEMU bionic load check only: no Vulkan instance, DRM node or GPU work. */
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
/* Public hw_module_t prefix from Android hardware/hardware.h. */
struct module_header {
    uint32_t tag;
    uint16_t module_api_version, hal_api_version;
    const char *id, *name, *author;
};
int main(int argc, char **argv)
{
    if (argc != 2) return 2;
    void *lib = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!lib) { fprintf(stderr, "%s\n", dlerror()); return 1; }
    const struct module_header *hal = dlsym(lib, "HMI");
    /* Turnip provenance is checked against Mesa SHA256SUMS by the host. */
    int ok = hal && hal->id && !strcmp(hal->id, "vulkan");
    printf("VULKAN_LOAD relocations=1 turnip_hal=%d hardware_tested=0\n", ok);
    dlclose(lib);
    return !ok;
}
