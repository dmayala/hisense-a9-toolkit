# SNS client message layouts: what the stock vendor actually sends

Status: measured from the binaries, 2026-10-18. Offline. These layouts drive `s2-sns.c`; the offline test
`tests/mainline-sensors/test-s2-sns.py` re-encodes them independently.

**Sources.** Golden stock vendor (`~/A9-build/mainline/android/golden/vendor_b.img`, the image slot A runs today),
extracted with `debugfs` to `~/A9-build/mainline/sensors-s1/rea/stock-vendor/`:

| File | sha256 |
|---|---|
| `libsnsapi.so` | `5ca0264e...` |
| `libssc.so` | `ea5a7903...` |
| `sensors.ssc.so` | `88ac9308...` |
| `libqmi_cci.so` | `efc98b7d...` |

**Tools.** REA (Ghidra 12.1.4) and `aarch64 objdump`. A Ghidra address = file VA + 0x100000.

**Method for field numbers.** For every protobuf-lite `SerializeWithCachedSizes`, I paired each `WireFormatLite::Write*`
call with the immediate in `w0`. The script is in the session scratchpad; the output is reproduced below.

## QMI service (libsnsapi `SNS_CLIENT_SVC_qmi_idl_service_object_v01`, Ghidra 0x264570)

Service object: library_version 6, idl_version 1, **service 400**, max_msg_len 62016; 3 request, 3 response and 2
indication messages.

**Message table** (0x1627e0; each entry is `{qmi id, table id, max}`):

| Kind | Entries |
|---|---|
| Requests | `0x1e`; **`0x20` (max 1009)**; `0x23` |
| Responses | `0x1e`; **`0x20` (max 25)**; `0x23` |
| Indications | **`0x21` (max 1016)**; **`0x22` (max 62016)** |

**Encoded TLVs** (type table 0x25a008 -> messages 0x25a030 -> data 0x162810..0x162845):

| Message | TLVs |
|---|---|
| req 0x20 (C struct 1008 bytes) | `0x01` array of bytes, u16 length, max 1000; `0x10` u8, optional |
| resp 0x20 | `0x02` QMI result {u16 result, u16 error}; `0x10` u64 client_id; `0x11` u32 result |
| ind 0x21 | `0x01` u64 client_id; `0x02` array, max 1000 |
| ind 0x22 | `0x01` u64 client_id; `0x02` array, max **62000** (`30 f2` in the encoding) |

**The sizes cross-check:**
* 1009 = (3+2+1000) + (3+1)
* 25 = 7 + 11 + 7
* 1016 = 11 + 1005
* 62016 = 11 + 3 + 2 + 62000

**What stock sends.** libssc `ssc_qmi_connection::send_request` (0x114248):
* memsets the 1008-byte request (`mov w2,#0x3f0`);
* refuses a payload > 1000 (`cmp x8,#0x3e9`);
* copies the payload to +4;
* sets **`use_jumbo_report_valid=1, use_jumbo_report=1`** (`mov w8,#0x101; strh w8,[sp,#1220]`, i.e. struct +1004);
* sends msg **0x20** (`qmi_client_send_msg_sync/async`, `w1=#0x20`).

QRTR: `libqmi_cci` `qmi_cci_xprt_qrtr_supported` (0x109f18) opens `socket(42, SOCK_DGRAM|SOCK_CLOEXEC, 0)`.

## Protobuf messages (libsnsapi serializers)

| Message (serializer VA, Ghidra) | Fields (number, kind) |
|---|---|
| `sns_client_request_msg` (0x2249b0) | 1 suid (msg), 2 msg_id (**fixed32**), 3 susp_config (msg), 4 request (msg) |
| `..._suspend_config` (0x222ce8) | 1 client_proc_type (enum, member +0x2c), 2 delivery_type (enum, member +0x28), 3 nowakeup_msg_ids (fixed32) |
| `sns_std_suid` (0x207ad8) | 1 suid_low (fixed64), 2 suid_high (fixed64) |
| `sns_std_request` (0x20ec3c) | 1 batching (msg), 2 payload (bytes), 3 is_passive (bool) |
| `sns_std_request_batch_spec` (0x20daf0) | 1 batch_period (uint32), 2 flush_period (uint32), 3 and 4 (bool) |
| `sns_suid_req` (0x21cfd0) | 1 data_type (string), 2 register_updates (bool), 3 default_only (bool) |
| `sns_suid_event` (0x21e708) | 1 data_type (string), 2 suid (repeated msg) |
| `sns_std_sensor_config` (0x1d2e80) | 1 sample_rate (float) |
| `sns_std_sensor_event` (0x1d3e04) | 1 data (repeated float, written unpacked), 2 status (enum) |
| `sns_std_attr_req` (0x20f9e8) | 2 (bool) |
| `sns_std_attr_event` (0x210ec0) | 1 attributes (repeated msg) |
| `sns_std_attr` (0x20c8b0) | 1 attr_id (int32), 2 value (msg) |
| `sns_std_attr_value` (0x209080) | 1 values (repeated msg) |
| `sns_std_attr_value_data` (0x20ad5c) | 1 subtype (msg), 2 str, 3 flt (float), 4 sint (**sfixed64**), 5 boolean |
| `sns_std_error_event` (0x212084) | 1 error (enum) |
| `sns_client_event_msg` (0x227894) | 1 suid, 2 events (repeated msg) |
| `..._sns_client_event` (0x225f2c) | 1 msg_id (fixed32), 2 timestamp (fixed64), 3 payload (bytes) |

