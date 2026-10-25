# FastRPC interrupted-context restoration

Apply fastrpc-restore-context.patch AFTER the existing S1 FastRPC gate/teardown
patch, against the retained gated source SHA
f6b63cace02af489df6e0597ea90a3c78a3b2164e599a615dadd921b24b219d0.
Rebuild the external fastrpc module for the selected integration kernel and
replace the cached vendor fastrpc.ko; reusing an old cached module does not
apply this source change. No shared source/cache is modified by this commit.

Same-thread restarted user invocations resume the pending transaction before
allocation/marshalling/send. The context owns argument metadata across ioctl
return, matches the request and keeps existing remote/disconnect ownership.
No R17 printks, counters, capture code, PM bypass or battery change is included.

The resulting C source is byte-identical to the stock-study candidate
SHA dafab838d60439d1c2807620359be412588bfabc7c340c1c1874783f02045e24.
That candidate compiled against the noVT 7.2.10-a9+ SDK, rc0/all92 import CRCs
matched. Its host control-flow tests covered retry/repeated interruption,
other thread, changed metadata and disconnected channel. They do not prove
kernel freezer concurrency or DMA lifetime.

Phone evidence supplied by agent: suspend-novtcon-20261021/
r17-fastrpc-restore-20261021T153918Z. Matched early-cut devices diagnostic,
all prepare bodies bypassed, put_noidle: one interruption/one restoration,
ctx_sends=1, global sends2183->2183, no send error, ADSP survived, battery valid,
clean capture. R17 had extra diagnostics; this clean source has identical
restoration logic. Real full device suspend/RTC acceptance remains pending.

Stock reference: stock-adsp-suspend-20261021/report.md section2,
fastrpc_internal_invoke at0xffffff8008669e78. Interrupted contexts restored to
response wait, bypassing rpmsg_send. Local report is host-only static evidence,
not a claim that a measured stock suspend trace proves this fix.
