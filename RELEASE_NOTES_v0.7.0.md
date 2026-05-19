# Release Notes — v0.7.0

Release date: 2026-05-18

## Added

- Persistent settings for last selected device, folders, Logcat options, reconnect option, Platform-Tools folder, TCP/IP port, and window geometry.
- Progress/cancel UI for long operations:
  - APK install
  - file push
  - file pull
  - screenrecord pull
- Close-time Logcat protection with save/exit, exit-without-save, and cancel options.
- GitHub Actions workflow for syntax/import/parser/package validation.
- Real Windows + Android stress-test plan.

## Changed

- Long ADB transfers now run through a cancellable process runner instead of a simple background command wrapper.
- The window title now includes the app version.

## Notes

The package was validated at code/package level in this environment. Live Windows + Android device testing should follow `REAL_DEVICE_STRESS_TEST_PLAN.md`.
