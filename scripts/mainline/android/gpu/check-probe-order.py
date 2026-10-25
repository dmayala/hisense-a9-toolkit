#!/usr/bin/env python3
"""check-probe-order.py -- will the GPU (and the display controller) find every
supplier bound when their drivers probe? Offline host check (G1 attempt 1,
2026-10-16). runs/gpu.md "G1 attempt 1 fix".

  check-probe-order.py <dtb> <kernel .config> <module list file> [--mdss]

MEASURED on the phone (attempt 1): the kernel's deferred-probe timeout runs out
~10 s after boot (11.7 s), long before /vendor modules load (~50 s). A BUILT-IN
driver whose supplier is only a module therefore fails for good at the timeout
(59a0000.iommu: "probe with driver arm-smmu failed with error -110"), and a
MODULE driver loaded later whose supplier is not bound yet is probed "ignoring
dependency" and fails (adreno, msm-mdss -110). Rules checked, for every
consumer below and every supplier it names in clocks / power-domains / iommus /
interconnects / phys / qcom,gmu / nvmem-cells, recursively (each supplier is in
turn checked as a consumer of its own suppliers; review 34 F2):
  consumer built in  -> supplier driver built in
  consumer module    -> supplier built in, or a module EARLIER in the list
The list is the order the vendor loads modules in (modules.gpu for the vkms
image, modules.load for the E Ink image). --mdss adds the display subsystem
(E Ink image) and its children DPU, DSI, DSI PHY; without it the vkms image must keep MDSS unbound (a9ml-gpu.sh
sets its driver_override), which is checked by tests/test-gpu-setup.sh."""
import os, struct, sys

def fdt(path):
    d = open(path, 'rb').read()
    magic, _, off_struct, off_strings = struct.unpack_from('>4I', d, 0)
    assert magic == 0xd00dfeed, f'{path}: not a DTB'
    nodes, stack, i = {}, [], off_struct
    while True:
        tok = struct.unpack_from('>I', d, i)[0]; i += 4
        if tok == 1:
            e = d.index(b'\0', i); stack.append(d[i:e].decode()); i = (e + 4) & ~3
            nodes['/'.join(stack) or '/'] = {}
        elif tok == 2: stack.pop()
        elif tok == 3:
            ln, no = struct.unpack_from('>2I', d, i); i += 8
            pn = d[off_strings + no:d.index(b'\0', off_strings + no)].decode()
            nodes['/'.join(stack) or '/'][pn] = d[i:i + ln]; i = (i + ln + 3) & ~3
        elif tok == 4: continue
        elif tok == 9: break
    return nodes

# supplier/consumer compatible -> (Kconfig symbol, module name)
DRV = {
    'qcom,sm6115-gpucc': ('SM_GPUCC_6115', 'gpucc-sm6115'),
    'qcom,sm6115-dispcc': ('SM_DISPCC_6115', 'dispcc-sm6115'),
    'qcom,gcc-sm6115': ('SM_GCC_6115', 'gcc-sm6115'),
    'qcom,rpmcc': ('QCOM_CLK_SMD_RPM', 'clk-smd-rpm'),   # review 34 F1: the real symbol
    'fixed-clock': ('COMMON_CLK', 'clk-fixed'),
    'qcom,sm6115-dpu': ('DRM_MSM', 'msm'),
    'qcom,sm6115-dsi-ctrl': ('DRM_MSM', 'msm'),
    'qcom,dsi-phy-14nm-2290': ('DRM_MSM', 'msm'),
    # no driver of its own: adreno uses it through qcom,gmu; same module as the GPU
    'qcom,adreno-gmu-wrapper': ('DRM_MSM', 'msm'),
    'qcom,sm6115-rpmpd': ('QCOM_RPMPD', 'rpmpd'),
    'qcom,qfprom': ('NVMEM_QCOM_QFPROM', 'nvmem_qfprom'),
    'arm,mmu-500': ('ARM_SMMU', 'arm_smmu'),
    'qcom,sm6115-bimc': ('INTERCONNECT_QCOM_SM6115', 'icc-sm6115'),
    'qcom,sm6115-config-noc': ('INTERCONNECT_QCOM_SM6115', 'icc-sm6115'),
    'qcom,sm6115-cnoc': ('INTERCONNECT_QCOM_SM6115', 'icc-sm6115'),
    'qcom,sm6115-snoc': ('INTERCONNECT_QCOM_SM6115', 'icc-sm6115'),
    'qcom,sm6115-mmrt-virt': ('INTERCONNECT_QCOM_SM6115', 'icc-sm6115'),
    'qcom,sm6115-mmnrt-virt': ('INTERCONNECT_QCOM_SM6115', 'icc-sm6115'),
    'qcom,adreno': ('DRM_MSM', 'msm'),
    'qcom,sm6115-mdss': ('DRM_MSM', 'msm'),
}
CELLS = {'clocks': '#clock-cells', 'power-domains': '#power-domain-cells', 'iommus': '#iommu-cells',
         'interconnects': '#interconnect-cells', 'phys': '#phy-cells', 'qcom,gmu': None}

