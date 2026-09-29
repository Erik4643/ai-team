#!/usr/bin/env python3
"""Scan tracked/staged content and optionally all local history; report paths, never matches."""
import argparse
from pathlib import Path
import re
import subprocess
import sys

KIT=Path(__file__).resolve().parents[1]
PATTERNS=[re.compile(x) for x in [
    r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    r'\bAKIA[0-9A-Z]{16}\b',
    r'\bgh[pousr]_[A-Za-z0-9]{30,}\b',
    r'\bsk-(?:ant-)?[A-Za-z0-9_-]{24,}\b',
    r'\bxox[baprs]-[A-Za-z0-9-]{20,}\b',
    r'\bAIza[0-9A-Za-z_-]{30,}\b',
    r'\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b',
    r'https?://[^\s/@:]+:[^\s/@]+@',
]]
FORBIDDEN_PARTS={'state','runtime','logs','backups','archives','.codex','.claude','.gemini','.ai','__pycache__'}

def git(*args):
    r=subprocess.run(['git','-C',str(KIT),*args],capture_output=True)
    if r.returncode:raise RuntimeError('Git scan command failed: '+args[0])
    return r.stdout

def inspect(name,data):
    parts=Path(name).parts
    if any(p in FORBIDDEN_PARTS or p.startswith('backup-') for p in parts):return 'private/runtime path'
    if Path(name).name.startswith('.env') or Path(name).name in {'auth.json','credentials.json'}:return 'credential filename'
    if b'\0' in data:return 'unexpected binary in source package'
    text=data.decode('utf-8',errors='replace')
    if any(p.search(text) for p in PATTERNS):return 'credential-like value'
    # Machine-specific personal checkout paths cannot be portable source.
    if re.search(r'/(?:Users|home)/[A-Za-z0-9_.-]+/(?:Desktop|Documents|\.codex|\.claude|\.ai-kit)/',text):return 'machine-specific user path'
    return None

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--history',action='store_true');a=ap.parse_args()
    problems=[];seen=set();count=0
    for name in git('ls-files','-z').decode().split('\0'):
        if not name:continue
        data=git('show',':'+name);reason=inspect(name,data);count+=1
        if reason:problems.append((name,reason))
    if a.history:
        for commit in git('rev-list','--all').decode().splitlines():
            for line in git('ls-tree','-r',commit).decode().splitlines():
                meta,name=line.split('\t',1);blob=meta.split()[2]
                if (name,blob) in seen:continue
                seen.add((name,blob));reason=inspect(name,git('cat-file','blob',blob))
                if reason:problems.append((commit[:8]+':'+name,reason))
    for name,reason in problems:print('FAIL',name,reason)
    print(('PASS' if not problems else 'FAIL')+f': {count} staged/tracked files; {len(seen)} unique historical blobs; {len(problems)} findings.')
    return bool(problems)

if __name__=='__main__':sys.exit(main())
