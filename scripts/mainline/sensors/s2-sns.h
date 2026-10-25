/* SPDX-License-Identifier: GPL-2.0-only */
/* Minimal SNS client (QMI service 400 over QRTR) for the A9 sensors PD.
 *
 * Every layout here is taken from the stock vendor binaries (golden vendor_b,
 * runs/sensors-s2-plan.md and scripts/mainline/sensors/s2-sns-layouts.md cite
 * the addresses). Nothing is guessed: a field we could not pin is not sent.
 * No FastRPC, no files, no persist, no calibration: QRTR datagrams only.
 */
#ifndef S2_SNS_H
#define S2_SNS_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

#define S2_SNS_SERVICE 400u
#define S2_QMI_REQ 0x20u          /* SNS_CLIENT_REQ (req/resp) */
#define S2_QMI_REPORT_IND 0x21u   /* payload <= 1000 */
#define S2_QMI_JUMBO_IND 0x22u    /* payload <= 62000 */
#define S2_PAYLOAD_MAX 1000u      /* request TLV 0x01 array max (libssc send_request cmp #0x3e9) */
#define S2_JUMBO_MAX 62000u

/* message ids (libsnsapi *_IsValid bodies and EnumEntry tables) */
#define S2_MSGID_ATTR_REQ 1u
#define S2_MSGID_FLUSH_REQ 2u
#define S2_MSGID_DISABLE_REQ 10u
#define S2_MSGID_ATTR_EVENT 128u
#define S2_MSGID_FLUSH_EVENT 129u
#define S2_MSGID_ERROR_EVENT 130u
#define S2_MSGID_SUID_REQ 512u
#define S2_MSGID_SENSOR_CONFIG 513u
#define S2_MSGID_ON_CHANGE_CONFIG 514u
#define S2_MSGID_SUID_EVENT 768u          /* from the SUID sensor */
#define S2_MSGID_PHYSICAL_CONFIG_EVENT 768u /* from a data sensor */
#define S2_MSGID_PROXIMITY_EVENT 769u
#define S2_MSGID_PROXIMITY_RECURRENT 1031u
#define S2_MSGID_SENSOR_EVENT 1025u

/* sns_std_sensor_attr_id (EnumEntry table, libsnsapi Ghidra 0x25a478) */
#define S2_ATTR_NAME 0
#define S2_ATTR_VENDOR 1
#define S2_ATTR_TYPE 2
#define S2_ATTR_AVAILABLE 3
#define S2_ATTR_RATES 6
#define S2_ATTR_STREAM_TYPE 16

#define S2_PROC_APSS 1u        /* sns_std_client_processor (Ghidra 0x25b300) */
#define S2_DELIVERY_WAKEUP 0u  /* sns_client_delivery (Ghidra 0x25b798) */
#define S2_DELIVERY_NO_WAKEUP 1u

struct s2_buf { uint8_t *p; size_t n, cap; int err; };
struct s2_suid { uint64_t low, high; };

/* protobuf-lite subset (proto2 wire format) */
void s2_pb_varint(struct s2_buf *b, uint64_t v);
void s2_pb_tag(struct s2_buf *b, unsigned field, unsigned wire);
void s2_pb_fixed32(struct s2_buf *b, unsigned field, uint32_t v);
void s2_pb_fixed64(struct s2_buf *b, unsigned field, uint64_t v);
void s2_pb_bool(struct s2_buf *b, unsigned field, int v);
void s2_pb_enum(struct s2_buf *b, unsigned field, uint32_t v);
void s2_pb_float(struct s2_buf *b, unsigned field, float v);
void s2_pb_bytes(struct s2_buf *b, unsigned field, const void *data, size_t len);

/* message builders; return encoded length, 0 on overflow */
size_t s2_suid_req(uint8_t *out, size_t cap, const char *data_type, int default_only);
size_t s2_sensor_config(uint8_t *out, size_t cap, float sample_rate_hz);
/* sns_client_request_msg; payload may be NULL (request{} still carries batching) */
size_t s2_client_request(uint8_t *out, size_t cap, struct s2_suid suid, uint32_t msg_id,
                         uint32_t delivery, const uint8_t *payload, size_t payload_len,
                         int with_batching, uint32_t batch_period_us);
/* QMI SNS_CLIENT_REQ frame: header + TLV 0x01 {u16 len, bytes} + TLV 0x10 use_jumbo_report=1 */
size_t s2_qmi_request(uint8_t *out, size_t cap, uint16_t txn, const uint8_t *pb, size_t pb_len);

/* QMI decoding */
struct s2_qmi_resp { uint16_t result, error; int has_client_id; uint64_t client_id; int has_result; uint32_t sns_result; };
int s2_qmi_decode_response(const uint8_t *b, size_t n, uint16_t txn, struct s2_qmi_resp *out);
/* indication 0x21/0x22: returns 0 and points *payload into b */
int s2_qmi_decode_indication(const uint8_t *b, size_t n, uint64_t *client_id,
                             const uint8_t **payload, size_t *payload_len);

/* protobuf event decoding */
#define S2_MAX_EVENTS 64
#define S2_MAX_SUIDS 8
#define S2_MAX_FLOATS 16
struct s2_event { uint32_t msg_id; uint64_t timestamp; const uint8_t *payload; size_t payload_len; };
struct s2_event_msg { struct s2_suid suid; int has_suid; size_t n; struct s2_event ev[S2_MAX_EVENTS]; };
int s2_decode_event_msg(const uint8_t *b, size_t n, struct s2_event_msg *out);
struct s2_suid_event { char data_type[64]; size_t n; struct s2_suid suid[S2_MAX_SUIDS]; };
int s2_decode_suid_event(const uint8_t *b, size_t n, struct s2_suid_event *out);
struct s2_sensor_event { size_t n; float data[S2_MAX_FLOATS]; int has_status; uint32_t status; };
int s2_decode_sensor_event(const uint8_t *b, size_t n, struct s2_sensor_event *out);
/* Stock sns_proximity.proto: field 1 enum FAR=0/NEAR=1, 2 raw ADC, 3 status. */
int s2_decode_proximity_event(const uint8_t *b, size_t n, int *near, uint32_t *status);
struct s2_attrs {
    char name[64], vendor[64], type[64];
    int has_stream_type; int64_t stream_type;
    int has_available; int available;
    size_t n_rates; float rates[S2_MAX_FLOATS];
};
int s2_decode_attr_event(const uint8_t *b, size_t n, struct s2_attrs *out);
/* sns_std_error_event { enum error = 1 } */
int s2_decode_error_event(const uint8_t *b, size_t n, uint32_t *error);

/* Review 79j N1: the parts the stock registry/config name for this phone (S1 attempt 7 read their registry groups
 * and JSON configs: sns_stk3b6x_0, sns_kxtj3_0, sns_stk8baxx_0). A sensor whose NAME attribute does not contain
 * one of these tokens (case-insensitive) is not the part we expect and is refused. Returns 1 match, 0 no match,
 * -1 unknown data type. */
int s2_part_expected(const char *data_type, const char *name);
const char *s2_part_tokens(const char *data_type);   /* expected part tokens, or NULL */
/* printable copy of a protobuf string for logs: control bytes, quote and backslash become '?' */
void s2_sanitize(char *out, size_t cap, const uint8_t *in, size_t len);
#ifdef __cplusplus
}
#endif
#endif
