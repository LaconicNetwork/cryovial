# ansible/

Infrastructure automation. Ansible playbooks and roles for deploying
exophial infrastructure. See `docs/COORD_CONTAINER.md` for the
coordination container specification.

All Ansible files must pass `ansible-lint` at the `production` profile.
See CLAUDE.md (project root) § Infrastructure Automation for rules.

- roles/cryovial/ — Cryovial coordination container role
- roles/prerequisites/ — System prerequisites role
- inventory/ — Inventory files