def main():
    a = sys.argv[1:]
    mdss = '--mdss' in a; a = [x for x in a if x != '--mdss']
    dtb, cfgp, listp = a
    N = fdt(dtb)
    cfg = {}
    for l in open(cfgp):
        if l.startswith('CONFIG_') and '=' in l:
            k, v = l.strip().split('=', 1); cfg[k[7:]] = v
    order = [os.path.basename(l.strip())[:-3].replace('-', '_') for l in open(listp) if l.strip()]
    byph = {struct.unpack('>I', p['phandle'])[0]: k for k, p in N.items() if 'phandle' in p}
    def compat(path):
        c = N.get(path, {}).get('compatible', b'')
        return [x.decode() for x in c.split(b'\0') if x]
    def driver(path):
        for c in compat(path):
            if c in DRV: return DRV[c]
        return None
    def suppliers(path):
        p, out = N[path], set()
        for prop, cells in CELLS.items():
            if prop not in p: continue
            v = struct.unpack(f'>{len(p[prop]) // 4}I', p[prop]); i = 0
            while i < len(v):
                s = byph[v[i]]; n = struct.unpack('>I', N[s][cells])[0] if cells and cells in N[s] else 0
                out.add(s); i += 1 + n
        if 'nvmem-cells' in p:
            for ph in struct.unpack(f'>{len(p["nvmem-cells"]) // 4}I', p['nvmem-cells']):
                out.add(byph[ph].rsplit('/', 1)[0])   # the cell's provider device
        return sorted(out)
    consumers = ['/soc@0/gpu@5900000', '/soc@0/iommu@59a0000']
    if mdss:
        consumers.append('/soc@0/display-subsystem@5e00000')
        # review 34 F2: the MDSS children (DPU, DSI, DSI PHY) too
        consumers += sorted(k for k in N if k.startswith('/soc@0/display-subsystem@5e00000/') and k.count('/') == 3
                            and compat(k) and N[k].get('status', b'okay\0').rstrip(b'\0') == b'okay')
    ok = True
    seen = set()
    def check(c, depth):
        # review 34 F2: suppliers are checked RECURSIVELY (gpucc's own rpmcc/gcc,
        # the GMU wrapper's power domains, ...), each with its own driver as consumer
        nonlocal ok
        if c in seen: return
        seen.add(c)
        st = N[c].get('status', b'okay\0').rstrip(b'\0').decode()
        cd = driver(c)
        ind = '  ' * (depth + 1)
        if st != 'okay' or not cd:
            print(f'{ind}{c} {compat(c)}: status {st}, driver {cd}: FAIL (not enabled/known)'); ok = False; return
        cv = cfg.get(cd[0], 'n')
        for s in suppliers(c):
            sd = driver(s)
            if sd is None:
                print(f'{ind}{c} <- {s} {compat(s)}: unknown supplier driver: FAIL'); ok = False; continue
            sv = cfg.get(sd[0], 'n')
            sm, cm = sd[1].replace('-', '_'), cd[1].replace('-', '_')
            if sv == 'y': res = 'ok (built in)'
            elif reaches(s, c):
                # e.g. dispcc takes the DSI PHY PLL as a parent while the PHY uses
                # dispcc clocks: fw_devlink relaxes such cycles ("Fixed dependency
                # cycle(s)", MEASURED in every E Ink boot) and the clock framework
                # adopts the orphan parent later; not a probe-time dependency
                res = 'ok (dependency cycle, relaxed by fw_devlink)'
            elif sv == 'm' and cv == 'm' and sm == cm: res = 'ok (same module)'
            elif sv == 'm' and cv == 'm' and sm in order and cm in order and order.index(sm) < order.index(cm):
                res = f'ok (module before {cd[1]})'
            else:
                res = f'FAIL (consumer {cd[0]}={cv}, supplier {sd[0]}={sv}: misses the deferred-probe timeout or the load order)'; ok = False
            print(f'{ind}{c} [{cd[0]}={cv}] <- {s} [{sd[0]}={sv}]: {res}')
            check(s, depth + 1)
    def reaches(a, b, seen=None):
        seen = seen or set()
        if a in seen: return False
        seen.add(a)
        return any(x == b or reaches(x, b, seen) for x in suppliers(a))
    for c in consumers:
        check(c, 0)
    print('check-probe-order:', 'PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)
main()
