"""Consolidation regressions: lazy loading, optional graph fallback and adapter ownership."""
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

    def test_no_extra_guidance_for_plain_edit_or_answer(self):
        with patch.object(m.shutil, 'which', return_value=None):
            prompt, sizes = m.build_prompt('implementer', 'TASK: change button label', self.root, {}, 'MICRO')
            self.assertFalse(any(k.startswith(('workflow:', 'optional:')) for k in sizes))
            self.assertNotIn('Graphify', prompt)
            self.assertEqual(m.build_prompt('answer', 'task', self.root, {})[1], {'task': 1})

    def test_selected_task_role_only_and_bounded(self):
        with patch.object(m.shutil, 'which', return_value=None):
            _, sizes = m.build_prompt('implementer', 'TASK: repair failing tests after dependency migration with slow performance', self.root, {}, 'MEDIUM')
            self.assertEqual(sum(k.startswith('workflow:') for k in sizes), 2)
            self.assertIn('workflow:debugging', sizes)
            self.assertNotIn('workflow:security', sizes)
            _, plain = m.build_prompt('implementer', 'TASK: change label\nEVIDENCE: security error dependency', self.root, {}, 'MICRO')
            self.assertFalse(any(k.startswith('workflow:') for k in plain))
            _, review = m.build_prompt('reviewer', 'TASK: review authorization changes', self.root, {}, 'SMALL')
            self.assertIn('workflow:security', review)

    def test_graph_gates_and_fallback(self):
        p = self.root/'graphify-out/graph.json'
        p.parent.mkdir()
        with patch.object(m.shutil, 'which', return_value='/fake/graphify'), patch.dict(os.environ, {'AI_TEAM_GRAPHIFY':'on'}):
            for body in ['{broken', '{}', '{"nodes": [], "links": []}', '{"nodes":[{}],"links":[]}']:
                p.write_text(body)
                self.assertFalse(m.graph_ready(self.root))
                self.assertFalse(m.capability_guidance('explorer', 'explain code', self.root))
            p.write_text(json.dumps({'nodes':[{'id':'a'}], 'links':[]}))
            self.assertTrue(m.graph_ready(self.root))
            self.assertEqual([x[0] for x in m.capability_guidance('explorer','explain code',self.root)], ['optional:graphify'])
            self.assertFalse(m.capability_guidance('implementer','change label',self.root))
            with patch.dict(os.environ, {'AI_TEAM_GRAPHIFY':'off'}):
                self.assertFalse(m.graph_ready(self.root))
        with patch.object(m.shutil, 'which', return_value=None):
            self.assertFalse(m.graph_ready(self.root))

    def test_guidance_dropped_before_budgeted_essential_context(self):
        with patch.object(m.shutil,'which',return_value=None):
            _, sizes=m.build_prompt('reviewer','TASK: review security changes',self.root,{},'SMALL',extra='x'*40000)
            self.assertNotIn('workflow:security',sizes)
            self.assertLessEqual(sum(v for k,v in sizes.items() if not k.startswith('_')),2060)

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
        self.assertEqual(len(m.CAPABILITIES['capabilities']),15)
        self.assertEqual(sum(c['runtime_type']=='optional skill' for c in m.CAPABILITIES['capabilities']),1)
        for c in m.CAPABILITIES['capabilities']:
            for source in c['sources']:
                p=KIT/source
                self.assertTrue(p.is_file(),source)
                self.assertNotIn('plugins',p.parts)
                self.assertNotIn('backup-2026-09-29',p.parts)
        with self.assertRaises(ValueError):m.role_instructions('../POLICY','',self.root)
