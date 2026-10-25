#!/usr/bin/env python3
"""Extract the real msm tail; its phase placement is part of the test."""
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text()
start = source.index('void msm_atomic_commit_tail(')
end = source.index('{', start) + 1
level = 1
while level:
    level += (source[end] == '{') - (source[end] == '}')
    end += 1
Path(sys.argv[2]).write_text(source[start:end] + '\n')
