# Skill audit summary

Historical discovery counted 4,661 skill definitions (1,642 distinct definition hashes) and 15 role/command definitions. It included vendor caches, synced packages, duplicate copies, temporary catalogs and archives. These are **not installed runtime skills**.

The private detailed inventory and migration evidence have been archived outside this Git repository. Published runtime membership is defined by `capabilities.json` and `skill-inventory.json`: 15 capabilities, six thin provider entrypoints, one optional Graphify integration. Vendor, cache, archive, duplicate and project-data categories are excluded.

Regenerate sanitized documentation with `python3 scripts/consolidate_inventory.py`. This command never scans provider homes or project repositories.
