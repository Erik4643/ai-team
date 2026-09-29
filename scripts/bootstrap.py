#!/usr/bin/env python3
"""Safe, idempotent project bootstrap. Reads instructions/manifests, never application source."""
import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

KIT=Path(__file__).resolve().parents[1]
POINTER='# Project instructions\n\nRead `.ai/CONTEXT.md` at the repository root. Commands: `.ai/repo-map.json`.\n'
CLAUDE_POINTER='@.ai/CONTEXT.md\n'
CONTEXT_LIMIT=6000
AI_DIRS=('.codex','.claude','.agents','agents','skills','commands','prompts','.cursor','.junie')


def engine():
    loader=importlib.machinery.SourceFileLoader('ai_team_bootstrap',str(KIT/'bin/ai-team'))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    m=importlib.util.module_from_spec(spec);loader.exec_module(m)
    return m


def _read(path,root):
    if path.is_symlink() and not path.resolve().is_relative_to(root):return ''
    return path.read_text(errors='replace') if path.is_file() else ''


def _write(path,text):
    if not path.is_symlink() and path.is_file() and path.read_text()==text:return False
    if path.is_symlink():path.unlink()
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text);return True


def _backup(root,paths):
    paths=sorted(set(p for p in paths if p.exists() or p.is_symlink()))
    if not paths:return
    h=hashlib.sha256()
    for p in paths:
        h.update(str(p.relative_to(root)).encode())
        h.update(os.readlink(p).encode() if p.is_symlink() else p.read_bytes())
    dest=root/'.ai/migration-backup'/('migration-'+h.hexdigest()[:16])
    if not dest.exists():
        dest.mkdir(parents=True,mode=0o700)
        for p in paths:
            target=dest/p.relative_to(root);target.parent.mkdir(parents=True,exist_ok=True)
            if p.is_symlink():target.symlink_to(os.readlink(p))
            else:shutil.copy2(p,target)
    for stale in sorted(dest.parent.glob('migration-*'),key=lambda p:p.stat().st_mtime_ns,reverse=True)[3:]:
        if stale.is_dir() and not stale.is_symlink():shutil.rmtree(stale)


def _ai_files(root):
    files=[]
    for name in AI_DIRS:
        folder=root/name
        if not folder.is_dir() or folder.is_symlink():continue
        for current,dirs,names in os.walk(folder,followlinks=False):
            dirs[:]=[d for d in dirs if d not in {'.git','node_modules','__pycache__'} and not (Path(current)/d).is_symlink()]
            files.extend(Path(current)/name for name in names)
    return files


def _safe_json(text):
    try:
        value=json.loads(text);return value if isinstance(value,dict) else {}
    except ValueError:return {}


