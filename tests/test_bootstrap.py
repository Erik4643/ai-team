import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

KIT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('bootstrap_tests_module',KIT/'scripts/bootstrap.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)

class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ai init spaces ');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'project';self.root.mkdir()
    def put(self,p,text):
        f=self.root/p;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(text);return f
    def snapshot(self):
        return {str(p.relative_to(self.root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file() and '.git' not in p.parts}
    def test_unknown_idempotent_and_no_gemini(self):
        b.bootstrap(self.root);first=self.snapshot();self.assertEqual(b.bootstrap(self.root),[]);self.assertEqual(first,self.snapshot())
        self.assertEqual(json.loads((self.root/'.ai/repo-map.json').read_text())['stacks'],['unknown'])
        self.assertFalse((self.root/'GEMINI.md').exists());self.assertFalse((self.root/'.gemini').exists())
        self.assertTrue((self.root/'.ai/state').is_dir())
    def test_root_instructions_migrated_and_secret_redacted(self):
        self.put('AGENTS.md','# Rules\nBuild with make verify\nAPI_TOKEN=private-value\n')
        self.put('CLAUDE.md','Never edit generated output.\n')
        b.bootstrap(self.root)
        context=(self.root/'.ai/CONTEXT.md').read_text()
        self.assertIn('make verify',context);self.assertIn('Never edit',context);self.assertNotIn('private-value',context)
        self.assertEqual((self.root/'CLAUDE.md').read_text(),b.CLAUDE_POINTER)
        self.assertEqual(len(list((self.root/'.ai/migration-backup').glob('*'))),1)
        for p in (self.root/'.ai/references').glob('*'):self.assertNotIn('private-value',p.read_text())
        before=self.snapshot();b.bootstrap(self.root);self.assertEqual(before,self.snapshot())
    def test_existing_context_sections_and_settings_preserved(self):
        text='# Context\n## Project facts\nKeep fact.\n## Graphify\nLocal graph policy.\n## Boundaries\nNever cross this boundary.\n## Rules\nKeep rule.\n'
        self.put('.ai/CONTEXT.md',text);self.put('.codex/config.toml','model = "custom"\n');self.put('.claude/settings.json','{"theme":"dark"}')
        self.put('.claude/skills/local/SKILL.md','# Project-only skill\nDo special work.\n')
        self.put('.ai/decisions.md','Keep our decision.\n')
        b.bootstrap(self.root,True)
        self.assertEqual((self.root/'.ai/CONTEXT.md').read_text(),text)
        self.assertIn('custom',(self.root/'.codex/config.toml').read_text())
        self.assertTrue((self.root/'.claude/skills/local/SKILL.md').exists())
        self.assertIn('Keep our decision',(self.root/'.ai/decisions.md').read_text())
    def test_clean_exact_duplicates_removed_after_backup(self):
        body=(KIT/'roles/explorer.md').read_text()
        dupe=self.put('.claude/agents/explorer.md',body)
        source=self.put('agents/explorer.py',body)
        unique=self.put('.codex/agents/special.md','Use ai-team, plus unique business constraints.')
        self.put('.claude/settings.json','{"theme":"dark"}')
        b.bootstrap(self.root,True)
        self.assertFalse(dupe.exists());self.assertTrue(source.exists());self.assertTrue(unique.exists())
        backup=next((self.root/'.ai/migration-backup').glob('*'))
        self.assertEqual((backup/'.claude/agents/explorer.md').read_text(),body)
        self.assertTrue((backup/'.claude/settings.json').exists())
        before=self.snapshot();b.bootstrap(self.root,True);self.assertEqual(before,self.snapshot())
    def test_backup_retention(self):
        for i in range(5):self.put('AGENTS.md','Fact number '+str(i));b.bootstrap(self.root)
        self.assertEqual(len(list((self.root/'.ai/migration-backup').glob('*'))),3)
    def test_symlink_adapter_does_not_overwrite_context(self):
        context=self.put('.ai/CONTEXT.md','# Context\n## Project facts\nPreserve me.\n## Rules\nBe careful.\n')
        (self.root/'AGENTS.md').symlink_to('.ai/CONTEXT.md')
        b.bootstrap(self.root)
        self.assertIn('Preserve me',context.read_text());self.assertFalse((self.root/'AGENTS.md').is_symlink())
    def test_external_symlinks_not_written(self):
        outside=Path(self.temp.name)/'outside';outside.write_text('unchanged')
        (self.root/'AGENTS.md').symlink_to(outside)
        b.bootstrap(self.root);self.assertEqual(outside.read_text(),'unchanged')
        (self.root/'.ai/state/init.json').unlink();(self.root/'.ai/state/init.json').symlink_to(outside)
        with self.assertRaises(ValueError):b.bootstrap(self.root)
        self.assertEqual(outside.read_text(),'unchanged')
    def test_detector_never_imports_application_executable(self):
        marker=self.root/'bad-marker'
        self.put('bin/ai-team','raise RuntimeError("application code must not execute")\n')
        b.bootstrap(self.root);self.assertFalse(marker.exists())
    def test_long_instructions_fully_preserved_locally(self):
        self.put('AGENTS.md','\n'.join('Unique fact '+str(i)+' detail '*20 for i in range(200)))
        b.bootstrap(self.root)
        self.assertLessEqual(len((self.root/'.ai/CONTEXT.md').read_text()),b.CONTEXT_LIMIT)
        self.assertIn('Unique fact 199',(self.root/'.ai/references/migrated-AGENTS.md').read_text())
    def test_react_python_and_unknown_detection(self):
        self.put('package.json',json.dumps({'dependencies':{'react':'1'},'scripts':{'test':'runner','lint':'lint-tool'}}))
        self.put('yarn.lock','');self.put('pyproject.toml','[tool.pytest.ini_options]\n');self.put('tests/test_example.py','')
        b.bootstrap(self.root);m=json.loads((self.root/'.ai/repo-map.json').read_text())
        self.assertIn('react',m['stacks']);self.assertIn('python',m['stacks']);self.assertEqual(m['package_manager'],'yarn')
        self.assertEqual(m['commands']['test'],'yarn test')
    def test_handwritten_map_preserved_generated_map_refreshed(self):
        self.put('package.json','{"scripts":{"test":"x"}}');b.bootstrap(self.root)
        p=self.root/'.ai/repo-map.json';m=json.loads(p.read_text());m['boundaries']=['keep'];m['commands']['lint']='custom-lint';p.write_text(json.dumps(m))
        self.put('package.json','{"scripts":{"test:unit":"x"}}');b.bootstrap(self.root);m=json.loads(p.read_text())
        self.assertEqual(m['boundaries'],['keep']);self.assertEqual(m['commands']['lint'],'custom-lint')
        self.assertEqual(m['commands']['test'],'npm run test:unit')
    def test_cli_locates_root_and_dirty_source_untouched(self):
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        source=self.put('src/main.py','print("unchanged")\n');nested=self.root/'src'
        r=subprocess.run([sys.executable,str(KIT/'bin/ai-team'),'init'],cwd=nested,capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr);self.assertTrue((self.root/'AGENTS.md').exists())
        self.assertFalse((nested/'.ai').exists());self.assertEqual(source.read_text(),'print("unchanged")\n')
        r=subprocess.run(['git','-C',str(self.root),'check-ignore','-q','.ai/state/probe'])
        self.assertEqual(r.returncode,0)
    def test_global_kit_rejected_as_application(self):
        with self.assertRaises(ValueError):b.bootstrap(KIT)
    def test_monorepo_and_additional_stacks(self):
        for f in ['packages/a/go.mod','packages/b/Cargo.toml','Package.swift','Gemfile','composer.json','Dockerfile','main.tf','kustomization.yaml','build.gradle.kts','app.csproj']:
            self.put(f,'{}' if f.endswith('.json') else '')
        b.bootstrap(self.root);m=json.loads((self.root/'.ai/repo-map.json').read_text())
        for s in ['swift','ruby','php','docker','terraform','kubernetes','kotlin','dotnet']:self.assertIn(s,m['stacks'])
        self.assertEqual(m['workspaces'],['packages/a','packages/b'])
