# Troubleshooting

Version: 0.6.5

## `adb` was not found

Open the **Installers** tab and click **Check ADB**. If it is missing, install Platform-Tools to `C:db` or another folder.

If ADB was installed but a new terminal cannot find it, close and reopen the terminal. System PATH changes do not always apply to already-open shells.

## Device is `unauthorized`

Unlock the Android device and accept the USB debugging prompt. Then click **Refresh**.

You can also try:

```powershell
adb kill-server
adb start-server
adb devices
```

## Device is `offline`

Try these steps:

1. Disconnect/reconnect the USB cable.
2. Disable and re-enable USB debugging.
3. Kill/start the ADB server.
4. Reboot the device.

## Logcat seems slow or noisy

The GUI intentionally limits visible log output to protect responsiveness. The full session is still written to the spool file while logging.

Use filters to reduce noise when possible, for example:

```text
*:W
```

## Save log fails because 7-Zip is missing

Open the **Installers** tab and click **Check 7-Zip** or **Install 7-Zip**.

## Error: remote path not accessible

Use the Files tab path field and try one of these:

```text
/sdcard/
/storage/emulated/0/
/
```

v0.6.5 handles blank paths and safe fallbacks more defensively, but Android permission restrictions can still prevent browsing some folders.

## USB auto-reconnect did not resume

The reconnect routine expects the same ADB serial to come back. If Windows or Android exposes a different serial after reconnect, select the new device manually and start logcat again.

## Python is not detected

Install Python 3 from the **Installers** tab or from Python.org. Make sure it is available as either `py -3` or `python`.

## The GUI opens but some installer buttons do nothing

Installer/status actions run in background workers. Watch the installer output panel for progress and error messages. Network access may be required for downloading current installers.

## Dashboard output looks empty

Use version 0.6.5 or newer. Earlier builds only wrote some actions to the Dashboard command output box. In 0.6.5, Dashboard quick buttons and background actions write timestamped command history entries.

If output is still empty, click **Check ADB** or **Refresh Devices** in the Dashboard tab. If ADB is missing or not working, the error should appear in both ADB Status and Command Output.