def bootstrap(root,clean=False):
    root=Path(root).expanduser().resolve()
    if not root.is_dir():raise ValueError('Project directory does not exist')
    if root==KIT:raise ValueError('The global kit is not an application project; no project context is needed here')
    # Generated directories/files must not redirect writes outside project ownership.
    for rel in ['.ai','.ai/state','.ai/references','.ai/migration-backup','.ai/CONTEXT.md','.ai/repo-map.json','.ai/decisions.md','.ai/.gitignore','.ai/state/init.json']:
        if (root/rel).is_symlink():raise ValueError('Preserve/resolve project symlink before migration: '+rel)
    m=engine();detected=m.detect_map(root)
    def scrub(value):
        if isinstance(value,str):return m.redact(value)
        if isinstance(value,dict):return {k:scrub(v) for k,v in value.items()}
        if isinstance(value,list):return [scrub(v) for v in value]
        return value
    detected=scrub(detected)
    ai=root/'.ai';context=ai/'CONTEXT.md'
    original=_read(context,root)
    context_text=m.redact(original)
    if not context_text:
        context_text=('# Project context\n\n## Project facts\n'
                      '- Stack: '+', '.join(detected['stacks'])+'.\n'
                      '- Package/build manager: '+str(detected.get('package_manager') or 'unknown')+'.\n'
                      '- Commands and layout: `.ai/repo-map.json`; durable decisions: `.ai/decisions.md`.\n\n'
                      '## Rules\n- Preserve existing conventions and make the smallest scoped change.\n'
                      '- Never print secrets or claim checks passed unless they ran.\n')
    if '## Project facts' not in context_text:context_text+='\n## Project facts\n- See project notes above and `.ai/repo-map.json`.\n'
    if '## Rules' not in context_text:context_text+='\n## Rules\n- Follow the project constraints above.\n'
    inputs=[root/'AGENTS.md',root/'CLAUDE.md']
    if (root/'.claude/CLAUDE.md').is_file() and not (root/'.claude').is_symlink():inputs.append(root/'.claude/CLAUDE.md')
    refs={};changes=[];replace=[]
    for p in inputs:
        old=_read(p,root)
        desired=CLAUDE_POINTER if p==root/'CLAUDE.md' else POINTER
        already=old in ('',POINTER,CLAUDE_POINTER) or (p.is_symlink() and p.resolve()==context)
        if not already:
            # Keep full redacted instructions locally; summarize only short prose, not code dumps.
            rel='.ai/references/migrated-'+str(p.relative_to(root)).replace('/','-').replace('.claude','claude')
            refs[root/rel]=m.redact(old)
            facts=[];fence=False
            for line in m.redact(old).splitlines():
                if line.strip().startswith('```'):fence=not fence;continue
                value=line.strip().lstrip('-* ')
                global_directive=any(word in value.lower() for word in ('ai-team','/team','subagent','orchestrat'))
                if not fence and not global_directive and value and not value.startswith(('#','@')) and len(value)<250 and value not in context_text:
                    facts.append('- '+value)
            addition='\n## Migrated project instructions\n'+'\n'.join(facts[:12])+'\n- Full preserved instructions: `'+rel+'`.\n'
            if rel not in context_text:context_text+=addition
        if old!=desired or p.is_symlink():replace.append(p)
    if len(context_text)>CONTEXT_LIMIT:
        rel='.ai/references/migrated-context.md';refs[root/rel]=context_text
        context_text=context_text[:CONTEXT_LIMIT-180].rsplit('\n',1)[0]+'\n\n## Rules\n- Additional preserved project instructions: `'+rel+'`.\n'
    signatures=set(json.loads((KIT/'scripts/migration-signatures.json').read_text())['sha256'])
    signatures.update(hashlib.sha256((KIT/'roles'/name).read_bytes()).hexdigest() for name in ['explorer.md','architect.md','implementer.md','reviewer.md','orchestrator.md'])
    signatures.update(hashlib.sha256(s['legacy'].encode()).hexdigest() for s in m.adapter_specs())
    existing=_ai_files(root)
    redundant=[p for p in existing if not p.is_symlink() and p.is_file() and p.suffix.lower() in {'.md','.mdc'}
               and p.name not in {'settings.md','CLAUDE.md'} and p.stat().st_size<512000
               and hashlib.sha256(p.read_bytes()).hexdigest() in signatures]
    replace.extend(redundant)
    if original and original!=context_text:replace.append(context)
    for p,content in refs.items():
        if p.is_symlink():raise ValueError('Refusing to replace symlinked migration reference: '+str(p.relative_to(root)))
        if p.exists() and p.read_text()!=content:replace.append(p)
    # Strong mode snapshots provider config before replacement, but never deletes unknown content.
    if clean and replace:replace.extend(existing)
    _backup(root,replace)
    for p,text in refs.items():
        if _write(p,text):changes.append(p)
    if _write(context,context_text):changes.append(context)
    for p in inputs:
        if _write(p,CLAUDE_POINTER if p==root/'CLAUDE.md' else POINTER):changes.append(p)
    previous=_safe_json(_read(ai/'state/init.json',root)).get('detected',{})
    old_map=_safe_json(_read(ai/'repo-map.json',root));repo_map={**detected,**{k:v for k,v in old_map.items() if k not in detected}}
    for k in ['commands','boundaries','optional_capabilities']:
        value=old_map.get(k)
        if value is not None and value!=previous.get(k):
            if isinstance(value,dict):
                prior=previous.get(k,{})
                custom={name:val for name,val in value.items() if name not in prior or val!=prior[name]}
                repo_map[k]={**detected.get(k,{}),**custom}
            else:
                repo_map[k]=value
    generated={ai/'repo-map.json':json.dumps(scrub(repo_map),indent=2)+'\n',ai/'state/init.json':json.dumps({'detected':detected,'providers':['codex','claude']},sort_keys=True,indent=2)+'\n'}
    if not (ai/'decisions.md').exists():generated[ai/'decisions.md']='# Decisions\n\nRecord durable project decisions here.\n'
    lines=_read(ai/'.gitignore',root).splitlines()
    for line in ['migration-backup/','state/']:
        if line not in lines:lines.append(line)
    generated[ai/'.gitignore']='\n'.join(lines)+'\n'
    for p,text in generated.items():
        if _write(p,text):changes.append(p)
    for p in redundant:
        p.unlink();changes.append(p)
    # Syntax/ownership checks, no tool installation or application commands.
    json.loads((ai/'repo-map.json').read_text())
    assert (ai/'CONTEXT.md').is_file() and (root/'AGENTS.md').is_file() and (root/'CLAUDE.md').is_file()
    return changes


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--root',type=Path,default=Path.cwd());ap.add_argument('--clean',action='store_true')
    a=ap.parse_args()
    git=subprocess.run(['git','-C',str(a.root),'rev-parse','--show-toplevel'],capture_output=True,text=True)
    root=Path(git.stdout.strip()) if git.returncode==0 else a.root
    try:changed=bootstrap(root,a.clean)
    except (OSError,ValueError) as e:ap.exit(1,'ai-team init: '+str(e)+'\n')
    print('Initialized project: '+str(root) if changed else 'Already initialized; context preserved, adapters valid, nothing destructive required.')
    print('Changed '+str(len(changed))+' AI files; project configuration preserved; Codex + Claude ready.')
    return 0

if __name__=='__main__':sys.exit(main())
