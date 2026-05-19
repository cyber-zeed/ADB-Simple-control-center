# Changelog

## v0.7.0 — 2026-05-18

### Added

- Persistent settings system using `%APPDATA%\ADB Control Center\settings.json` on Windows.
- Restores last selected device, saved paths/folders, Logcat settings, reconnect option, TCP/IP port, Platform-Tools folder, and window geometry.
- Cancellable progress UI for large push, pull, APK install, and screenrecord pull operations.
- Close prompt while Logcat is running with optional autosave before exit.
- GitHub Actions validation workflow.
- Real-device Windows + Android stress-test plan.

### Fixed / Hardened

- Reduced risk of silent data loss on app close while Logcat is still active.
- Long operations now have a user-visible cancel path.

## v0.6.13 and earlier

See prior release history for Logcat dual-spool, filter, reconnect, file-browser, installer, About, and dashboard fixes.
