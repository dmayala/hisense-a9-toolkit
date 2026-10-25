/* SPDX-License-Identifier: Apache-2.0 */
/* Whole-message schemas. No pointer, TA name, payload or arbitrary suffix.
 * TRACE explicitly admits public16-bit frame addresses for PIN diagnosis;
 * never a key ID, MAC, counter, nonce or opaque PTBL context.
 */
#ifndef A9_RPMB_DIAG_SCHEMA_H
#define A9_RPMB_DIAG_SCHEMA_H
struct schema { const char *prefix, *fields; };
/* Grammar: U unsigned32, I signed32, S signed64, B bool, P phase1..6, T phase1..3,
 * C cmd1or6, H public SCM function ID, A fixed arginfo0x83, N nargs0..10.
 * R reason0..6, O operation0..4, L boundedlength0..1048576,
 * D callbackcommand/direction0..3, K descriptorIDN0/2/7,
 * J opcode0..2, F request0..5, W read/writecommand2or3, Y refusal1..6,
 * Z mode/phase1..2, V operation0..6, E public16-bit control0..65535,
 * G framecount0..49, X partitionclass0..5. N also bounds context0..10. */
static const struct schema schemas[] = {
 {"A9_RPMB_REGISTER", "request=L mapped=L buf_page_aligned=B len_page_aligned=B cmd=6 arginfo=A"},
 {"A9_RPMB_REGISTER", "check=scm_return cmd=C rc=I"},
 {"A9_RPMB_REGISTER", "phase=P rc=I"},
 {"A9_RPMB_SCM", "check=unknown_convention rc=-22"},
 {"A9_RPMB_SCM", "check=ce_clock rc=I"},
 {"A9_RPMB_SCM", "check=bandwidth rc=I"},
 {"A9_RPMB_SCM", "check=transport_return rc=I"},
 {"A9_RPMB_SCM", "check=missing_ext_pool nargs=N rc=-22"},
 {"A9_RPMB_SCM", "check=invoke fn=H arginfo=A nargs=N size=L buf_page_aligned=B len_page_aligned=B"},
 {"A9_RPMB_SCM", "check=smc_return transport=I status_valid=B secure_status=S mapped_rc=I"},
 {"A9_RPMB_GUARD", "mode=Z allowed=B operation=V reason=R"},
 {"A9_KMTB_WRITE", "phase=T allowed=B operation=J reason=R changed=U"},
 {"A9_KMTB_READ", "phase=T valid=B"},
 {"A9_RPMB_BOOTSTRAP", "phase=P rc=I"},
 {"A9_RPMB_MAP", "phase=Z valid=B"},
 {"A9_RPMB_REQUEST_TRACE", "command=W shape=B reason=R request=F frames=G input_frames=G offset=L first=E count=E map_valid=B context=N table_candidate=B table_valid=B"},
 {"A9_RPMB_WRITE_TRACE", "allowed=B map_valid=B groups_valid=B partition=X first=E offset=E frames=G context=N"},
 {"A9_RPMB_REJECT", "reason=Y opcode=J direction=D bytes_valid=B length_match=B frames=U frame_valid=B request=F"},
 {"A9_RPMB_RESULT", "command=W status_valid=B operation_rc=I"},
 {"A9_RPMB_CALLBACK", "phase=1 command=D size_valid=B"},
 {"A9_RPMB_CALLBACK", "phase=2 command=D rc=I status_valid=B qsee_result=S"},
 {"A9_RPMB_QUERY", "idn=K rpmb_lun=B rc=I length_valid=B"},
 {"A9_QSEE_LOG", "phase=T rc=I"},
 {"A9_QSEE_CAPTURE", "phase=T rc=I"},
 {"A9_PERSIST_RO", "phase=T rc=I"},
};
#endif
