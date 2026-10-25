#!/usr/bin/env python3
"""Emit only deduplicated SELinux type/class/permission vectors from stdin.
No names, paths, object values, MLS categories, process or network identifiers.
Run by the coordinator on private captures; raw lines never leave that owner.
"""
import collections
import json
import re
import sys

vectors = collections.Counter()
for line in sys.stdin:
    if not re.search(r'\bavc:\s+denied\b', line):
        continue
    perms = re.search(r'\{([a-z_ ]+)\}', line)
    source = re.search(r'\bscontext=u:r:([a-zA-Z0-9_]+):', line)
    target = re.search(r'\btcontext=u:(?:r|object_r):([a-zA-Z0-9_]+):', line)
    cls = re.search(r'\btclass=([a-zA-Z0-9_]+)\b', line)
    if all((perms, source, target, cls)):
        vectors[(source[1], target[1], cls[1], tuple(sorted(set(perms[1].split()))))] += 1
json.dump([dict(source=s, target=t, tclass=c, permissions=list(p), count=n)
           for (s, t, c, p), n in sorted(vectors.items())], sys.stdout, indent=2)
sys.stdout.write('\n')
