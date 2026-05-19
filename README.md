# ADB Control Center

**Current version:** `0.7.0`  
**Author/Credits:** Flavio Lira (CyberZeed)  
**Contact:** fr.lira@gmail.com  
**GitHub:** https://github.com/Cyber-Zeed

ADB Control Center is a Windows-friendly Python/Tkinter desktop GUI for common Android Debug Bridge workflows: device discovery, shell commands, APK install/uninstall, file push/pull, Android file browsing, screenshots, screen recording, wireless ADB, package inspection, and Logcat capture.

## Highlights in v0.7.0

- Persistent settings for:
  - last selected device serial
  - local and remote folders
  - Android file-browser folder
  - Logcat filter
  - timestamp option
  - auto-reconnect option
  - Platform-Tools install folder
  - TCP/IP port
  - window size/geometry
- Cancellable progress UI for long operations:
  - `adb push`
  - `adb pull`
  - APK install
  - screenrecord pull
- Close protection while Logcat is running:
  - prompts to save and exit
  - stop without saving
  - or cancel close
- GitHub Actions workflow for compile/import/parser/package checks.
- Real-device Windows + Android stress-test plan included.

## Quick start

1. Install Python 3 if needed.
2. Extract this repository/package.
3. Double-click:

```bat
Launch_ADB_Control_Center.bat
```

Or run manually:

```powershell
python .\ADB_Control_Center.py
```

## First run

Open the **Installers** tab and use:

- **Check ADB**
- **Install Platform-Tools** if needed
- **Check Python**
- **Install Python 3 (Latest)** if needed
- **Check 7-Zip**
- **Install 7-Zip (Latest)** if needed

Default Platform-Tools folder is `C:db`.

## Logcat behavior

Logcat is designed to avoid data loss:

- unfiltered ADB output is spooled to a temporary file as soon as Logcat starts
- host-side filtered output is spooled in parallel when using text/regex filters
- saved `.7z` archives can include both unfiltered and filtered logs
- USB/wireless disconnects and expected reboot/root disconnects auto-save the closed session
- closing the app while Logcat is running prompts before exiting

## Repository layout

```text
ADB_Control_Center.py
Launch_ADB_Control_Center.bat
README.md
USER_MANUAL.md
TROUBLESHOOTING.md
CHANGELOG.md
RELEASE_NOTES.md
VERSION
VERSIONING.md
VALIDATION_v0.7.0.md
REAL_DEVICE_STRESS_TEST_PLAN.md
requirements.txt
.github/workflows/validate.yml
tools/validate_package.py
```

## Validation

Run local validation:

```powershell
python .	oolsalidate_package.py
```

GitHub Actions runs the same checks on push and pull request.

## Known limitation

This package is code/package validated here, but live Windows + Android device stress testing must be run on real hardware. See `REAL_DEVICE_STRESS_TEST_PLAN.md`.
