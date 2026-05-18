# ADB Control Center User Manual

Version: 0.6.5  
Release date: 2026-05-18

## 1. Starting the application

Recommended:

```text
Launch_ADB_Control_Center.bat
```

Manual:

```powershell
python .\ADB_Control_Center.py
```

The batch file first tries the Windows Python launcher `py -3`, then falls back to `python`.

## 2. First-time setup

1. Open the **Installers** tab.
2. Click **Check Python**. If Python is missing, install Python 3.
3. Click **Check 7-Zip**. If missing, install 7-Zip.
4. Click **Check ADB**. If missing, choose an installation folder and install Platform-Tools.
5. Connect your Android device by USB.
6. Enable USB debugging on the device.
7. Accept the authorization prompt on the device.
8. Click **Refresh** in the app.

For system PATH updates, run the app as Administrator. If not elevated, ADB can still be installed, but system-wide PATH updates may be skipped.

## 3. Device bar

The top bar contains the selected ADB device and global actions.

- **Refresh**: reloads connected devices.
- **Info**: reads device properties and updates the dashboard.
- **Reboot**: normal Android reboot.
- **Recovery**: reboot to recovery.
- **Bootloader**: reboot to bootloader.

## 4. Installers tab

### Android Platform-Tools

- Default folder: `C:db`
- You can choose another folder.
- **Check ADB** searches PATH and known folders.
- **Install Platform-Tools** downloads/extracts the official package and attempts to update PATH.

### Python 3

- **Check Python** verifies Python availability.
- **Install Python 3** resolves the latest official Windows x64 installer and runs it in passive mode.

### 7-Zip

- **Check 7-Zip** searches PATH and common install locations.
- **Install 7-Zip** resolves the latest Windows x64 installer and runs it silently.

All check/install actions are designed to run in worker threads so the GUI remains responsive.

## 5. Logcat tab

### Basic logging

1. Select a device.
2. Optionally set a logcat filter.
3. Choose whether to prefix host timestamps.
4. Click **Start Logcat**.

### Session controls

- **New Session**: clears visible logs and starts a fresh spool/session.
- **Append Session**: keeps appending to the current session.
- **Clear Visible Only**: clears the GUI text area but keeps the full spool file for saving.

### Saving logs

Manual save writes a `.7z` archive. The saved log includes:

- export timestamp
- selected device serial
- manufacturer/model/build information when available
- battery information when available
- log contents

Filename pattern:

```text
<device_serial>_logcat_<timestamp>.7z
```

### USB disconnect rollover

When enabled and a USB device disconnects while logging:

1. The current session is automatically saved into `logs/`.
2. The app waits for the same serial to reconnect.
3. Logcat resumes into a fresh session.

## 6. Files tab

The Files tab supports both direct push/pull paths and the remote Android browser.

### Browse remote folders

- **Refresh**: list current path.
- **Up**: go to parent folder.
- **Home**: go to `/sdcard/`.
- Double-click a folder to enter it.
- Select an item and use **Pull Selected** to copy it to the PC.
- Use **Push Local to Current Folder** to push a PC file/folder into the currently displayed Android folder.

v0.6.5 includes safer handling for blank/inaccessible remote paths and no longer shows raw internal error markers.

## 7. Shell and Raw ADB tabs

### Shell

Runs commands through:

```text
adb shell <command>
```

### Raw ADB

Runs arbitrary ADB arguments. Example:

```text
devices -l
```

Do not include the word `adb`; the app adds it automatically.

## 8. APK and Packages tabs

- Install APK.
- Optional reinstall and permission grant.
- Uninstall by package name.
- List packages.
- Show APK path.
- Query battery info and device properties.

## 9. Capture tab

- Take screenshots and pull them to the PC.
- Start/stop Android screenrecord.
- Pull and remove the recorded file.
- Use wireless ADB helpers.

## 10. About menu

Use **Help → About** to view app version, credits, contact e-mail, and GitHub link.

Credits: Flavio Lira (CyberZeed)
E-mail: fr.lira@gmail.com
GitHub: https://github.com/Cyber-Zeed

## Dashboard command output

The Dashboard tab contains three areas: ADB Status, Device Info, and Command Output. In version 0.6.5, the Command Output area has quick action buttons:

- Check ADB
- Refresh Devices
- Load Device Info
- Start Server
- Kill Server
- Clear Output

Dashboard actions write timestamped entries to Command Output. This makes it easier to confirm whether a button actually ran, what command was used, and what ADB returned.
