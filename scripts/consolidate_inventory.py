#!/usr/bin/env python3
"""Render sanitized canonical capability documentation; never scan provider homes."""
import json
from pathlib import Path

KIT=Path(__file__).resolve().parents[1]

def main():
    manifest=json.loads((KIT/'capabilities.json').read_text())
    entries=manifest['capabilities']
    assert 1<=len(entries)<=15
    sources=sorted({s for c in entries for s in c['sources']})
    for source in sources:assert (KIT/source).is_file(),source
    inventory={'schema_version':2,'scope':'global reusable runtime only',
               'discovery_summary':{'skill_definitions':4661,'distinct_definition_hashes':1642,'additional_role_command_definitions':15,
                                    'note':'Historical discovery included vendor caches, duplicates and archives. Private detailed evidence was archived outside the repository; discovered definitions are not installed capabilities.'},
               'canonical_runtime':{'manifest':'capabilities.json','capability_count':len(entries),'provider_adapter_count':6,
                                    'providers':['codex','claude'],'optional_capability_count':1,'entries':entries},
               'excluded_runtime_categories':['vendor','archive','cache','duplicate','project facts'],
               'sources':[{'source':s,'classification':'canonical','runtime_enabled':True} for s in sources]}
    docs=KIT/'docs';docs.mkdir(exist_ok=True)
    (docs/'skill-inventory.json').write_text(json.dumps(inventory,indent=2)+'\n')
    lines=['# Canonical capabilities','',f'ai-team is a router: it classifies a task, picks the cheapest capable Codex or Claude Code model and hands the original task to that native CLI. The runtime owns {len(entries)} global capabilities and six thin Codex/Claude entrypoints. One policy (`POLICY.md`), one routing configuration (`routing.json`), and one capability map (`capabilities.json`) are authoritative.','','| Name | Runtime type | Scope | Providers | Load | Status | Reason |','|---|---|---|---|---|---|---|']
    for c in entries:
        lines.append('| '+' | '.join(str(c[k]).replace('|','\\|') for k in ['name','runtime_type','scope','providers','load_strategy','status','reason'])+' |')
    lines+=['','`shared` means Codex and Claude. Routed runs send the original task plus a few router lines; role files serve only the interactive entrypoints. Deterministic controls add no prompt text, and ai-team carries no tool-, IDE- or service-specific solving logic: the agents investigate with their own tools.','','Graphify is optional: one prompt line (and guidance for the interactive explorer/architect) only with an installed CLI and a valid local graph; otherwise the agent searches files itself. ai-team never installs, rebuilds or uploads graphs. Project graphs and facts stay in the project.','','Provider adapters load canonical instructions through `ai-team instructions ROLE`. The six entrypoints are four Claude roles, Claude `/team`, and Codex `team`. No third-provider configuration is generated. Vendor catalogs, caches, historical inventories and backups are excluded from runtime and installation.','','The historical audit discovered 4,661 definitions, not 4,661 runtime skills. The published inventory contains only aggregate audit evidence and canonical runtime membership. Existing role loading was already lazy, so no permanent-token reduction is claimed. Optional graph guidance is about 1 KiB rather than a full vendor pipeline.']
    (docs/'CANONICAL_CAPABILITIES.md').write_text('\n'.join(lines)+'\n')
    (docs/'SKILL_INVENTORY.md').write_text('# Skill audit summary\n\nHistorical discovery counted 4,661 skill definitions (1,642 distinct definition hashes) and 15 role/command definitions. It included vendor caches, synced packages, duplicate copies, temporary catalogs and archives. These are **not installed runtime skills**.\n\nThe private detailed inventory and migration evidence have been archived outside this Git repository. Published runtime membership is defined by `capabilities.json` and `skill-inventory.json`: '+str(len(entries))+' capabilities, six thin provider entrypoints, one optional Graphify integration. Vendor, cache, archive, duplicate and project-data categories are excluded.\n\nRegenerate sanitized documentation with `python3 scripts/consolidate_inventory.py`. This command never scans provider homes or project repositories.\n')
    print(f'Rendered sanitized inventory and {len(entries)}-capability summary; no ecosystem scan.')

if __name__=='__main__':main()
