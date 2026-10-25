#!/usr/bin/env python3
"""Synthetic band-reader smoke check; no firmware, backups or phone access."""
import ast
import contextlib
import io
import json
from pathlib import Path
import runpy
import struct
import sys
import tempfile
import types

HERE = Path(__file__).resolve().parent
for path in HERE.rglob('*.py'):
    ast.parse(path.read_text(), filename=str(path))

# Only the band bytes below may be read. Any access to the unrelated record's
# payload fails the test. The fake CFB adapter supplies no backup content.
mask = bytes.fromhex('8301680400000200')
lte = bytes.fromhex('95000000e2010000')
items = [(9999, b''), (441, b'\0'+mask[:2]), (946, b'\0'+mask[2:4]),
         (2954, b'\0'+mask[4:]), (1877, mask), (6828, lte)]
class Stream:
    def seek(self, offset):
        self.index, self.offset = divmod(offset, 136)
    def read(self, size):
        nv, payload = items[self.index]
        if self.offset == 0:
            assert size == 8
            self.offset = 8
            return struct.pack('<HHI', 136, 1, nv)
        assert nv != 9999 and size <= len(payload)
        return payload[:size]
class Ole:
    def __init__(self, path, write_mode):
        assert write_mode is False
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def listdir(self): return [['NV_ITEM_ARRAY'], ['EFS_Dir', 'band']]
    def get_size(self, path):
        return 136*len(items) if path[-1]=='NV_ITEM_ARRAY' else 8
    def openstream(self, path):
        if path[-1]=='NV_ITEM_ARRAY': return Stream()
        if 'EFS_Dir' in path:
            return io.BytesIO(b'/nv/item_files/modem/mmode/lte_bandpref\0')
        return io.BytesIO(lte)

saved_argv = sys.argv
saved_ole = sys.modules.get('olefile')
sys.modules['olefile'] = types.SimpleNamespace(OleFileIO=Ole)
try:
    with tempfile.TemporaryDirectory() as temp:
        output = Path(temp)/'bands.json'
        sys.argv = ['qcn-bands.py', 'synthetic.qcn', str(output)]
        with contextlib.redirect_stdout(io.StringIO()):
            runpy.run_path(str(HERE/'qcn-bands.py'), run_name='__main__')
        result = json.loads(output.read_text())
        assert result['decoded']['lte_bands_1_to_43'] == [1,3,5,8,34,38,39,40,41]
        assert result['decoded']['wcdma_bands'] == [1,5,8]
        assert all(record['nv'] != 9999 for record in result['records'])
finally:
    sys.argv = saved_argv
    if saved_ole is None: del sys.modules['olefile']
    else: sys.modules['olefile'] = saved_ole
print('HOST_TEST syntax_and_band_allowlist=PASS no_device_access')
