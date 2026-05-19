# User Manual — ADB Control Center v0.7.0

## Dashboard

The Dashboard shows ADB status, selected device information, and timestamped command output. Use it for quick checks: **Check ADB**, **Refresh Devices**, **Load Device Info**, **Start Server**, and **Kill Server**.

## Top toolbar

The top toolbar contains:

- device selector
- Refresh
- Info
- Reboot
- Recovery
- Bootloader
- Root

The **Root** button runs `adb root`; it only works on devices/builds where `adbd` can run as root.

## APK tab

Use this tab to install and uninstall Android packages.

New in v0.7.0: APK install uses a cancellable progress UI. If the install is very large or stalls, press **Cancel**.

## Files tab

Use this tab to:

- push local files/folders to Android
- pull Android files/folders to the PC
- browse Android storage with a robust NUL-delimited file listing routine
- open selected folders
- push local content into the currently opened Android folder

New in v0.7.0: large push/pull operations use a cancellable progress UI.

## Logcat tab

Use **Start**, **Stop**, **New Session**, **Append Session**, **Clear Visible Only**, and **Save**.

Filter examples:

```text
gps
text:gps
regex:gps|gnss
adb:-v threadtime -b radio
ActivityManager:D *:S
```

Normal text/regex filters are host-side display filters. The unfiltered log is still preserved in the spool file. When a host-side filter is active, the app also writes a filtered spool file in parallel.

## Capture tab

Use this tab for screenshots, screenrecord, and wireless ADB.

New in v0.7.0: **Stop + Pull Screenrecord** uses a cancellable progress UI.

## Installers tab

This tab can check/install:

- Android Platform-Tools / ADB
- Python 3
- 7-Zip

Status checks and installers run in background workers so the GUI remains responsive.

## Persistent settings

Settings are saved automatically. On Windows, they are stored under:

```text
%APPDATA%\ADB Control Center\settings.json
```

Saved settings include the last selected device, paths/folders, Logcat options, reconnect option, TCP/IP port, Platform-Tools folder, and window geometry.

## Closing while Logcat is running

When Logcat is running and you close the app, you will be asked whether to:

- save the current Logcat session and exit
- exit without saving
- cancel close and keep the app open
