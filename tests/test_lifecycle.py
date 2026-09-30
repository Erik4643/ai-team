import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

KIT=Path(__file__).resolve().parents[1]
loader=importlib.machinery.SourceFileLoader('lifecycle_engine',str(KIT/'bin/ai-team'))
spec=importlib.util.spec_from_loader(loader.name,loader);m=importlib.util.module_from_spec(spec);loader.exec_module(m)

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='portable home ');self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)/'home';self.home.mkdir()
        self.kit=self.home/'kit with spaces';self.kit.mkdir()
        for name in ['bin','scripts','roles','skills','template','tests']:
            shutil.copytree(KIT/name,self.kit/name,ignore=shutil.ignore_patterns('__pycache__'))
        for name in ['install.sh','VERSION','routing.json','capabilities.json','POLICY.md']:
            shutil.copy2(KIT/name,self.kit/name)
        self.env={**os.environ,'HOME':str(self.home),'AI_KIT':str(self.kit),'PATH':str(self.home/'.local/bin')+os.pathsep+str(Path(sys.executable).parent)+os.pathsep+os.defpath}
    def run_cmd(self,args,ok=True,cwd=None):
        r=subprocess.run(args,env=self.env,cwd=cwd or self.kit,capture_output=True,text=True)
        if ok:self.assertEqual(r.returncode,0,r.stdout[-2000:]+r.stderr[-2000:])
        return r
    def install(self):self.run_cmd([str(self.kit/'install.sh'),'--skip-checks'])
    def snapshot(self):
        return {str(p.relative_to(self.home)):p.read_bytes() for d in ['.codex','.claude'] for p in (self.home/d).rglob('*') if p.is_file()}
    def test_clean_home_install_idempotent_and_alias(self):
        self.install();before=self.snapshot();self.install();self.assertEqual(before,self.snapshot())
        self.assertTrue((self.home/'.local/bin/ai-team').is_symlink());self.assertTrue((self.home/'.local/bin/ai-init').is_symlink())
        self.assertFalse((self.home/'.gemini').exists())
        self.assertEqual(self.run_cmd([str(self.home/'.local/bin/ai-team'),'--version']).stdout.strip(),(self.kit/'VERSION').read_text().strip())
        project=self.home/'project';project.mkdir();self.run_cmd(['git','init','-q',str(project)])
        self.run_cmd([str(self.home/'.local/bin/ai-init')],cwd=project)
        self.assertTrue((project/'.ai/CONTEXT.md').is_file())
    def test_global_personal_settings_preserved_backup_external(self):
        folder=self.home/'.claude';folder.mkdir();(folder/'settings.json').write_text('{"theme":"dark"}')
        (folder/'CLAUDE.md').write_text('Personal unique preference.\n# Global (all repos)\nTrivial single-file work: do it directly. Anything bigger or multi-agent: `/team <task>` (runs `ai-team`; `ai-team --plan` is free).\n')
        self.install()
        text=(folder/'CLAUDE.md').read_text();self.assertIn('Personal unique preference',text);self.assertNotIn('Anything bigger',text)
        self.assertEqual((folder/'settings.json').read_text(),'{"theme":"dark"}')
        backups=list((self.home/'.local/state/ai-team/backups').glob('global-*'));self.assertEqual(len(backups),1)
        self.assertFalse((self.kit/'backups').exists())
    def test_doctor_health_and_global_project_separation(self):
        self.install();self.run_cmd([sys.executable,str(self.kit/'scripts/manage.py'),'health-write','--result','pass'])
        result=self.run_cmd([str(self.home/'.local/bin/ai-team'),'--doctor'])
        self.assertIn('Doctor: PASS',result.stdout);self.assertIn('application context is not required',result.stdout)
        status=self.run_cmd([str(self.home/'.local/bin/ai-team'),'--status']).stdout
        self.assertIn('GLOBAL KIT STATUS',status);self.assertIn('CURRENT PROJECT STATUS',status);self.assertNotIn('gemini',status)
        self.assertFalse((self.kit/'.ai/CONTEXT.md').exists())
    def test_update_refuses_dirty_tree_without_fetch(self):
        self.run_cmd(['git','init','-q',str(self.kit)])
        result=self.run_cmd([sys.executable,str(self.kit/'bin/ai-team'),'update'],ok=False)
        self.assertNotEqual(result.returncode,0);self.assertIn('dirty',result.stdout)
    def test_provider_subsets_cooldown_and_no_gemini(self):
        with patch.object(m,'cooldowns',return_value={}),patch.dict(os.environ,{'AI_TEAM_PROVIDERS':''}):
            for providers in [{'codex'},{'claude'},{'codex','claude'},{'gemini'}]:
                m._AVAIL.clear()
                with patch.object(m.shutil,'which',side_effect=lambda n:'/fake/'+n if n in providers else None):
                    self.assertEqual(set(m.available()),providers&{'codex','claude'})
            m._AVAIL.clear()
            with patch.object(m.shutil,'which',return_value='/fake/cli'),patch.object(m,'cooldowns',return_value={'codex':'2099-01-01'}):
                self.assertEqual(m.available(),['claude'])
    def test_unstructured_or_error_provider_result_not_done(self):
        self.assertTrue(m.parse_status('failure without JSON','implementer')['_unstructured'])  # the router never counts it as confirmed
        def bad(cmd,**kwargs):return subprocess.CompletedProcess(cmd,1,json.dumps({'is_error':True,'result':'service failure'}),'')
        with patch.object(m.subprocess,'run',side_effect=bad):
            with self.assertRaises(m.ProviderError):m.run_provider('claude',1,'x',True,'implementer',self.home,'t')
    def test_shipped_sources_are_portable_and_inventory_is_runtime_only(self):
        for folder in ['bin','scripts','roles','skills','template']:
            for p in (KIT/folder).rglob('*'):
                if p.is_file() and '__pycache__' not in p.parts:
                    text=p.read_text()
                    self.assertNotIn('/Users/',text,str(p))
        self.assertEqual(set(m.CFG['providers']),{'codex','claude'})
        self.assertLessEqual(len(m.CAPABILITIES['capabilities']),15)
        for gone in ['diagnostics','workflows','PROTOCOL.md']:self.assertFalse((KIT/gone).exists(),gone)
        self.assertLess(len((KIT/'roles/orchestrator.md').read_text()),600)
