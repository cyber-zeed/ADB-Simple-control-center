# Changelog

## v0.6.5 - 2026-05-18

### Fixed

- Hardened Logcat startup command construction against missing ADB/path failures.
- Hardened Screenrecord startup command construction against missing ADB/path failures.

### Validation

- Full static validation pass over the package.
- Syntax, AST, import smoke, helper, parser, package-integrity, and metadata checks passed.

## v0.6.4 - 2026-05-18

### Fixed
- Fixed Dashboard Command Output appearing unused or blank for common Dashboard actions.
- Added timestamped Dashboard command-history entries for ADB checks, device refreshes, device info, and background actions.

### Added
- Added Dashboard quick action buttons: Check ADB, Refresh Devices, Load Device Info, Start Server, Kill Server, and Clear Output.

## v0.6.3 - 2026-05-18

### Fixed
- Corrected the GitHub URL shown in **Help → About** and documentation.
- Updated project metadata to `https://github.com/Cyber-Zeed`.

## v0.6.3 - 2026-05-18

### Added

- Expanded **Help → About** dialog with:
  - Flavio Lira (CyberZeed) credit
  - e-mail: `fr.lira@gmail.com`
  - GitHub link: `https://github.com/Cyber-Zeed`
  - buttons to open GitHub and copy the e-mail address

### Changed

- Replaced the simple About message box with a small custom About window so contact fields are easier to read and use.

## v0.6.1 - 2026-05-18

### Fixed

- Fixed Android file-browser handling that could show the internal marker `__ADBGUI_ERROR__Not a directory or not accessible:` directly to the user.
- Blank remote browser paths now safely default to `/sdcard/`.
- Added fallback remote paths: `/sdcard`, `/storage/emulated/0`, and `/`.
- Improved the shell-side listing routine so invalid remote paths return structured output that the GUI can parse cleanly.

### Validation

- Python compile check passed.
- AST parse passed.
- Import smoke test passed.
- Remote listing parser smoke tests passed.
- Package integrity check passed.

## v0.6.0 - 2026-05-18

### Added

- First GitHub-tracked release.
- Version constants in the script.
- Installer tab with ADB, Python 3, and 7-Zip checks/installers.
- Logcat `.7z` export with device header and device-serial filename prefix.
- Host timestamp prefix option.
- USB disconnect auto-save/reconnect behavior.
- Explicit log session controls: New Session, Append Session, Clear Visible Only.
- Improved Android file browser using a NUL-delimited listing protocol.
- Help → About menu with credits to Flavio Lira (CyberZeed).

### Changed

- Logcat display handling was optimized with bounded queues, batching, trimming, and full-session spool files.
- Installer/status checks were moved to background workers to reduce UI blocking.