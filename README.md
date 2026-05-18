# ADB Control Center

**Version:** 0.6.5  
**Release date:** 2026-05-18  
**Author/Credits:** Flavio Lira (CyberZeed)  
**E-mail:** fr.lira@gmail.com  
**GitHub:** https://github.com/Cyber-Zeed

ADB Control Center is a Windows desktop GUI for common Android Debug Bridge workflows. It wraps day-to-day ADB operations in a Tkinter interface and includes built-in installers for Android Platform-Tools, Python 3, and 7-Zip.

## Current package contents

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
VALIDATION_v0.6.5.md
requirements.txt
.gitignore
```

## Requirements

- Windows 10/11 recommended
- Python 3.8 or newer
- Android device with USB debugging enabled
- 7-Zip for `.7z` log export
- Administrator privileges recommended only when installing ADB and updating the system PATH

The application itself uses only Python standard-library modules.

## Quick start

1. Extract the package.
2. Double-click `Launch_ADB_Control_Center.bat`.
3. Open the **Installers** tab.
4. Use **Check ADB** and, if needed, install Android Platform-Tools.
5. Connect and authorize your Android device.
6. Click **Refresh** in the device bar.

Manual launch:

```powershell
python .\ADB_Control_Center.py
```

## Main features

### Device and ADB management

- Detect ADB from PATH, `C:db`, or legacy `C:\platform-tools`.
- Start/kill ADB server.
- Refresh device list.
- Show connected device details.
- Reboot to Android, recovery, or bootloader.

### Installers tab

- Check ADB status.
- Install Android Platform-Tools to a selected folder, defaulting to `C:db`.
- Check Python 3 status.
- Install latest Python 3 Windows x64 installer.
- Check 7-Zip status.
- Install latest 7-Zip Windows x64 installer.
- Installer/status checks run in background workers to avoid UI freezes.

### Logcat

- Live logcat viewer.
- Bounded visible log buffer to reduce freeze risk under heavy logs.
- Full-session spool file for complete saves.
- Optional host-side timestamp prefix for each line.
- Device-info header in saved logs.
- Save logs as `.7z` with ultra compression.
- Device serial is added at the start of saved log filenames.
- USB disconnect auto-rollover: current log is auto-saved, then a fresh session starts after reconnect.
- Explicit session controls:
  - **New Session**
  - **Append Session**
  - **Clear Visible Only**

### File manager

- Browse Android remote folders.
- Push local files/folders to the selected Android folder.
- Pull selected remote files/folders.
- Remote listing uses a NUL-delimited shell protocol instead of parsing plain `ls` output, improving support for filenames with spaces, quotes, brackets, Unicode, and embedded newlines.
- v0.6.5 fixes the raw `__ADBGUI_ERROR__...` popup and adds safe remote path fallbacks.

### Other tools

- Shell commands.
- Raw ADB commands.
- APK install/uninstall.
- Package listing.
- Screenshot capture.
- Screen recording.
- Wireless ADB connect/tcpip helpers.

## About menu

The application includes **Help → About** with credit to **Flavio Lira (CyberZeed)**, e-mail **fr.lira@gmail.com**, and GitHub link **https://github.com/Cyber-Zeed**.

## Known limitations

- A real Windows + Android high-volume logcat stress test is still recommended before a `1.0.0` stable release.
- USB auto-reconnect requires the device to return with the same ADB serial.
- Remote file browsing is more robust than plain `ls` parsing, but Android shell/toolbox differences can still expose edge cases on very old or heavily customized devices.
- Installing Python and 7-Zip depends on current official download pages being reachable.

## Versioning

This project uses semantic-style versioning:

```text
MAJOR.MINOR.PATCH
```

- Patch: bugfix-only updates.
- Minor: new features or UI improvements.
- Major: breaking changes or stable release milestone.

v0.6.5 is a patch release over v0.6.1.


## v0.6.5 note

This patch corrects the GitHub link in Help → About and project documentation to `https://github.com/Cyber-Zeed`.

### Dashboard command output improvements

Version 0.6.5 improves the Dashboard tab so command output is visible and easier to follow. The Dashboard now includes quick action buttons for Check ADB, Refresh Devices, Load Device Info, Start Server, Kill Server, and Clear Output. A timestamped command history is also written to the Dashboard command output area for ADB status checks, device refreshes, device info, and background actions.
