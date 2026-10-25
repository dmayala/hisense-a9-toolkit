#!/usr/bin/env python3
"""FP6 covers the stock TZ APP_START validation extent with owned shared memory."""
from pathlib import Path
import importlib.util

HERE=Path(__file__).resolve().parent
OUT=Path('/home/user/A9-build/mainline/integ-20261023-fp6')


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def main():
    k=load('fp6_kernel',HERE/'build-fp4-kernel.py')
    k.OUT=OUT;k.K=OUT/'kernel-source';k.OBJ=OUT/'obj'
    k.TARGET_CONFIG=(k.BASE/'kernel.config').read_bytes().replace(b'CONFIG_QCOM_TZMEM_MODE_GENERIC=y\n# CONFIG_QCOM_TZMEM_MODE_SHMBRIDGE is not set',b'# CONFIG_QCOM_TZMEM_MODE_GENERIC is not set\nCONFIG_QCOM_TZMEM_MODE_SHMBRIDGE=y')
    k.EXTRA_OBJECTS=['drivers/firmware/qcom/qcom_tzmem.o']
    k.ALLOWED_CHANGED_EXPORTS={'devm_qcom_tzmem_pool_new','qcom_tzmem_enable'}
    k.ALLOWED_NEW_EXPORTS={'qcom_tzmem_shm_bridge_create','qcom_tzmem_shm_bridge_delete'}
    if not (OUT/'Image.gz').exists():k.main()
    b=load('fp6_package',HERE/'build-fp4.py');b.OUT=OUT;b.main()
    print('FP6 host image PASS; APP_START staging covers MDT + image',flush=True)


if __name__=='__main__':main()
