#!/usr/bin/env python3
"""Portable installation, health checks and explicit fast-forward updates. Stdlib only."""
import argparse
import datetime
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

KIT = Path(__file__).resolve().parents[1]
BEGIN, END = '<!-- ai-team:begin -->', '<!-- ai-team:end -->'
GLOBAL_TEXT = ('Project facts and rules: .ai/CONTEXT.md when present; commands: .ai/repo-map.json.\n'
               'Initialize a project with `ai-team init`. For requested orchestration use `/team` or `ai-team`;\n'
               'load canonical guidance only for the selected task. Keep reads and command output bounded.\n')


def call(args, cwd=None, capture=False):
    return subprocess.run(args, cwd=cwd, text=True, capture_output=capture,
                          env={**os.environ, 'AI_KIT':str(KIT)})


def engine():
    loader=importlib.machinery.SourceFileLoader('ai_team_management',str(KIT/'bin/ai-team'))
    spec=importlib.util.spec_from_loader(loader.name,loader)
    m=importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def fingerprint():
    paths=[KIT/'routing.json',KIT/'capabilities.json',KIT/'VERSION',KIT/'install.sh']
    for folder in ['bin','scripts','roles','workflows','skills','template','tests']:
        paths.extend(p for p in (KIT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    h=hashlib.sha256()
    for p in sorted(paths):
        h.update(str(p.relative_to(KIT)).encode());h.update(p.read_bytes())
    return h.hexdigest()


class Backup:
    def __init__(self):self.directory=None
    def save(self,p):
        if not p.exists() and not p.is_symlink():return
        if self.directory is None:
            parent=Path.home()/'.local/state/ai-team/backups'
            parent.mkdir(parents=True,exist_ok=True)
            self.directory=parent/('global-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            self.directory.mkdir(mode=0o700)
        dest=self.directory/p.relative_to(Path.home())
        dest.parent.mkdir(parents=True,exist_ok=True)
        if p.is_symlink():dest.symlink_to(os.readlink(p))
        elif p.is_dir():shutil.copytree(p,dest,symlinks=True)
        else:shutil.copy2(p,dest)
    def finish(self):
        if self.directory:
            for old in sorted(self.directory.parent.glob('global-*'))[:-3]:
                if old.is_dir() and not old.is_symlink():shutil.rmtree(old)
            print('Migration backup:',self.directory)


def write_changed(p,body,backup):
    if p.is_symlink():
        print('Preserved user symlink:',p);return False
    if p.exists() and p.read_text()==body:return False
    backup.save(p)
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(body)
    return True


def global_adapter(existing):
    # Replace only our managed block or recognizable legacy ai-team lines.
    text=re.sub(re.escape(BEGIN)+r'[\s\S]*?'+re.escape(END)+r'\n?', '', existing)
    lines=[]
    for line in text.splitlines():
        known=(line=='# Global (all repos)' or line.startswith('Project rules: the repo\'s ')
               or line.startswith('Multi-step or multi-agent work: `ai-team ')
               or line.startswith('Trivial single-file work: do it directly. Anything bigger or multi-agent:')
               or line.startswith('Cap unknown command output (`| head -c 4000`)'))
        if not known:lines.append(line)
    preserved='\n'.join(lines).strip()
    block=BEGIN+'\n'+GLOBAL_TEXT+END+'\n'
    return (preserved+'\n\n' if preserved else '')+block


def install(skip_checks=False):
    if sys.version_info<(3,9):raise SystemExit('Python 3.9+ is required')
    backup=Backup();home=Path.home();bindir=home/'.local/bin';bindir.mkdir(parents=True,exist_ok=True)
    for name in ['ai-team','ai-init']:
        p=bindir/name;target=KIT/'bin'/name
        if p.is_symlink() and p.resolve()==target.resolve():continue
        if p.exists() or p.is_symlink():
            if p.is_dir() and not p.is_symlink():raise SystemExit('Refusing to replace directory: '+str(p))
            backup.save(p);p.unlink()
        p.symlink_to(target)
    for folder,name in [('.codex','AGENTS.md'),('.claude','CLAUDE.md')]:
        p=home/folder/name
        write_changed(p,global_adapter(p.read_text() if p.exists() else ''),backup)
    m=engine();ownedfile=KIT/'state/installed-adapters.json'
    try:owned=json.loads(ownedfile.read_text())
    except (OSError,ValueError):owned={}
    for spec in m.adapter_specs():
        p=home/spec['path'];old=p.read_text() if p.is_file() else ''
        digest=hashlib.sha256(old.encode()).hexdigest()
        # Previous generated thin adapters differ only in provider metadata.
        thin=('Load `ai-team instructions '+spec['role']+'`')
        body=old.split('---',2)[-1].strip() if old.startswith('---\n') else ''
        previous_thin=(body.startswith(thin) and body.count('\n')==0) or (spec['role']=='orchestrator' and body.startswith('Task: $ARGUMENTS\n\n'+thin) and body.count('\n')==2)
        is_owned=old in ('',spec['body'],spec['legacy']) or owned.get(spec['path'])==digest or previous_thin
        if not is_owned or p.is_symlink():
            print('Preserved custom provider adapter:',p);continue
        write_changed(p,spec['body'],backup)
        owned[spec['path']]=hashlib.sha256(spec['body'].encode()).hexdigest()
    ownedfile.parent.mkdir(parents=True,exist_ok=True)
    new=json.dumps(owned,indent=2)+'\n'
    if not ownedfile.exists() or ownedfile.read_text()!=new:ownedfile.write_text(new)
    backup.finish()
    if str(bindir) not in os.environ.get('PATH','').split(os.pathsep):
        print('PATH setup required: add export PATH="$HOME/.local/bin:$PATH" to your shell profile.')
    print('Installed ai-team', (KIT/'VERSION').read_text().strip(), 'for Codex and Claude. No provider CLI/auth installed.')
    if skip_checks:return 0
    rc=call([sys.executable,str(KIT/'bin/ai-team'),'--self-test'],cwd=KIT).returncode
    return rc or doctor(KIT)


def doctor(project):
    errors=[]
    def check(name,ok,detail='',warning=False,hint=''):
        # detail is always informative; hint is remediation and only shown when the check is not OK
        print(('OK   ' if ok else 'WARN ' if warning else 'FAIL ')+name+''.join(': '+x for x in (detail,'' if ok else hint) if x))
        if not ok and not warning:errors.append(name)
    print('GLOBAL KIT HEALTH (T0; no model calls)')
    check('kit root',KIT.is_dir(),str(KIT))
    check('version',(KIT/'VERSION').is_file(),(KIT/'VERSION').read_text().strip() if (KIT/'VERSION').exists() else '')
    for name in ['ai-team','ai-init']:
        p=Path.home()/'.local/bin'/name
        check(name+' link',p.is_symlink() and p.resolve()==(KIT/'bin'/name).resolve())
        check(name+' executable',os.access(KIT/'bin'/name,os.X_OK))
    check('PATH',str(Path.home()/'.local/bin') in os.environ.get('PATH','').split(os.pathsep),warning=True,hint='add $HOME/.local/bin to PATH')
    try:
        m=engine();check('active providers',set(m.CFG['providers'])=={'codex','claude'})
        for name in m.CFG['providers']:check(name+' CLI',bool(shutil.which(name)),warning=True,hint='install/authenticate separately if needed')
        check('routing',all(str(t) in p['tiers'] for p in m.CFG['providers'].values() for t in (1,2,3)))
        check('canonical capabilities',8<=len(m.CAPABILITIES['capabilities'])<=15)
        for c in m.CAPABILITIES['capabilities']:
            check('source '+c['id'],all((KIT/p).is_file() for p in c['sources']))
        for s in m.adapter_specs():
            p=Path.home()/s['path']
            check('adapter '+s['path'],p.is_file() and p.read_text()==s['body'],warning=p.exists(),hint='custom content is preserved')
    except (OSError,ValueError,KeyError) as e:check('configuration',False,type(e).__name__)
    for folder,name in [('.codex','AGENTS.md'),('.claude','CLAUDE.md')]:
        p=Path.home()/folder/name
        check('global '+name,p.is_file() and BEGIN in p.read_text(),warning=p.is_symlink(),hint='custom symlinks may require a manual pointer')
    git=call(['git','-C',str(KIT),'rev-parse','--show-toplevel'],capture=True)
    if git.returncode==0:
        check('Git root',Path(git.stdout.strip()).resolve()==KIT)
        remote=call(['git','-C',str(KIT),'remote','get-url','origin'],capture=True)
        check('Git remote',remote.returncode==0,remote.stdout.strip() if remote.returncode==0 else 'source archive/local fixture',warning=True)
        status=call(['git','-C',str(KIT),'status','--porcelain'],capture=True)
        check('Git working tree',not status.stdout.strip(),'uncommitted changes' if status.stdout.strip() else 'clean',warning=True)
    else:check('Git metadata',False,'source archive; update command needs a Git clone',warning=True)
    state=KIT/'state';state.mkdir(exist_ok=True)
    check('runtime writable',os.access(state,os.W_OK))
    try:
        health=json.loads((state/'self-test.json').read_text())
        check('self-test health',health.get('ok') is True and health.get('fingerprint')==fingerprint(),hint='rerun ai-team --self-test')
    except (OSError,ValueError):check('self-test health',False,'run ai-team --self-test')
    check('optional Graphify',bool(shutil.which('graphify')),warning=True,hint='not installed; targeted search is the fallback')
    project=Path(project).resolve()
    print('CURRENT PROJECT HEALTH')
    if project==KIT:print('Global kit: application context is not required.')
    elif not (project/'.ai/CONTEXT.md').exists():print('Not initialized; run ai-team init when ready.')
    else:
        for rel in ['.ai/CONTEXT.md','.ai/repo-map.json','.ai/decisions.md','AGENTS.md','CLAUDE.md']:
            check(rel,(project/rel).is_file())
        try:json.loads((project/'.ai/repo-map.json').read_text())
        except (OSError,ValueError):check('project map JSON',False)
        ignored=call(['git','-C',str(project),'check-ignore','-q','.ai/state/probe'],capture=True)
        check('project state ignored',ignored.returncode==0)
    print('Doctor:', 'PASS' if not errors else 'FAIL ('+str(len(errors))+')')
    return bool(errors)


def update():
    root=call(['git','-C',str(KIT),'rev-parse','--show-toplevel'],capture=True)
    if root.returncode or Path(root.stdout.strip()).resolve()!=KIT:
        print('Update requires a Git clone rooted at the kit directory.');return 1
    status=call(['git','-C',str(KIT),'status','--porcelain','--untracked-files=normal'],capture=True)
    if status.returncode or status.stdout.strip():
        print('Update refused: kit working tree is dirty. Commit or stash changes first.');return 1
    branch=call(['git','-C',str(KIT),'symbolic-ref','--short','HEAD'],capture=True)
    if branch.returncode:
        print('Update refused: detached HEAD. Check out the tracked default branch.');return 1
    upstream=call(['git','-C',str(KIT),'rev-parse','--abbrev-ref','@{upstream}'],capture=True)
    if upstream.returncode:
        print('Update refused: current branch has no upstream.');return 1
    head=lambda:call(['git','-C',str(KIT),'rev-parse','--short','HEAD'],capture=True).stdout.strip()
    old=head()
    if call(['git','-C',str(KIT),'fetch','--prune']).returncode:return 1
    if call(['git','-C',str(KIT),'merge','--ff-only','@{upstream}']).returncode:return 1
    # Execute the refreshed installer, not this process's old code.
    rc=call([str(KIT/'install.sh')],cwd=KIT).returncode
    if rc==0 and head()!=old:
        print('Global kit updated '+old+' -> '+head()+'. Refresh each project: cd <project> && ai-team init (idempotent; project facts preserved).')
    return rc


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('command',choices=['install','doctor','update','health-write'])
    ap.add_argument('--skip-checks',action='store_true',help='test harness only; production install validates by default')
    ap.add_argument('--project',default=str(KIT));ap.add_argument('--result',choices=['pass','fail'])
    a=ap.parse_args()
    if a.command=='install':return install(a.skip_checks)
    if a.command=='doctor':return doctor(a.project)
    if a.command=='update':return update()
    state=KIT/'state';state.mkdir(exist_ok=True)
    (state/'self-test.json').write_text(json.dumps({'ok':a.result=='pass','fingerprint':fingerprint()})+'\n')
    return 0

if __name__=='__main__':sys.exit(main())
