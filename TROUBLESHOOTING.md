# Troubleshooting

## ADB is not detected

Open the Installers tab and press **Check ADB**. If ADB is missing, install Android Platform-Tools. Restart the terminal/app if PATH was changed.

## Device is unauthorized

Unlock the Android device and accept the USB debugging authorization prompt. Then press **Refresh**.

## Device is offline

Try:

```powershell
adb kill-server
adb start-server
adb devices -l
```

Then reconnect the USB cable and refresh devices.

## Logcat is noisy or GUI slows down

The app limits visible Logcat lines and spools the full stream to disk. Use host-side filters such as `gps` or `regex:gps|gnss` to reduce visible output while preserving the full session.

## 7-Zip save fails

Install 7-Zip from the Installers tab or add `7z.exe` to PATH.

## Push/Pull/Install hangs

Use the operation progress area and press **Cancel**. Then verify USB stability, device authorization, and storage permissions.
