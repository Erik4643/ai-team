"""Consolidation regressions: routed prompts stay the original task, optional graph fallback and adapter ownership."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

KIT = Path(os.environ.get('AI_KIT', str(Path(__file__).resolve().parents[1])))
loader = importlib.machinery.SourceFileLoader('capability_runtime', str(KIT/'bin/ai-team'))
spec = importlib.util.spec_from_loader(loader.name, loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)

class CanonicalCapabilities(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_routed_prompts_carry_no_guidance(self):
        with patch.object(m.shutil, 'which', return_value=None):
            for role, write in (('implementer', True), ('explorer', False), ('reviewer', False)):
                prompt = m.task_prompt(role, 'repair failing tests after dependency migration with slow performance', self.root, write)
                self.assertTrue(prompt.startswith('repair failing tests after dependency migration with slow performance\n'))
                self.assertNotIn('code graph navigation', prompt)
                self.assertNotIn('# Role:', prompt)
            self.assertEqual(m.task_prompt('answer', 'task', self.root), 'task')

    def test_graph_gates_and_fallback(self):
        p = self.root/'graphify-out/graph.json'
        p.parent.mkdir()
        with patch.object(m.shutil, 'which', return_value='/fake/graphify'), patch.dict(os.environ, {'AI_TEAM_GRAPHIFY':'on'}):
            for body in ['{broken', '{}', '{"nodes": [], "links": []}', '{"nodes":[{}],"links":[]}']:
                p.write_text(body)
                self.assertFalse(m.graph_ready(self.root))
                self.assertNotIn('graphify', m.task_prompt('explorer', 'explain code', self.root))
                self.assertNotIn('code graph navigation', m.role_instructions('explorer', 'explain code', self.root))
            p.write_text(json.dumps({'nodes':[{'id':'a'}], 'links':[]}))
            self.assertTrue(m.graph_ready(self.root))
            self.assertIn('graphify query', m.task_prompt('explorer', 'explain code', self.root))  # one optional line, not the skill
            self.assertLess(m.tok(m.task_prompt('explorer', 'explain code', self.root)), 120)
            self.assertIn('code graph navigation', m.role_instructions('explorer', 'explain code', self.root))
            self.assertNotIn('code graph navigation', m.role_instructions('implementer', 'change label', self.root))
            with patch.dict(os.environ, {'AI_TEAM_GRAPHIFY':'off'}):
                self.assertFalse(m.graph_ready(self.root))
        with patch.object(m.shutil, 'which', return_value=None):
            self.assertFalse(m.graph_ready(self.root))

    def test_adapters_are_thin_and_custom_content_is_preserved(self):
        specs=m.adapter_specs()
        self.assertEqual(len(specs),6)
        for s in specs:
            self.assertIn('ai-team instructions',s['body'])
            self.assertNotIn('# Role:',s['body'])
        custom=self.root/'.claude/agents/reviewer.md'
        custom.parent.mkdir(parents=True)
        custom.write_text('user-owned custom content')
        legacy=self.root/'.codex/skills/team/SKILL.md'
        legacy.parent.mkdir(parents=True)
        old=next(s for s in specs if s['path']=='.codex/skills/team/SKILL.md')
        legacy.write_text(old['legacy'])
        with patch.object(m.Path,'home',return_value=self.root), patch.object(m.shutil,'which',return_value=None):
            m.cmd_install()
            first=legacy.read_text()
            m.cmd_install()
        self.assertEqual(custom.read_text(),'user-owned custom content')
        self.assertEqual(first,old['body'])
        self.assertEqual(legacy.read_text(),first)
        self.assertFalse((self.root/'.gemini').exists())

    def test_runtime_allowlist_is_owned_and_small(self):
        self.assertEqual(len(m.CAPABILITIES['capabilities']),8)
        self.assertEqual(sum(c['runtime_type']=='optional skill' for c in m.CAPABILITIES['capabilities']),1)
        for c in m.CAPABILITIES['capabilities']:
            for source in c['sources']:
                p=KIT/source
                self.assertTrue(p.is_file(),source)
                self.assertNotIn('plugins',p.parts)
                self.assertNotIn('backup-2026-09-29',p.parts)
        with self.assertRaises(ValueError):m.role_instructions('../POLICY','',self.root)
