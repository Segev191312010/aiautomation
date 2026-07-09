# Development Notes

## Workspace Hygiene

The repository source tree must not be used as a downloads folder or release
artifact staging area.

- Do not place unrelated installers, tools, archives, or DLLs in the repo root.
- Release artifacts belong in an external release/output location, not beside
  source files.
- Binary fixtures must be explicitly documented and allowlisted before they are
  added.
- Run `python scripts/check_workspace_hygiene.py` before committing Phase A
  safety or release changes.

