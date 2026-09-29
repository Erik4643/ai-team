# Handoff protocol (for interactive leads; ai-team builds these itself)
Agents never see each other's conversations. Input is a handoff with only non-empty sections:
TASK · RESULT · CONFIDENCE · FILES (paths, agent opens them) · DOCS (paths) · EVIDENCE · DECISION · RISKS · OPEN QUESTIONS · NEXT ACTION · CONSTRAINTS
Output is one JSON object whose schema is given per role in the prompt (explorer: status, confidence, relevant_files,
relevant_symbols, root_cause_candidate, affected_subsystems, architecture_decision_required, external_research_required,
risk, unresolved_questions, recommended_next_stage). Large artifacts stay in `.ai/state/tasks/<id>/`; pass paths.
No chain-of-thought, no source dumps, no repeated project docs.
