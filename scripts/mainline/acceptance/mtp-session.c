/* Read-only host MTP storage/session check. Never reset the composite USB device:
 * libmtp's Android FORCE_RESET_ON_CLOSE quirk would also tear down our ADB owner.
 * This exported executable symbol interposes libmtp's libusb PLT call. */
#include <libmtp.h>
#include <stdio.h>
#include <stdlib.h>

struct libusb_device_handle;
static unsigned resets_refused;
int libusb_reset_device(struct libusb_device_handle *handle)
{
    (void)handle;
    resets_refused++;
    return -12; /* LIBUSB_ERROR_NOT_SUPPORTED; never claim a reset succeeded. */
}

int main(void)
{
    LIBMTP_raw_device_t *raw = NULL;
    int count = 0, ok = 0;
    LIBMTP_Init();
    if (LIBMTP_Detect_Raw_Devices(&raw, &count) != LIBMTP_ERROR_NONE || count != 1) {
        free(raw);
        puts("MTP_SESSION 0");
        return 1;
    }
    LIBMTP_mtpdevice_t *device = LIBMTP_Open_Raw_Device_Uncached(&raw[0]);
    free(raw);
    if (device) {
        /* Actual GetStorageIDs/GetStorageInfo responses, no folder/object names
         * or data retrieval. The host already proved this is the ADB device. */
        ok = LIBMTP_Get_Storage(device, LIBMTP_STORAGE_SORTBY_NOTSORTED) == 0
                && device->storage != NULL && device->storage->id != 0;
        LIBMTP_Release_Device(device);
    }
    printf("MTP_SESSION %d\nMTP_RESETS_REFUSED %u\n", ok, resets_refused);
    return ok ? 0 : 1;
}
