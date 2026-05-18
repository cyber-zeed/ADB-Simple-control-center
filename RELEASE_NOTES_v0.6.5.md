# Release Notes - ADB Control Center v0.6.5

Release date: 2026-05-18

## Type

Patch / validation hardening release.

## Summary

This release performs a full validation pass over v0.6.4 and adds small defensive fixes discovered during the audit.

## Fixed

- Wrapped Logcat command construction in error handling so missing/moved ADB or command-building failures show a clean dialog instead of a Tkinter callback traceback.
- Wrapped Screenrecord command construction in error handling for the same failure mode.

## Revalidated

- Dashboard command output routing from v0.6.4.
- Version metadata and About metadata.
- Android file-browser path helpers and NUL-delimited parser.
- Logcat queue/event handling logic.
- Package integrity.

## Known limitation

No live Windows + Android device stress test was available in this environment.
