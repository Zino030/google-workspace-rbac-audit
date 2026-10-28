# Google Workspace RBAC Audit

Read-only audit of Google Workspace permissions. Collects shared mailbox delegations, Google group memberships and shared drive permissions and exports them as a role matrix (XLSX and CSV).

All API calls are GET requests only; `workspace_audit/auth.py` blocks any other HTTP method at runtime.

## Setup

```bash
pip install -r requirements.txt
```

Place a service account key with domain-wide delegation as `service-account.json` (it is git-ignored).

## Usage

```bash
python -m workspace_audit --admin admin@example.com
python -m workspace_audit --admin admin@example.com --only mailboxes,groups
python -m workspace_audit --admin admin@example.com --dry-run
```

Output is written to `./output` (override with `--out`).

## Layout

- `workspace_audit/discover.py` - discovers domains, users, groups, shared drives
- `workspace_audit/extractors/` - mailboxes, groups, shared drives (chat_spaces is a skeleton)
- `workspace_audit/core/` - role normalisation and matrix building
- `workspace_audit/export/` - XLSX and CSV writers
