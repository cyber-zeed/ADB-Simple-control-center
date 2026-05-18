# Versioning Guide

ADB Control Center uses semantic-style versioning:

```text
MAJOR.MINOR.PATCH
```

## Rules

- `PATCH`: bug fixes only, no intended behavior-breaking changes.
- `MINOR`: new features, UI improvements, or workflow additions.
- `MAJOR`: stable milestone or breaking changes.

## Release checklist

1. Update `APP_VERSION`, `APP_RELEASE_DATE`, and `__version__` in `ADB_Control_Center.py`.
2. Update `VERSION`.
3. Update `CHANGELOG.md`.
4. Create or update `RELEASE_NOTES.md`.
5. Run validation:
   - Python compile check
   - AST parse
   - import smoke test
   - targeted parser/helper tests
   - package integrity check
6. Build the GitHub zip package.
7. Tag the release in GitHub.

## Current stability

v0.6.x is considered a pre-1.0 development series. The feature set is broad, but a real Windows + Android stress test is still recommended before v1.0.0.
