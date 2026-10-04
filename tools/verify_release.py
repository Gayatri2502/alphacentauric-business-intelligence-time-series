#!/usr/bin/env python3
"""Verify release-file hashes; extra runtime/environment files are ignored."""
import hashlib
from pathlib import Path
import sys
root=Path(__file__).resolve().parents[1]
manifest=root/'MANIFEST.sha256'
if not manifest.is_file():raise SystemExit('MANIFEST.sha256 is missing.')
failures=[];count=0
for line in manifest.read_text().splitlines():
    if not line.strip():continue
    expected,name=line.split('  ',1);path=(root/name).resolve()
    if not path.is_relative_to(root) or not path.is_file():failures.append(name);continue
    actual=hashlib.sha256(path.read_bytes()).hexdigest()
    if actual!=expected:failures.append(name)
    count+=1
if failures:
    print('Missing or changed files:',*failures,sep='\n');sys.exit(1)
print(f'Verified {count} release files. Generated extra files were not checked.')