## Enum values (`IsValid` bodies, objdump; EnumEntry tables, REA `read_bytes`)

| Enum | Values |
|---|---|
| `sns_client_msgid` (0x121f74) | valid only 10 = DISABLE_REQ |
| `sns_std_msgid` (0x10d28c) | 1, 2 (ATTR_REQ, FLUSH_REQ); 128-130 (ATTR_EVENT, FLUSH_EVENT, ERROR_EVENT) |
| `sns_suid_msgid` (0xec010) | 512 (SUID_REQ), 768 (SUID_EVENT) |
| `sns_std_sensor_msgid` (0xd2210) | 513 (SENSOR_CONFIG), 514 (ON_CHANGE_CONFIG), 768 (PHYSICAL_CONFIG_EVENT), 1025 (SENSOR_EVENT) |
| `sns_std_client_processor` (table 0x25b300) | SSC 0, APSS 1, ADSP 2, MDSP 3, CDSP 4 |
| `sns_client_delivery` (table 0x25b798) | WAKEUP 0, NO_WAKEUP 1 |
| `sns_std_sensor_attr_id` (table 0x25a478, 29 entries) | NAME 0, VENDOR 1, TYPE 2, AVAILABLE 3, VERSION 4, API 5, RATES 6, RESOLUTIONS 7, FIFO_SIZE 8, ACTIVE_CURRENT 9, SLEEP_CURRENT 10, RANGES 11, OP_MODES 12, DRI 13, STREAM_SYNC 14, EVENT_SIZE 15, **STREAM_TYPE 16**, DYNAMIC 17, HW_ID 18, RIGID_BODY 19, PLACEMENT 20, PHYSICAL_SENSOR 21, PHYSICAL_SENSOR_TESTS 22, SELECTED_RESOLUTION 23, SELECTED_RANGE 24, ADDITIONAL_LOW_LATENCY_RATES 25, PASSIVE_REQUEST 26, DIRECT_CHANNEL_SAMPLE_RATE 27, DIRECT_CHANNEL_REPORT_RATE 28 |
| `sns_std_sensor_sample_status` | 0..3 |

## What the stock HAL puts in requests

**SUID lookup.** libssc `suid_lookup::request_suid` (0x116184):
* `sns_suid_req{data_type, register_updates=1, default_only=<arg>}` serialized into `request.payload`;
* `msg_id=0x200`;
* suid low = high = **0xabababababababab**;
* susp_config `delivery_type(+0x28)=0` (WAKEUP), `client_proc_type(+0x2c)=1` (APSS).

**Sensor config.** sensors.ssc.so `ssc_sensor::send_sensor_config_request` (0x14ee68):
* `request.batching.batch_period` is always set;
* susp_config `delivery_type = ~wakeup_flag & 1` (non-wakeup sensors -> NO_WAKEUP);
* `client_proc_type = 1`.

**Not replicated.** The resampler path and the flush period. Those requests are never sent by the probe.

## What s2-sns sends (exactly these, nothing else)

| Purpose | msg_id | delivery | request |
|---|---|---|---|
| SUID lookup | 512 | 0 | `{payload: suid_req(dt, register_updates=1, default_only=1)}` |
| Attributes | 1 | 1 | `{payload: <empty>}` |
| ALS on-change | 514 | 1 | `{batching{batch_period=0}, payload: <empty>}` |
| ALS streaming (if `STREAM_TYPE != 1`) | 513 | 1 | `{batching{0}, payload: sensor_config{5.0}}` |
| Accel | 513 | 1 | `{batching{0}, payload: sensor_config{50.0}}` |
| Disable | 10 | 1 | `{}` |

Every request is wrapped in QMI 0x20 with TLV 0x01 (u16 length + bytes) and TLV 0x10 = 1.
