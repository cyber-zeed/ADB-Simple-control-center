# Real Windows + Android Stress Test Plan — ADB Control Center v0.7.0

This plan is intended for a real Windows PC and at least one Android device with USB debugging enabled.

## Test environment record

Fill this before testing:

- Windows version:
- Python version:
- ADB/platform-tools version:
- 7-Zip version:
- Device model:
- Android version:
- USB connection type/cable:
- Wireless ADB target, if used:

## 1. Startup and settings persistence

1. Launch the app.
2. Resize the window.
3. Select a device.
4. Set local path, remote path, Android browser folder, Logcat filter, timestamp option, reconnect option, and TCP/IP port.
5. Close and reopen the app.
6. Confirm all options and window size are restored.

Expected result: no crash, settings restored from `%APPDATA%\ADB Control Center\settings.json`.

## 2. Device discovery and toolbar actions

1. Press **Refresh** repeatedly.
2. Press **Info**.
3. Start Logcat, then press **Root** on a build that allows or rejects root.
4. Start Logcat, then press **Reboot**.
5. Repeat for **Recovery** and **Bootloader** only on a device where those actions are safe.

Expected result: UI remains responsive; expected disconnects are handled; logs are autosaved where applicable.

## 3. Logcat high-volume stress

1. Start Logcat with no filter for 10 minutes.
2. Run a high-volume app/activity on the device.
3. Confirm GUI remains responsive.
4. Save logs.
5. Confirm `.7z` archive contains unfiltered log.
6. Restart Logcat with `gps` filter.
7. Confirm visible output is filtered, while saved archive contains both unfiltered and filtered files.
8. Repeat with `regex:gps|gnss`.

Expected result: no freeze; no data loss in spool/archive.

## 4. USB disconnect/reconnect during Logcat

1. Start Logcat.
2. Wait 30 seconds.
3. Disconnect USB.
4. Wait for autosave message.
5. Reconnect the same device.
6. Confirm Logcat resumes into a new session.
7. Inspect the `logs` folder.

Expected result: old session saved as `.7z`; new session starts after reconnect.

## 5. Wireless ADB reconnect

1. Enable TCP/IP mode.
2. Connect wirelessly.
3. Start Logcat.
4. Disable Wi-Fi or disconnect wireless ADB temporarily.
5. Reconnect.

Expected result: wireless session is autosaved and Logcat resumes when the same serial/IP target returns.

## 6. Large file push/pull with cancel

1. Push a large file or folder to `/sdcard/Download`.
2. Confirm progress UI appears.
3. Press **Cancel** mid-transfer.
4. Repeat and allow completion.
5. Pull a large folder from Android.
6. Cancel once, then allow one full run.

Expected result: UI remains responsive; cancel terminates the ADB process; successful run reports output.

## 7. APK install with cancel

1. Select a large APK/APKM-compatible file that `adb install` can process.
2. Start install.
3. Confirm progress UI appears.
4. Cancel once.
5. Repeat and allow completion.

Expected result: cancel does not freeze the app; completion output is shown.

## 8. Screenrecord pull with cancel

1. Start screenrecord.
2. Record for at least 30 seconds.
3. Stop + Pull.
4. Cancel the pull.
5. Confirm the recording remains on device or is recoverable.
6. Repeat and allow pull completion.

Expected result: progress UI appears; cancel does not clear state incorrectly; completed pull removes remote temp file.

## 9. Android file browser unusual filenames

Create or locate files/folders with:

- spaces
- brackets
- quotes
- leading/trailing spaces
- non-ASCII characters

Refresh the Android file browser and navigate into folders.

Expected result: listings do not break and selected paths are reflected correctly.

## 10. Close while Logcat is running

1. Start Logcat.
2. Close the app.
3. Choose Cancel.
4. Close again, choose Yes.
5. Confirm archive is saved.
6. Reopen, start Logcat again, close, choose No.

Expected result: Cancel keeps app open; Yes saves; No exits without saving.

## Pass/fail criteria

A build is ready for a `1.0.0` candidate only if:

- no GUI freeze occurs during high-volume Logcat
- no Logcat data is lost during normal save, USB disconnect, wireless disconnect, reboot, or close-save
- long operations can be canceled without leaving the GUI stuck
- settings persist across restarts
- GitHub Actions validation passes
