import os
import re
import sys
import json
import queue
import shutil
import signal
import zipfile
import tempfile
import threading
import subprocess
import time
import shlex
import webbrowser
from pathlib import Path
from datetime import datetime
from urllib.request import urlretrieve, urlopen, Request
from urllib.parse import urljoin
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

APP_TITLE = "ADB Control Center"
APP_VERSION = "0.7.0"
APP_RELEASE_DATE = "2026-05-18"
__version__ = APP_VERSION
AUTHOR_NAME = "Flavio Lira"
AUTHOR_ALIAS = "CyberZeed"
AUTHOR_EMAIL = "fr.lira@gmail.com"
AUTHOR_GITHUB = "https://github.com/Cyber-Zeed"
PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
PYTHON_WINDOWS_RELEASES_URL = "https://www.python.org/downloads/windows/"
PYTHON_DOWNLOADS_URL = "https://www.python.org/downloads/"
SEVENZIP_DOWNLOADS_URL = "https://www.7-zip.org/download.html"
SETTINGS_SCHEMA_VERSION = 1
SETTINGS_APP_DIR_NAME = "ADB Control Center"

SEVENZIP_BASE_URL = "https://www.7-zip.org/"
DEFAULT_INSTALL_DIR = r"C:\adb"
LEGACY_INSTALL_DIRS = [r"C:\platform-tools"]

# Logcat UI throttling. Tkinter freezes if a very noisy logcat stream is fully
# drained in one event-loop pass. These limits keep the UI responsive.
LOGCAT_POLL_INTERVAL_MS = 50
LOGCAT_BUSY_POLL_INTERVAL_MS = 10
LOGCAT_POLL_MAX_ITEMS = 400
LOGCAT_UI_QUEUE_MAX_ITEMS = 20000
LOGCAT_POLL_TIME_BUDGET_SEC = 0.030
LOGCAT_UI_MAX_LINES = 8000
LOGCAT_UI_TRIM_TO_LINES = 6000
# Flush each accepted raw logcat line to the temporary session file. This is
# intentionally conservative: the UI may drop lines under heavy load, but the
# spool file should keep the complete stream that adb emitted.
LOGCAT_SPOOL_FLUSH_EVERY_LINES = 1


def is_windows() -> bool:
    return os.name == "nt"


def find_7zip_executable():
    candidates = [
        shutil.which("7z"),
        shutil.which("7z.exe"),
        shutil.which("7za"),
        shutil.which("7za.exe"),
        r"C:\\Program Files\\7-Zip\\7z.exe",
        r"C:\\Program Files (x86)\\7-Zip\\7z.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def run_quick(command, timeout=30, creationflags=0, cwd=None):
    effective_creationflags = creationflags
    if is_windows() and hasattr(subprocess, "CREATE_NO_WINDOW"):
        effective_creationflags |= subprocess.CREATE_NO_WINDOW
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        creationflags=effective_creationflags,
        cwd=cwd,
    )


def split_user_args(text: str):
    text = text or ""
    try:
        # posix=False preserves unquoted Windows paths such as C:\Users\Me\app.apk.
        # After parsing, strip only matching outer quotes so quoted paths with spaces
        # still work correctly.
        lexer = shlex.shlex(text, posix=False)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
        cleaned = []
        for token in tokens:
            if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
                token = token[1:-1]
            cleaned.append(token)
        return cleaned
    except ValueError:
        # Fall back to a simple split for malformed quoting so the GUI stays usable.
        return text.split()


def normalize_remote_path(path: str) -> str:
    # Preserve intentional spaces in remote paths. Some Android folders/files may
    # contain leading/trailing spaces, so avoid a blanket .strip() here. UI callers
    # should trim user-entered paths before passing them when that behavior is desired.
    path = str(path if path is not None else '/sdcard/')
    path = path.rstrip('\r\n')
    if path == '':
        path = '/sdcard/'
    path = path.replace('\\', '/')
    if not path.startswith('/'):
        path = '/' + path
    path = re.sub(r'/+', '/', path)
    if len(path) > 1 and path.endswith('/'):
        path = path.rstrip('/')
    return path or '/'


def join_remote_path(base: str, name: str) -> str:
    base = normalize_remote_path(base)
    # Do not strip spaces from Android filenames. Only path separators are invalid
    # inside a single filename, so keep whitespace exactly as reported by the device.
    name = '' if name is None else str(name).replace('\\', '/')
    name = name.strip('/')
    if not name:
        return base
    if base == '/':
        return '/' + name
    return normalize_remote_path(base + '/' + name)


def parent_remote_path(path: str) -> str:
    path = normalize_remote_path(path)
    if path == '/':
        return '/'
    parts = [p for p in path.split('/') if p]
    if len(parts) <= 1:
        return '/'
    return '/' + '/'.join(parts[:-1])


class ADBManager:
    def __init__(self):
        self.adb_path = self.find_adb()

    def find_adb(self):
        cmd = shutil.which("adb")
        if cmd and Path(cmd).exists():
            return cmd
        candidates = [Path(DEFAULT_INSTALL_DIR)] + [Path(p) for p in LEGACY_INSTALL_DIRS]
        for base in candidates:
            fallback = base / "adb.exe"
            if fallback.exists():
                return str(fallback)
        return None


    def has_adb(self):
        return self.find_adb() is not None

    def require_adb(self):
        self.adb_path = self.find_adb()
        if not self.adb_path:
            raise RuntimeError("ADB was not found. Use Tools > Install ADB first.")
        return self.adb_path

    def adb_cmd(self, *args, serial=None):
        adb = self.require_adb()
        cmd = [adb]
        if serial:
            cmd += ["-s", serial]
        cmd += list(args)
        return cmd

    def run_adb_result(self, *args, serial=None, timeout=60):
        cmd = self.adb_cmd(*args, serial=serial)
        result = run_quick(cmd, timeout=timeout)
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip() or "Unknown ADB error"
            raise RuntimeError(stderr)
        return result

    def run_adb(self, *args, serial=None, timeout=60):
        return self.run_adb_result(*args, serial=serial, timeout=timeout).stdout.strip()

    def run_adb_raw(self, *args, serial=None, timeout=60):
        # Raw stdout is needed for NUL-delimited shell output used by the file
        # browser. .strip() would corrupt unusual names such as files ending in a
        # space if they happen to be the last token.
        return self.run_adb_result(*args, serial=serial, timeout=timeout).stdout

    def adb_version(self):
        return self.run_adb("version")

    def list_devices(self):
        output = self.run_adb("devices", "-l")
        devices = []
        for line in output.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            serial = parts[0]
            state = parts[1] if len(parts) > 1 else "unknown"
            meta = " ".join(parts[2:]) if len(parts) > 2 else ""
            model = ""
            m = re.search(r"model:(\S+)", meta)
            if m:
                model = m.group(1)
            devices.append({
                "serial": serial,
                "state": state,
                "model": model,
                "meta": meta,
            })
        return devices

    def shell_script(self, script, serial=None, timeout=120):
        return self.run_adb("shell", "sh", "-c", script, serial=serial, timeout=timeout)

    def shell_script_raw(self, script, serial=None, timeout=120):
        return self.run_adb_raw("shell", "sh", "-c", script, serial=serial, timeout=timeout)

    @staticmethod
    def parse_remote_dir_listing(output: str, requested_path: str):
        # NUL-delimited protocol generated by list_remote_dir(). This avoids the
        # common ls-parsing failure cases: spaces, tabs, quotes, brackets, Unicode,
        # and even embedded newlines in filenames.
        requested_path = normalize_remote_path(requested_path)
        tokens = output.split('\x00')
        current_path = requested_path
        entries = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if not token:
                index += 1
                continue
            if token.startswith("__ADBGUI_ERROR__"):
                raise RuntimeError(token.replace("__ADBGUI_ERROR__", "", 1).strip() or f"Unable to list remote path: {requested_path}")
            if token.startswith("__ADBGUI_PWD__"):
                current_path = normalize_remote_path(token.replace("__ADBGUI_PWD__", "", 1) or requested_path)
                index += 1
                continue
            if token.startswith("__ADBGUI_ENTRY__"):
                entry_type = token.replace("__ADBGUI_ENTRY__", "", 1)
                name = tokens[index + 1] if index + 1 < len(tokens) else ""
                index += 2
                if not name or name in {'.', '..'}:
                    continue
                is_dir = entry_type == "d"
                entries.append({
                    'name': name,
                    'is_dir': is_dir,
                    'path': join_remote_path(current_path, name),
                })
                continue
            index += 1

        entries.sort(key=lambda item: (not item['is_dir'], item['name'].casefold()))
        return current_path, entries

    def _list_remote_dir_once(self, remote_path, serial=None, timeout=120):
        # This helper is intentionally strict and returns a structured error token
        # with exit code 0. If the shell exits non-zero, ADBManager.run_adb_result()
        # raises before parse_remote_dir_listing() can remove the internal
        # __ADBGUI_ERROR__ marker, which is what produced the raw popup reported in
        # v0.6.0.
        remote_path = normalize_remote_path(remote_path)
        quoted_path = shlex.quote(remote_path)
        script_parts = [
            f"p={quoted_path}; ",
            r'if [ -z "$p" ]; then p="/sdcard"; fi; ',
            r'if [ ! -d "$p" ]; then printf "%s\0" "__ADBGUI_ERROR__Not a directory or not accessible: $p"; exit 0; fi; ',
            r'cd "$p" 2>/dev/null || { printf "%s\0" "__ADBGUI_ERROR__Unable to enter: $p"; exit 0; }; ',
            r'pwd_value=$(pwd -P 2>/dev/null || pwd); ',
            r'printf "%s\0" "__ADBGUI_PWD__$pwd_value"; ',
            r'for entry in ./* ./.[!.]* ./..?*; do ',
            r'[ -e "$entry" ] || [ -L "$entry" ] || continue; ',
            r'name=${entry#./}; ',
            r'if [ "$name" = "." ] || [ "$name" = ".." ]; then continue; fi; ',
            r'if [ -d "$entry" ]; then typ=d; else typ=f; fi; ',
            r'printf "%s\0%s\0" "__ADBGUI_ENTRY__$typ" "$name"; ',
            r'done',
        ]
        script = "".join(script_parts)
        output = self.shell_script_raw(script, serial=serial, timeout=timeout)
        return self.parse_remote_dir_listing(output, remote_path)

    def list_remote_dir(self, remote_path, serial=None, timeout=120):
        # Be defensive with UI-provided values. A blank file-browser path should
        # always mean the Android shared-storage root, not an empty shell variable.
        raw_path = "" if remote_path is None else str(remote_path).strip()
        remote_path = normalize_remote_path(raw_path or "/sdcard/")

        # Some devices resolve /sdcard differently, and a few builds may expose
        # only /storage/emulated/0. Try safe fallbacks before surfacing an error.
        candidates = [remote_path]
        if remote_path == "/sdcard" or remote_path.startswith("/sdcard/"):
            mapped = "/storage/emulated/0" + remote_path[len("/sdcard"):]
            candidates.append(mapped)
        if remote_path == "/storage/emulated/0" or remote_path.startswith("/storage/emulated/0/"):
            mapped = "/sdcard" + remote_path[len("/storage/emulated/0"):]
            candidates.append(mapped)
        if remote_path in {"/sdcard", "/storage/emulated/0"}:
            candidates.extend(["/storage/emulated/0", "/sdcard", "/"])

        seen = set()
        last_error = None
        for candidate in candidates:
            candidate = normalize_remote_path(candidate)
            if candidate in seen:
                continue
            seen.add(candidate)
            try:
                return self._list_remote_dir_once(candidate, serial=serial, timeout=timeout)
            except RuntimeError as exc:
                last_error = exc

        if last_error:
            raise last_error
        raise RuntimeError(f"Unable to list remote path: {remote_path}")

    def install_adb(self, install_dir=DEFAULT_INSTALL_DIR, progress_cb=None):
        if not is_windows():
            raise RuntimeError("This installer is intended for Windows.")

        install_dir = Path(install_dir).expanduser()
        temp_zip = Path(tempfile.gettempdir()) / "platform-tools-latest-windows.zip"
        temp_extract = Path(tempfile.gettempdir()) / f"platform-tools-extract-{os.getpid()}"

        def update(msg):
            if progress_cb:
                progress_cb(msg)

        unsafe_targets = {
            Path(install_dir.anchor) if install_dir.anchor else install_dir,
            Path.home(),
        }
        if install_dir in unsafe_targets or str(install_dir).rstrip('\\/') in {"C:", "C:"}:
            raise RuntimeError(f"Refusing to install into unsafe folder: {install_dir}")

        existing_adb = install_dir / "adb.exe"
        if install_dir.exists() and install_dir.is_dir():
            existing_entries = [p.name.lower() for p in install_dir.iterdir()]
            allowed = {"adb.exe", "adbwinapi.dll", "adbwinusbapi.dll", "fastboot.exe", "source.properties", "sqlite3.exe", "etc", "lib", "NOTICE.txt".lower(), "NOTICE".lower()}
            if existing_entries and not existing_adb.exists() and "fastboot.exe" not in existing_entries:
                raise RuntimeError(
                    f"Target folder already exists and does not look like a platform-tools folder: {install_dir}"
                )

        try:
            update("Downloading platform-tools...")
            if temp_zip.exists():
                temp_zip.unlink(missing_ok=True)
            if temp_extract.exists():
                shutil.rmtree(temp_extract, ignore_errors=True)

            urlretrieve(PLATFORM_TOOLS_URL, temp_zip)

            update("Extracting platform-tools...")
            with zipfile.ZipFile(temp_zip, "r") as zf:
                zf.extractall(temp_extract)

            extracted = temp_extract / "platform-tools"
            if not extracted.exists():
                raise RuntimeError("Downloaded archive did not contain platform-tools folder.")

            update(f"Installing to {install_dir}...")
            install_dir.parent.mkdir(parents=True, exist_ok=True)
            if install_dir.exists():
                shutil.rmtree(install_dir, ignore_errors=True)
            shutil.move(str(extracted), str(install_dir))

            try:
                update("Adding platform-tools to system PATH...")
                self._add_to_system_path(str(install_dir))
            except RuntimeError as exc:
                current_parts = [p for p in os.environ.get("PATH", "").split(";") if p.strip()]
                if str(install_dir).lower() not in [p.rstrip('\\/').lower() for p in current_parts]:
                    os.environ["PATH"] = os.environ.get("PATH", "") + (";" if os.environ.get("PATH") else "") + str(install_dir)
                update(f"WARNING: {exc}")
                update("ADB was installed, but the system PATH was not updated. Restart as Administrator to add it system-wide.")

            self.adb_path = str(install_dir / "adb.exe")
            update("ADB installed successfully.")
        finally:
            shutil.rmtree(temp_extract, ignore_errors=True)
            temp_zip.unlink(missing_ok=True)


    def _add_to_system_path(self, path_to_add):
        if not is_windows():
            return
        try:
            import winreg
        except ImportError as exc:
            raise RuntimeError("winreg is unavailable on this Python build.") from exc

        reg_path = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
        access = winreg.KEY_READ | winreg.KEY_WRITE
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, access) as key:
                try:
                    current_path, reg_type = winreg.QueryValueEx(key, "Path")
                except FileNotFoundError:
                    current_path, reg_type = "", winreg.REG_EXPAND_SZ

                parts = [p for p in current_path.split(";") if p.strip()]
                norm = [p.rstrip("\\/").lower() for p in parts]
                candidate = path_to_add.rstrip("\\/").lower()
                if candidate not in norm:
                    new_path = current_path + (";" if current_path and not current_path.endswith(";") else "") + path_to_add
                    winreg.SetValueEx(key, "Path", 0, reg_type, new_path)

            if path_to_add.lower() not in [p.rstrip("\\/").lower() for p in os.environ.get("PATH", "").split(";") if p.strip()]:
                os.environ["PATH"] = os.environ.get("PATH", "") + (";" if os.environ.get("PATH") else "") + path_to_add

            # Broadcast environment change to Windows.
            import ctypes
            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            SMTO_ABORTIFHUNG = 0x0002
            ctypes.windll.user32.SendMessageTimeoutW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, None)
        except PermissionError as exc:
            raise RuntimeError("Administrator privileges are required to update the system PATH.") from exc

    def detect_python(self):
        candidates = [
            ["py", "-3", "--version"],
            ["python", "--version"],
            ["python3", "--version"],
        ]
        for cmd in candidates:
            exe = shutil.which(cmd[0])
            if not exe:
                continue
            try:
                res = run_quick(cmd, timeout=20)
            except Exception:
                continue
            output = ((res.stdout or "") + (res.stderr or "")).strip()
            if res.returncode == 0 and output:
                resolved_exe = exe
                if cmd[0] == "py":
                    try:
                        probe = run_quick(["py", "-3", "-c", "import sys; print(sys.executable)"], timeout=20)
                        candidate = (probe.stdout or "").strip().splitlines()
                        if probe.returncode == 0 and candidate:
                            resolved_exe = candidate[-1].strip() or exe
                    except Exception:
                        pass
                return resolved_exe, output
        return None, None

    def resolve_latest_python_installer(self):
        pages = [PYTHON_WINDOWS_RELEASES_URL, PYTHON_DOWNLOADS_URL]
        pattern = re.compile(r"href=[\"']([^\"']*python-(\d+\.\d+\.\d+)-amd64\.exe)[\"']", re.IGNORECASE)
        best = None

        for page_url in pages:
            req = Request(page_url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=30) as response:
                html = response.read().decode("utf-8", "replace")
            for href, version in pattern.findall(html):
                version_tuple = tuple(int(part) for part in version.split('.'))
                absolute = urljoin(page_url, href)
                candidate = (version_tuple, version, absolute)
                if best is None or candidate[0] > best[0]:
                    best = candidate

        if not best:
            raise RuntimeError("Could not resolve the latest Python Windows installer from python.org.")

        return best[1], best[2]

    def install_python_latest(self, progress_cb=None):
        if not is_windows():
            raise RuntimeError("This installer is intended for Windows.")

        def update(msg):
            if progress_cb:
                progress_cb(msg)

        version, url = self.resolve_latest_python_installer()
        temp_exe = Path(tempfile.gettempdir()) / f"python-{version}-amd64.exe"
        try:
            update(f"Resolved Python installer: {version}")
            update(f"Downloading from: {url}")
            urlretrieve(url, temp_exe)

            cmd = [
                str(temp_exe),
                "/passive",
                "InstallAllUsers=0",
                "PrependPath=1",
                "Include_test=0",
                "Include_launcher=1",
            ]
            update("Launching Python installer...")
            creationflags = subprocess.CREATE_NO_WINDOW if is_windows() and hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=3600,
                creationflags=creationflags,
            )
            if result.returncode not in (0, 3010, 1641):
                details = (result.stdout or "") + ("\n" if result.stdout and result.stderr else "") + (result.stderr or "")
                raise RuntimeError(details.strip() or f"Python installer exited with code {result.returncode}.")

            python_exe, version_text = self.detect_python()
            if python_exe:
                python_dir = str(Path(python_exe).parent)
                parts = [p for p in os.environ.get("PATH", "").split(";") if p.strip()]
                if python_dir.lower() not in [p.lower() for p in parts]:
                    os.environ["PATH"] = os.environ.get("PATH", "") + (";" if os.environ.get("PATH") else "") + python_dir

            update("Python installation completed.")
            if version_text:
                update(version_text)
            return version_text or f"Python {version} installed."
        finally:
            try:
                temp_exe.unlink(missing_ok=True)
            except Exception:
                pass

    def detect_7zip(self):
        candidates = [
            find_7zip_executable(),
            shutil.which("7z"),
            shutil.which("7z.exe"),
            r"C:\\Program Files\\7-Zip\\7z.exe",
            r"C:\\Program Files (x86)\\7-Zip\\7z.exe",
        ]

        seen = set()
        for candidate in candidates:
            if not candidate:
                continue
            candidate = str(candidate)
            key = candidate.lower()
            if key in seen:
                continue
            seen.add(key)
            if not Path(candidate).exists():
                continue
            try:
                res = run_quick([candidate], timeout=15)
            except Exception:
                continue
            output = ((res.stdout or "") + (res.stderr or "")).strip()
            if output:
                return candidate, output.splitlines()[0].strip()
        return None, None

    def resolve_latest_7zip_installer(self):
        req = Request(SEVENZIP_DOWNLOADS_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=30) as response:
            html = response.read().decode("utf-8", "replace")

        version_match = re.search(r'Download\s+7-Zip\s+(\d+\.\d+)\s*\(', html, re.IGNORECASE)
        version = version_match.group(1) if version_match else "latest"

        patterns = [
            re.compile(r"href=[\"']([^\"']*7z\d+(?:\.\d+)?-x64\.exe)[\"']", re.IGNORECASE),
            re.compile(r"href=[\"']([^\"']*7z\d+(?:\.\d+)?-x64\.msi)[\"']", re.IGNORECASE),
            re.compile(r"href=[\"']([^\"']*7z\d+(?:\.\d+)?\.exe)[\"']", re.IGNORECASE),
        ]

        for pattern in patterns:
            match = pattern.search(html)
            if match:
                return version, urljoin(SEVENZIP_BASE_URL, match.group(1))

        raise RuntimeError("Could not resolve the latest 7-Zip Windows x64 installer from 7-zip.org.")

    def install_7zip_latest(self, progress_cb=None):
        if not is_windows():
            raise RuntimeError("This installer is intended for Windows.")

        def update(msg):
            if progress_cb:
                progress_cb(msg)

        version, url = self.resolve_latest_7zip_installer()
        suffix = ".msi" if url.lower().endswith(".msi") else ".exe"
        temp_installer = Path(tempfile.gettempdir()) / f"7zip-{version}-x64{suffix}"

        try:
            update(f"Resolved 7-Zip installer: {version}")
            update(f"Downloading from: {url}")
            urlretrieve(url, temp_installer)

            creationflags = subprocess.CREATE_NO_WINDOW if is_windows() and hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            if suffix == ".msi":
                cmd = ["msiexec", "/i", str(temp_installer), "/q"]
            else:
                cmd = [str(temp_installer), "/S"]

            update("Launching 7-Zip installer...")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=3600,
                creationflags=creationflags,
            )
            if result.returncode not in (0, 3010, 1641):
                details = (result.stdout or "") + ("\n" if result.stdout and result.stderr else "") + (result.stderr or "")
                raise RuntimeError(details.strip() or f"7-Zip installer exited with code {result.returncode}.")

            update("7-Zip installation completed.")
            seven_zip_exe, version_text = self.detect_7zip()
            if seven_zip_exe:
                seven_zip_dir = str(Path(seven_zip_exe).parent)
                parts = [p for p in os.environ.get("PATH", "").split(";") if p.strip()]
                if seven_zip_dir.lower() not in [p.lower() for p in parts]:
                    os.environ["PATH"] = os.environ.get("PATH", "") + (";" if os.environ.get("PATH") else "") + seven_zip_dir
                update(f"7-Zip executable: {seven_zip_exe}")

            if version_text:
                update(version_text)
            return version_text or f"7-Zip {version} installed."
        finally:
            try:
                temp_installer.unlink(missing_ok=True)
            except Exception:
                pass



class ADBGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE} v{APP_VERSION}")
        self.settings = self.load_settings()
        self.geometry(self.settings.get("window_geometry") or "1280x840")
        self.minsize(1100, 760)

        self.manager = ADBManager()
        self.devices = []
        self.remote_entries = []
        self.selected_serial = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="Ready")
        self.logcat_process = None
        self.logcat_thread = None
        self.logcat_queue = queue.Queue(maxsize=LOGCAT_UI_QUEUE_MAX_ITEMS)
        self.logcat_queue_lock = threading.Lock()
        self.logcat_ui_dropped_lines = 0
        self.logcat_generation = 0
        self.logcat_active_serial = None
        self.logcat_target_serial = None
        self.logcat_stop_requested = False
        self.logcat_reconnect_running = False
        self.logcat_reconnect_thread = None
        self.logcat_cached_header = None
        self.logcat_pending_new_session = False
        self.logcat_ui_line_count = 0
        self.logcat_save_lock = threading.Lock()
        self.logcat_session_file = None
        self.logcat_filtered_session_file = None
        self.logcat_filter_description = None
        self.logcat_filter_text = ""
        self.logcat_session_timestamp_enabled = False
        self.logcat_expected_reboot_serial = None
        self.logcat_expected_reboot_mode = None
        self.logcat_next_start_clear_visible = False
        self.logcat_next_start_seed_visible = False
        self.screenrecord_process = None
        self.screenrecord_remote = None
        self.screenrecord_serial = None
        self._closing = False
        self._settings_save_after_id = None
        self._settings_ready = False
        self._active_operation_proc = None
        self._active_operation_dialog = None
        self._active_operation_cancel_requested = False

        self._build_style()
        self._build_menu()
        self._build_top_bar()
        self._build_tabs()
        self._bind_settings_autosave()
        self._settings_ready = True
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._poll_queues()
        self.after(250, self.refresh_devices)

    # ---------- UI building ----------
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

    def _build_menu(self):
        menubar = tk.Menu(self)

        tools = tk.Menu(menubar, tearoff=0)
        tools.add_command(label="Check ADB", command=self.check_adb)
        tools.add_command(label="Install ADB", command=self.install_adb)
        tools.add_separator()
        tools.add_command(label="Start Server", command=lambda: self.run_background_action("adb start-server", self._start_server))
        tools.add_command(label="Kill Server", command=lambda: self.run_background_action("adb kill-server", self._kill_server))
        tools.add_separator()
        tools.add_command(label="Export Device Info", command=self.export_device_info)
        menubar.add_cascade(label="Tools", menu=tools)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menubar)

    def _build_top_bar(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Device:").pack(side="left")
        self.device_combo = ttk.Combobox(top, textvariable=self.selected_serial, width=50, state="readonly")
        self.device_combo.pack(side="left", padx=(6, 8))
        self.device_combo.bind("<<ComboboxSelected>>", self._on_device_combo_selected)

        ttk.Button(top, text="Refresh", command=self.refresh_devices).pack(side="left")
        ttk.Button(top, text="Info", command=self.load_device_info).pack(side="left", padx=6)
        ttk.Button(top, text="Reboot", command=lambda: self.device_reboot("reboot")).pack(side="left")
        ttk.Button(top, text="Recovery", command=lambda: self.device_reboot("recovery")).pack(side="left", padx=6)
        ttk.Button(top, text="Bootloader", command=lambda: self.device_reboot("bootloader")).pack(side="left")
        ttk.Button(top, text="Root", command=self.device_root).pack(side="left", padx=(6, 0))

        ttk.Label(top, textvariable=self.status_var, anchor="e").pack(side="right", fill="x", expand=True)

    def _build_tabs(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.tab_dashboard = ttk.Frame(self.notebook, padding=10)
        self.tab_shell = ttk.Frame(self.notebook, padding=10)
        self.tab_apk = ttk.Frame(self.notebook, padding=10)
        self.tab_files = ttk.Frame(self.notebook, padding=10)
        self.tab_logcat = ttk.Frame(self.notebook, padding=10)
        self.tab_capture = ttk.Frame(self.notebook, padding=10)
        self.tab_packages = ttk.Frame(self.notebook, padding=10)
        self.tab_installers = ttk.Frame(self.notebook, padding=10)
        self.tab_raw = ttk.Frame(self.notebook, padding=10)

        self.notebook.add(self.tab_dashboard, text="Dashboard")
        self.notebook.add(self.tab_shell, text="Shell")
        self.notebook.add(self.tab_apk, text="APK")
        self.notebook.add(self.tab_files, text="Files")
        self.notebook.add(self.tab_logcat, text="Logcat")
        self.notebook.add(self.tab_capture, text="Capture")
        self.notebook.add(self.tab_packages, text="Packages")
        self.notebook.add(self.tab_installers, text="Installers")
        self.notebook.add(self.tab_raw, text="Raw ADB")

        self._build_dashboard_tab()
        self._build_shell_tab()
        self._build_apk_tab()
        self._build_files_tab()
        self._build_logcat_tab()
        self._build_capture_tab()
        self._build_packages_tab()
        self._build_installers_tab()
        self._build_raw_tab()


    def _build_dashboard_tab(self):
        upper = ttk.Frame(self.tab_dashboard)
        upper.pack(fill="x")

        left = ttk.LabelFrame(upper, text="ADB Status", padding=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        self.adb_status_text = ScrolledText(left, height=8, wrap="word")
        self.adb_status_text.pack(fill="both", expand=True)

        right = ttk.LabelFrame(upper, text="Device Info", padding=10)
        right.pack(side="left", fill="both", expand=True)

        self.device_info_text = ScrolledText(right, height=8, wrap="word")
        self.device_info_text.pack(fill="both", expand=True)

        lower = ttk.LabelFrame(self.tab_dashboard, text="Command Output", padding=10)
        lower.pack(fill="both", expand=True, pady=(10, 0))

        output_controls = ttk.Frame(lower)
        output_controls.pack(fill="x", pady=(0, 6))
        ttk.Button(output_controls, text="Check ADB", command=self.check_adb).pack(side="left")
        ttk.Button(output_controls, text="Refresh Devices", command=self.refresh_devices).pack(side="left", padx=(6, 0))
        ttk.Button(output_controls, text="Load Device Info", command=self.load_device_info).pack(side="left", padx=(6, 0))
        ttk.Button(output_controls, text="Start Server", command=lambda: self.run_background_action("adb start-server", self._start_server)).pack(side="left", padx=(6, 0))
        ttk.Button(output_controls, text="Kill Server", command=lambda: self.run_background_action("adb kill-server", self._kill_server)).pack(side="left", padx=(6, 0))
        ttk.Button(output_controls, text="Clear Output", command=lambda: self.clear_text(self.dashboard_output)).pack(side="right")

        self.dashboard_output = ScrolledText(lower, wrap="word")
        self.dashboard_output.pack(fill="both", expand=True)
        self.append_dashboard_output("Dashboard ready", "Use the buttons above or the top toolbar to run ADB status/device actions.")

    def _build_shell_tab(self):
        controls = ttk.Frame(self.tab_shell)
        controls.pack(fill="x")
        ttk.Label(controls, text="Shell command:").pack(side="left")
        self.shell_entry = ttk.Entry(controls)
        self.shell_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.shell_entry.bind("<Return>", lambda e: self.run_shell_command())
        ttk.Button(controls, text="Run", command=self.run_shell_command).pack(side="left")

        self.shell_output = ScrolledText(self.tab_shell, wrap="word")
        self.shell_output.pack(fill="both", expand=True, pady=(10, 0))

    def _build_apk_tab(self):
        frame = ttk.Frame(self.tab_apk)
        frame.pack(fill="x")

        ttk.Label(frame, text="APK file:").grid(row=0, column=0, sticky="w")
        self.apk_path_var = tk.StringVar(value=self.settings.get("apk_path", ""))
        ttk.Entry(frame, textvariable=self.apk_path_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(frame, text="Browse", command=self.browse_apk).grid(row=0, column=2)

        self.apk_reinstall_var = tk.BooleanVar(value=False)
        self.apk_grant_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Reinstall (-r)", variable=self.apk_reinstall_var).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Checkbutton(frame, text="Grant runtime permissions (-g)", variable=self.apk_grant_var).grid(row=1, column=1, sticky="w", pady=(8, 0))
        ttk.Button(frame, text="Install APK", command=self.install_apk).grid(row=1, column=2, pady=(8, 0))

        ttk.Separator(frame, orient="horizontal").grid(row=2, column=0, columnspan=3, sticky="ew", pady=12)
        ttk.Label(frame, text="Package name to uninstall:").grid(row=3, column=0, sticky="w")
        self.uninstall_pkg_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.uninstall_pkg_var).grid(row=3, column=1, sticky="ew", padx=8)
        ttk.Button(frame, text="Uninstall", command=self.uninstall_package).grid(row=3, column=2)

        frame.columnconfigure(1, weight=1)

        self.apk_progress_frame, self.apk_progress_var = self._create_operation_progress_area(self.tab_apk)

        self.apk_output = ScrolledText(self.tab_apk, wrap="word")
        self.apk_output.pack(fill="both", expand=True, pady=(10, 0))

    def _build_files_tab(self):
        top = ttk.Frame(self.tab_files)
        top.pack(fill="x")

        ttk.Label(top, text="Local path:").grid(row=0, column=0, sticky="w")
        self.local_path_var = tk.StringVar(value=self.settings.get("local_path", ""))
        ttk.Entry(top, textvariable=self.local_path_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(top, text="Browse File", command=self.browse_local_file).grid(row=0, column=2)
        ttk.Button(top, text="Browse Folder", command=self.browse_local_folder).grid(row=0, column=3, padx=(6, 0))

        ttk.Label(top, text="Remote path:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.remote_path_var = tk.StringVar(value=self.settings.get("remote_path", "/sdcard/"))
        ttk.Entry(top, textvariable=self.remote_path_var).grid(row=1, column=1, sticky="ew", padx=8, pady=(8, 0))
        ttk.Button(top, text="Push", command=self.push_file).grid(row=1, column=2, pady=(8, 0))
        ttk.Button(top, text="Pull", command=self.pull_file).grid(row=1, column=3, padx=(6, 0), pady=(8, 0))
        top.columnconfigure(1, weight=1)

        browser = ttk.LabelFrame(self.tab_files, text="Android File Browser", padding=10)
        browser.pack(fill="both", expand=True, pady=(10, 0))

        nav = ttk.Frame(browser)
        nav.pack(fill="x")
        ttk.Label(nav, text="Current folder:").pack(side="left")
        self.remote_browser_path_var = tk.StringVar(value=self.settings.get("remote_browser_path", "/sdcard/"))
        self.remote_browser_entry = ttk.Entry(nav, textvariable=self.remote_browser_path_var)
        self.remote_browser_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.remote_browser_entry.bind("<Return>", lambda e: self.remote_go_to_path())
        ttk.Button(nav, text="Refresh", command=self.refresh_remote_files).pack(side="left")
        ttk.Button(nav, text="Up", command=self.remote_go_up).pack(side="left", padx=6)
        ttk.Button(nav, text="Home", command=self.remote_go_home).pack(side="left")

        action_bar = ttk.Frame(browser)
        action_bar.pack(fill="x", pady=(8, 8))
        ttk.Button(action_bar, text="Pull Selected", command=self.pull_selected_remote).pack(side="left")
        ttk.Button(action_bar, text="Use Selected Path", command=self.use_selected_remote_path).pack(side="left", padx=6)
        ttk.Button(action_bar, text="Open Selected Folder", command=self.open_selected_remote_folder).pack(side="left", padx=(0, 6))
        ttk.Button(action_bar, text="Push Local to Current Folder", command=self.push_to_current_remote).pack(side="left")

        tree_frame = ttk.Frame(browser)
        tree_frame.pack(fill="both", expand=True)
        self.remote_tree = ttk.Treeview(tree_frame, columns=("type", "path"), show="tree headings", selectmode="browse")
        self.remote_tree.heading("#0", text="Name")
        self.remote_tree.heading("type", text="Type")
        self.remote_tree.heading("path", text="Full Path")
        self.remote_tree.column("#0", width=280, anchor="w")
        self.remote_tree.column("type", width=90, anchor="center")
        self.remote_tree.column("path", width=420, anchor="w")
        tree_scroll_y = ttk.Scrollbar(tree_frame, orient="vertical", command=self.remote_tree.yview)
        tree_scroll_x = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.remote_tree.xview)
        self.remote_tree.configure(yscrollcommand=tree_scroll_y.set, xscrollcommand=tree_scroll_x.set)
        self.remote_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll_y.grid(row=0, column=1, sticky="ns")
        tree_scroll_x.grid(row=1, column=0, sticky="ew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.remote_tree.bind("<<TreeviewSelect>>", self._on_remote_item_selected)
        self.remote_tree.bind("<Double-1>", self._on_remote_item_activated)
        self.remote_tree.bind("<Return>", self._on_remote_item_activated)

        output_frame = ttk.LabelFrame(self.tab_files, text="File Operations Output", padding=10)
        output_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.files_progress_frame, self.files_progress_var = self._create_operation_progress_area(output_frame)
        self.files_output = ScrolledText(output_frame, wrap="word")
        self.files_output.pack(fill="both", expand=True)

    def _build_logcat_tab(self):
        controls = ttk.Frame(self.tab_logcat)
        controls.pack(fill="x")
        ttk.Label(controls, text="Filter:").pack(side="left")
        self.logcat_filter_var = tk.StringVar(value=self.settings.get("logcat_filter", ""))
        ttk.Entry(controls, textvariable=self.logcat_filter_var).pack(side="left", fill="x", expand=True, padx=8)
        self.logcat_timestamp_var = tk.BooleanVar(value=bool(self.settings.get("logcat_timestamp", False)))
        ttk.Checkbutton(controls, text="Prefix host timestamp", variable=self.logcat_timestamp_var).pack(side="left", padx=(0, 8))
        self.logcat_auto_reconnect_var = tk.BooleanVar(value=bool(self.settings.get("logcat_auto_reconnect", True)))
        ttk.Checkbutton(controls, text="Auto reconnect same device", variable=self.logcat_auto_reconnect_var).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Start", command=self.start_logcat).pack(side="left")
        ttk.Button(controls, text="Stop", command=self.stop_logcat).pack(side="left", padx=6)
        ttk.Button(controls, text="New Session", command=self.new_logcat_session).pack(side="left")
        ttk.Button(controls, text="Append Session", command=self.append_logcat_session).pack(side="left", padx=6)
        ttk.Button(controls, text="Clear Visible Only", command=self.clear_visible_logcat_only).pack(side="left")
        ttk.Button(controls, text="Save", command=self.save_logcat).pack(side="left", padx=6)

        self.logcat_output = ScrolledText(self.tab_logcat, wrap="none")
        self.logcat_output.pack(fill="both", expand=True, pady=(10, 0))

    def _build_capture_tab(self):
        frame = ttk.Frame(self.tab_capture)
        frame.pack(fill="x")

        ttk.Button(frame, text="Take Screenshot", command=self.take_screenshot).grid(row=0, column=0, sticky="w")
        ttk.Button(frame, text="Start Screenrecord", command=self.start_screenrecord).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(frame, text="Stop + Pull Screenrecord", command=self.stop_screenrecord).grid(row=0, column=2, sticky="w")

        ttk.Separator(frame, orient="horizontal").grid(row=1, column=0, columnspan=3, sticky="ew", pady=12)
        ttk.Label(frame, text="Wireless ADB IP:Port").grid(row=2, column=0, sticky="w")
        self.connect_target_var = tk.StringVar(value="192.168.0.10:5555")
        ttk.Entry(frame, textvariable=self.connect_target_var, width=25).grid(row=2, column=1, sticky="w", padx=8)
        ttk.Button(frame, text="adb connect", command=self.connect_wireless).grid(row=2, column=2, sticky="w")

        ttk.Label(frame, text="TCP/IP Port").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.tcpip_port_var = tk.StringVar(value=self.settings.get("tcpip_port", "5555"))
        ttk.Entry(frame, textvariable=self.tcpip_port_var, width=10).grid(row=3, column=1, sticky="w", padx=8, pady=(8, 0))
        ttk.Button(frame, text="Enable adb tcpip", command=self.enable_tcpip).grid(row=3, column=2, sticky="w", pady=(8, 0))

        self.capture_progress_frame, self.capture_progress_var = self._create_operation_progress_area(self.tab_capture)
        self.capture_output = ScrolledText(self.tab_capture, wrap="word")
        self.capture_output.pack(fill="both", expand=True, pady=(10, 0))

    def _build_packages_tab(self):
        controls = ttk.Frame(self.tab_packages)
        controls.pack(fill="x")
        self.third_party_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(controls, text="Third-party only", variable=self.third_party_only).pack(side="left")
        ttk.Button(controls, text="List Packages", command=self.list_packages).pack(side="left", padx=8)
        ttk.Button(controls, text="App Path", command=self.show_package_path).pack(side="left")
        ttk.Button(controls, text="Battery Info", command=self.load_battery_info).pack(side="left", padx=8)
        ttk.Button(controls, text="Properties", command=self.load_properties).pack(side="left")

        mid = ttk.Frame(self.tab_packages)
        mid.pack(fill="both", expand=True, pady=(10, 0))

        left = ttk.Frame(mid)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="Packages:").pack(anchor="w")
        self.package_list = tk.Listbox(left)
        self.package_list.pack(fill="both", expand=True)
        self.package_list.bind("<<ListboxSelect>>", self._on_package_selected)

        right = ttk.Frame(mid)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        ttk.Label(right, text="Details:").pack(anchor="w")
        self.package_output = ScrolledText(right, wrap="word")
        self.package_output.pack(fill="both", expand=True)

    def _build_raw_tab(self):
        controls = ttk.Frame(self.tab_raw)
        controls.pack(fill="x")
        ttk.Label(controls, text="adb ").pack(side="left")
        self.raw_entry = ttk.Entry(controls)
        self.raw_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.raw_entry.bind("<Return>", lambda e: self.run_raw_command())
        ttk.Button(controls, text="Run", command=self.run_raw_command).pack(side="left")

        self.raw_output = ScrolledText(self.tab_raw, wrap="word")
        self.raw_output.pack(fill="both", expand=True, pady=(10, 0))


    def _build_installers_tab(self):
        adb_frame = ttk.LabelFrame(self.tab_installers, text="Android Platform-Tools", padding=10)
        adb_frame.pack(fill="x")

        ttk.Label(adb_frame, text="Install folder:").grid(row=0, column=0, sticky="w")
        self.installer_adb_dir_var = tk.StringVar(value=self.settings.get("installer_adb_dir", DEFAULT_INSTALL_DIR))
        ttk.Entry(adb_frame, textvariable=self.installer_adb_dir_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(adb_frame, text="Browse", command=self.browse_platform_tools_dir).grid(row=0, column=2)
        ttk.Button(adb_frame, text="Check ADB", command=self.check_adb_status).grid(row=0, column=3, padx=(8, 0))
        ttk.Button(adb_frame, text="Install Platform-Tools", command=self.install_adb_from_tab).grid(row=0, column=4, padx=(8, 0))
        self.installer_adb_status_var = tk.StringVar(value="Not checked")
        ttk.Label(adb_frame, textvariable=self.installer_adb_status_var).grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))
        ttk.Label(
            adb_frame,
            text=r"Default folder is C:\adb. Run the app as Administrator if you want the installer to update the system PATH.",
        ).grid(row=2, column=0, columnspan=5, sticky="w", pady=(4, 0))
        adb_frame.columnconfigure(1, weight=1)

        python_frame = ttk.LabelFrame(self.tab_installers, text="Python 3", padding=10)
        python_frame.pack(fill="x", pady=(10, 0))
        ttk.Label(python_frame, text="Latest official 64-bit Windows installer from python.org.").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Button(python_frame, text="Check Python", command=self.check_python_status).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(python_frame, text="Install Python 3 (Latest)", command=self.install_python_from_tab).grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))
        self.python_status_var = tk.StringVar(value="Not checked")
        ttk.Label(python_frame, textvariable=self.python_status_var).grid(row=1, column=2, sticky="w", pady=(8, 0))

        seven_zip_frame = ttk.LabelFrame(self.tab_installers, text="7-Zip", padding=10)
        seven_zip_frame.pack(fill="x", pady=(10, 0))
        ttk.Label(seven_zip_frame, text="Latest official 64-bit Windows installer from 7-zip.org.").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Button(seven_zip_frame, text="Check 7-Zip", command=self.check_7zip_status).grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(seven_zip_frame, text="Install 7-Zip (Latest)", command=self.install_7zip_from_tab).grid(row=1, column=1, sticky="w", padx=8, pady=(8, 0))
        self.seven_zip_status_var = tk.StringVar(value="Not checked")
        ttk.Label(seven_zip_frame, textvariable=self.seven_zip_status_var).grid(row=1, column=2, sticky="w", pady=(8, 0))

        output_frame = ttk.LabelFrame(self.tab_installers, text="Installer Output", padding=10)
        output_frame.pack(fill="both", expand=True, pady=(10, 0))
        self.installers_output = ScrolledText(output_frame, wrap="word")
        self.installers_output.pack(fill="both", expand=True)

    def sanitize_filename_part(self, value: str, fallback: str = "unknown_device") -> str:
        value = (value or "").strip()
        if not value:
            return fallback
        value = re.sub(r'[\\/:*?"<>|]+', '_', value)
        value = re.sub(r'\s+', '_', value)
        value = value.strip(' ._')
        return value or fallback

    def get_logcat_filename_serial(self) -> str:
        serial = self.logcat_active_serial
        if serial:
            return self.sanitize_filename_part(serial)
        try:
            return self.sanitize_filename_part(self.get_selected_serial())
        except Exception:
            return "unknown_device"

    def get_logs_output_dir(self) -> Path:
        base_dir = Path(__file__).resolve().parent
        logs_dir = base_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        return logs_dir

    def build_automatic_logcat_archive_path(self, serial: str, suffix: str = "logcat") -> Path:
        device_name = self.sanitize_filename_part(serial, fallback="unknown_device")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return self.get_logs_output_dir() / f"{device_name}_{suffix}_{timestamp}.7z"

    def build_fallback_log_header(self, serial: str) -> str:
        serial = (serial or "").strip() or "unknown_device"
        lines = [
            "=" * 80,
            "ADB GUI Log Export",
            "=" * 80,
            f"Host export time: {datetime.now().isoformat(timespec='seconds')}",
            f"Device serial: {serial}",
            "Device details: <unavailable>",
            "-" * 80,
            "Log contents",
            "-" * 80,
            "",
        ]
        return "\n".join(lines)

    def prepare_logcat_header(self, serial: str) -> str:
        try:
            header = self.collect_device_log_header(serial)
        except Exception:
            header = self.build_fallback_log_header(serial)
        self.logcat_cached_header = header
        return header

    def prepare_logcat_header_async(self, serial: str):
        # Build the rich device header in a worker thread. Doing multiple
        # getprop/dumpsys calls on the Tkinter thread can freeze the GUI when
        # the device is slow, unauthorized, or disconnecting.
        self.logcat_cached_header = self.build_fallback_log_header(serial)

        def worker(serial_ref=serial):
            try:
                header = self.collect_device_log_header(serial_ref)
            except Exception:
                header = self.build_fallback_log_header(serial_ref)
            if not self._closing and self.logcat_target_serial == serial_ref:
                self.logcat_cached_header = header

        threading.Thread(target=worker, daemon=True).start()

    def create_logcat_archive(self, archive_path, content: str, serial_for_header: str, header_override: str = None):
        seven_zip = find_7zip_executable()
        if not seven_zip:
            raise RuntimeError("7-Zip was not found. Install 7-Zip or add 7z.exe to PATH before saving logs as .7z.")

        archive_path_obj = Path(archive_path)
        archive_path_obj.parent.mkdir(parents=True, exist_ok=True)
        internal_name = archive_path_obj.stem + ".txt"
        temp_dir = Path(tempfile.mkdtemp(prefix="adb_gui_logcat_"))
        temp_log_path = temp_dir / internal_name

        try:
            header = header_override or self.logcat_cached_header or self.build_fallback_log_header(serial_for_header)
            archive_path_obj.unlink(missing_ok=True)
            full_content = header + content
            temp_log_path.write_text(full_content, encoding="utf-8")
            cmd = [
                seven_zip,
                "a",
                "-t7z",
                str(archive_path_obj),
                internal_name,
                "-mx=9",
                "-mmt=on",
                "-y",
            ]
            res = run_quick(cmd, timeout=600, cwd=str(temp_dir))
            if res.returncode != 0:
                details = (res.stdout or "") + ("\n" if res.stdout and res.stderr else "") + (res.stderr or "")
                raise RuntimeError(details.strip() or "7-Zip failed to create the archive.")
            return archive_path_obj
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def create_logcat_archive_from_file(self, archive_path, source_log_path, serial_for_header: str, header_override: str = None):
        seven_zip = find_7zip_executable()
        if not seven_zip:
            raise RuntimeError("7-Zip was not found. Install 7-Zip or add 7z.exe to PATH before saving logs as .7z.")

        source = Path(source_log_path)
        if not source.exists():
            raise RuntimeError(f"Log session file not found: {source}")

        archive_path_obj = Path(archive_path)
        archive_path_obj.parent.mkdir(parents=True, exist_ok=True)
        internal_name = archive_path_obj.stem + ".txt"
        temp_dir = Path(tempfile.mkdtemp(prefix="adb_gui_logcat_"))
        temp_log_path = temp_dir / internal_name

        try:
            header = header_override or self.logcat_cached_header or self.build_fallback_log_header(serial_for_header)
            archive_path_obj.unlink(missing_ok=True)
            with temp_log_path.open("w", encoding="utf-8", errors="replace") as dst:
                dst.write(header)
                with source.open("r", encoding="utf-8", errors="replace") as src:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
            cmd = [
                seven_zip,
                "a",
                "-t7z",
                str(archive_path_obj),
                internal_name,
                "-mx=9",
                "-mmt=on",
                "-y",
            ]
            res = run_quick(cmd, timeout=600, cwd=str(temp_dir))
            if res.returncode != 0:
                details = (res.stdout or "") + ("\n" if res.stdout and res.stderr else "") + (res.stderr or "")
                raise RuntimeError(details.strip() or "7-Zip failed to create the archive.")
            return archive_path_obj
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def create_logcat_archive_from_files(self, archive_path, source_specs, serial_for_header: str, header_override: str = None):
        """Create a .7z archive containing one or more log streams.

        source_specs is a list of dictionaries with:
          - path: source log file path
          - suffix: internal filename suffix, e.g. "unfiltered" or "filtered"
          - title: short stream description written below the export header
        """
        seven_zip = find_7zip_executable()
        if not seven_zip:
            raise RuntimeError("7-Zip was not found. Install 7-Zip or add 7z.exe to PATH before saving logs as .7z.")

        usable_specs = []
        for spec in source_specs or []:
            source = Path(spec.get("path") or "")
            # Keep zero-byte filtered logs in the archive. A filtered log with no
            # matches is still useful because it proves the filter ran in parallel.
            # Unfiltered logs may also be empty in very short sessions; include them
            # when the file exists so the archive structure remains predictable.
            if source.exists():
                usable_specs.append({**spec, "path": source})

        if not usable_specs:
            raise RuntimeError("No log session files were available to archive.")

        archive_path_obj = Path(archive_path)
        archive_path_obj.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix="adb_gui_logcat_"))
        internal_names = []

        try:
            header = header_override or self.logcat_cached_header or self.build_fallback_log_header(serial_for_header)
            archive_path_obj.unlink(missing_ok=True)
            used_names = set()
            for spec in usable_specs:
                suffix = self.sanitize_filename_part(spec.get("suffix", "log"), fallback="log")
                internal_name = f"{archive_path_obj.stem}_{suffix}.txt"
                # Keep internal names unique even if a caller accidentally repeats a suffix.
                if internal_name in used_names:
                    counter = 2
                    base = internal_name[:-4]
                    while f"{base}_{counter}.txt" in used_names:
                        counter += 1
                    internal_name = f"{base}_{counter}.txt"
                used_names.add(internal_name)
                internal_names.append(internal_name)
                temp_log_path = temp_dir / internal_name
                title = spec.get("title") or suffix
                with temp_log_path.open("w", encoding="utf-8", errors="replace") as dst:
                    dst.write(header)
                    dst.write(f"Log stream: {title}\n")
                    dst.write("-" * 80 + "\n")
                    with spec["path"].open("r", encoding="utf-8", errors="replace") as src:
                        shutil.copyfileobj(src, dst, length=1024 * 1024)

            cmd = [
                seven_zip,
                "a",
                "-t7z",
                str(archive_path_obj),
                *internal_names,
                "-mx=9",
                "-mmt=on",
                "-y",
            ]
            res = run_quick(cmd, timeout=600, cwd=str(temp_dir))
            if res.returncode != 0:
                details = (res.stdout or "") + ("\n" if res.stdout and res.stderr else "") + (res.stderr or "")
                raise RuntimeError(details.strip() or "7-Zip failed to create the archive.")
            return archive_path_obj
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def build_logcat_source_specs(self, full_source_path=None, filtered_source_path=None, filter_description=None):
        specs = []
        if full_source_path:
            specs.append({
                "path": full_source_path,
                "suffix": "unfiltered",
                "title": "Full unfiltered ADB output captured before host-side filtering",
            })
        if filtered_source_path:
            description = filter_description or self.logcat_filter_description or "host-side filter"
            specs.append({
                "path": filtered_source_path,
                "suffix": "filtered",
                "title": f"Host-filtered output ({description})",
            })
        return specs

    def auto_save_logcat_session(self, serial: str, reason: str = "disconnect"):
        content = self.logcat_output.get("1.0", tk.END)
        if not content or not content.strip():
            return None

        archive_path = self.build_automatic_logcat_archive_path(serial, suffix=f"logcat_{reason}")
        header = self.logcat_cached_header or self.build_fallback_log_header(serial)
        self.create_logcat_archive(archive_path, content, serial_for_header=serial, header_override=header)
        return archive_path

    def auto_save_logcat_session_async(self, serial: str, reason: str, content: str, header: str, source_log_path=None, filtered_source_log_path=None, filter_description=None):
        source = Path(source_log_path) if source_log_path else None
        filtered_source = Path(filtered_source_log_path) if filtered_source_log_path else None
        source_specs = self.build_logcat_source_specs(source, filtered_source, filter_description=filter_description)
        has_sources = any(Path(spec["path"]).exists() and Path(spec["path"]).stat().st_size > 0 for spec in source_specs)
        has_content = bool(content and content.strip())
        if not has_sources and not has_content:
            self.enqueue_logcat_event({"event": "logcat_status", "message": "[auto save] No log content to save."})
            return

        def worker():
            with self.logcat_save_lock:
                try:
                    archive_path = self.build_automatic_logcat_archive_path(serial, suffix=f"logcat_{reason}")
                    if has_sources:
                        self.create_logcat_archive_from_files(archive_path, source_specs, serial_for_header=serial, header_override=header)
                    else:
                        self.create_logcat_archive(archive_path, content, serial_for_header=serial, header_override=header)
                    pretty_reason = str(reason or "disconnect").replace("_", " ")
                    self.enqueue_logcat_event({"event": "logcat_status", "message": f"[auto save] Saved {pretty_reason} session to: {archive_path}"})
                except Exception as exc:
                    self.enqueue_logcat_event({"event": "logcat_status", "message": f"[auto save failed] {exc}"})
                finally:
                    if source and source == Path(self.logcat_session_file or ""):
                        self.logcat_session_file = None
                    if filtered_source and filtered_source == Path(self.logcat_filtered_session_file or ""):
                        self.logcat_filtered_session_file = None
                    for path in (source, filtered_source):
                        try:
                            if path:
                                path.unlink(missing_ok=True)
                        except Exception:
                            pass

        threading.Thread(target=worker, daemon=True).start()

    def collect_device_log_header(self, serial: str) -> str:
        serial = (serial or "").strip() or "unknown_device"
        fields = [
            ("ro.product.manufacturer", "Manufacturer"),
            ("ro.product.brand", "Brand"),
            ("ro.product.model", "Model"),
            ("ro.product.device", "Device"),
            ("ro.product.name", "Product"),
            ("ro.build.version.release", "Android"),
            ("ro.build.version.sdk", "SDK"),
            ("ro.build.id", "Build ID"),
            ("ro.build.fingerprint", "Build Fingerprint"),
        ]

        lines = [
            "=" * 80,
            "ADB GUI Log Export",
            "=" * 80,
            f"Host export time: {datetime.now().isoformat(timespec='seconds')}",
            f"Device serial: {serial}",
        ]

        for prop, label in fields:
            try:
                value = self.manager.run_adb("shell", "getprop", prop, serial=serial, timeout=30).strip()
            except Exception as exc:
                value = f"<unavailable: {exc}>"
            lines.append(f"{label}: {value or '<empty>'}")

        try:
            battery = self.manager.run_adb("shell", "dumpsys", "battery", serial=serial, timeout=30)
            level = next((line.split(":", 1)[1].strip() for line in battery.splitlines() if line.strip().startswith("level:")), "unknown")
            status = next((line.split(":", 1)[1].strip() for line in battery.splitlines() if line.strip().startswith("status:")), "unknown")
            lines.append(f"Battery level: {level}")
            lines.append(f"Battery status: {status}")
        except Exception as exc:
            lines.append(f"Battery info: <unavailable: {exc}>")

        lines.extend([
            "-" * 80,
            "Log contents",
            "-" * 80,
            "",
        ])
        return "\n".join(lines)

    @staticmethod
    def format_logcat_line_with_timestamp(line: str, enabled: bool) -> str:
        if line is None:
            return ""
        line = str(line)
        if not enabled or not line:
            return line
        if line.endswith('\n'):
            core = line[:-1]
            newline = '\n'
        else:
            core = line
            newline = ''
        if not core:
            return line
        stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        return f"[{stamp}] {core}{newline}"

    def format_logcat_line(self, line: str) -> str:
        return self.format_logcat_line_with_timestamp(line, self.logcat_timestamp_var.get())

    @staticmethod
    def build_logcat_filter_plan(filter_text: str):
        """Return (adb_args, local_filter, description) for the Logcat filter box.

        Older builds appended everything typed in the filter box to `adb logcat`.
        That makes simple text searches such as `gps` behave like a logcat
        filter-spec argument instead of a grep-style search, so it appears as
        if filtering is broken. This planner keeps advanced logcat arguments
        available while making the normal case a host-side filter that is
        independent of Android shell `grep` availability.
        """
        text = (filter_text or "").strip()
        if not text:
            return [], None, "no filter"

        lowered = text.lower()
        if lowered.startswith(("adb:", "args:")):
            raw = text.split(":", 1)[1].strip()
            return split_user_args(raw), None, f"adb logcat args: {raw}"

        if lowered.startswith("regex:"):
            pattern = text.split(":", 1)[1].strip()
            if not pattern:
                raise ValueError("regex: filter requires a pattern.")
            compiled = re.compile(pattern, re.IGNORECASE)
            return [], {"mode": "regex", "pattern": pattern, "compiled": compiled}, f"host regex: {pattern}"

        if lowered.startswith(("text:", "contains:")):
            needle = text.split(":", 1)[1].strip()
            if not needle:
                raise ValueError("text: filter requires search text.")
            return [], {"mode": "text", "text": needle.lower(), "display": needle}, f"host text: {needle}"

        # Pass through explicit logcat options/filter-specs. Examples:
        #   -v time -b main
        #   MyTag:D *:S
        # For ordinary words such as gps, location, camera, etc., use a local
        # text filter so it works like users expect from grep/search.
        tokens = split_user_args(text)
        priority_spec = re.compile(r"^(?:\*|[A-Za-z0-9_.\-$]+):[VDIWEFSvdiewfs]$")
        looks_like_adb_filter = bool(tokens) and (
            tokens[0].startswith("-") or any(priority_spec.match(tok) for tok in tokens)
        )
        if looks_like_adb_filter:
            return tokens, None, f"adb logcat args: {text}"

        return [], {"mode": "text", "text": text.lower(), "display": text}, f"host text: {text}"

    @staticmethod
    def logcat_line_matches_filter(line: str, local_filter) -> bool:
        if not local_filter:
            return True
        mode = local_filter.get("mode")
        if mode == "regex":
            return local_filter["compiled"].search(line) is not None
        if mode == "text":
            return local_filter["text"] in line.lower()
        return True

    def new_logcat_session_file(self, serial: str, kind: str = "unfiltered") -> Path:
        safe_serial = self.sanitize_filename_part(serial, fallback="unknown_device")
        safe_kind = self.sanitize_filename_part(kind, fallback="log")
        temp_dir = Path(tempfile.gettempdir())
        path = temp_dir / f"adb_gui_{safe_serial}_{safe_kind}_{os.getpid()}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.log"
        path.write_text("", encoding="utf-8")
        return path

    def cleanup_logcat_session_file(self):
        paths = [self.logcat_session_file, self.logcat_filtered_session_file]
        self.logcat_session_file = None
        self.logcat_filtered_session_file = None
        self.logcat_filter_text = ""
        for path in paths:
            if path:
                try:
                    Path(path).unlink(missing_ok=True)
                except Exception:
                    pass


    # ---------- persistent settings ----------
    @staticmethod
    def default_settings_path() -> Path:
        if is_windows():
            base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / SETTINGS_APP_DIR_NAME
        else:
            base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "adb-control-center"
        return base / "settings.json"

    def load_settings(self) -> dict:
        path = self.default_settings_path()
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {"schema_version": SETTINGS_SCHEMA_VERSION}

    def save_current_settings(self):
        if not getattr(self, "_settings_ready", False):
            return
        try:
            data = dict(getattr(self, "settings", {}) or {})
            data["schema_version"] = SETTINGS_SCHEMA_VERSION
            data["app_version"] = APP_VERSION
            data["window_geometry"] = self.geometry()
            data["last_selected_device_serial"] = self._selected_serial_value_or_empty()
            if hasattr(self, "apk_path_var"):
                data["apk_path"] = self.apk_path_var.get()
            if hasattr(self, "local_path_var"):
                data["local_path"] = self.local_path_var.get()
            if hasattr(self, "remote_path_var"):
                data["remote_path"] = self.remote_path_var.get()
            if hasattr(self, "remote_browser_path_var"):
                data["remote_browser_path"] = self.remote_browser_path_var.get()
            if hasattr(self, "installer_adb_dir_var"):
                data["installer_adb_dir"] = self.installer_adb_dir_var.get()
            if hasattr(self, "tcpip_port_var"):
                data["tcpip_port"] = self.tcpip_port_var.get()
            if hasattr(self, "logcat_filter_var"):
                data["logcat_filter"] = self.logcat_filter_var.get()
            if hasattr(self, "logcat_timestamp_var"):
                data["logcat_timestamp"] = bool(self.logcat_timestamp_var.get())
            if hasattr(self, "logcat_auto_reconnect_var"):
                data["logcat_auto_reconnect"] = bool(self.logcat_auto_reconnect_var.get())
            path = self.default_settings_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
            temp_path.replace(path)
            self.settings = data
        except Exception as exc:
            try:
                self.append_dashboard_output("Save Settings", f"Could not save settings: {exc}")
            except Exception:
                pass

    def schedule_settings_save(self, delay_ms=600):
        if self._closing or not getattr(self, "_settings_ready", False):
            return
        try:
            if self._settings_save_after_id:
                self.after_cancel(self._settings_save_after_id)
        except Exception:
            pass
        try:
            self._settings_save_after_id = self.after(delay_ms, self.save_current_settings)
        except Exception:
            pass

    def _bind_settings_autosave(self):
        traced = [
            getattr(self, "apk_path_var", None),
            getattr(self, "local_path_var", None),
            getattr(self, "remote_path_var", None),
            getattr(self, "remote_browser_path_var", None),
            getattr(self, "installer_adb_dir_var", None),
            getattr(self, "tcpip_port_var", None),
            getattr(self, "logcat_filter_var", None),
            getattr(self, "logcat_timestamp_var", None),
            getattr(self, "logcat_auto_reconnect_var", None),
        ]
        for var in [v for v in traced if v is not None]:
            try:
                var.trace_add("write", lambda *_: self.schedule_settings_save())
            except Exception:
                pass
        try:
            self.bind("<Configure>", self._on_configure_for_settings, add="+")
        except Exception:
            pass

    def _on_configure_for_settings(self, event=None):
        # Only save the root window geometry, not every child widget configure event.
        if event is not None and getattr(event, "widget", None) is not self:
            return
        self.schedule_settings_save(delay_ms=1200)

    def _selected_serial_value_or_empty(self) -> str:
        try:
            value = self.selected_serial.get().strip()
            return value.split(" | ")[0] if value else ""
        except Exception:
            return ""

    def _on_device_combo_selected(self, _event=None):
        self.schedule_settings_save(delay_ms=100)

    # ---------- progress/cancel operations ----------
    def _create_operation_progress_area(self, parent):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=(8, 0))
        label_var = tk.StringVar(value="No long operation running.")
        ttk.Label(frame, textvariable=label_var).pack(side="left")
        bar = ttk.Progressbar(frame, mode="indeterminate", length=180)
        bar.pack(side="left", padx=8)
        cancel_btn = ttk.Button(frame, text="Cancel", command=self.cancel_active_operation, state="disabled")
        cancel_btn.pack(side="left")
        frame._progress_bar = bar
        frame._cancel_button = cancel_btn
        return frame, label_var

    def _set_progress_area(self, frame, var, running: bool, text: str):
        try:
            var.set(text)
            if running:
                frame._progress_bar.start(12)
                frame._cancel_button.configure(state="normal")
            else:
                frame._progress_bar.stop()
                frame._cancel_button.configure(state="disabled")
        except Exception:
            pass

    def cancel_active_operation(self):
        self._active_operation_cancel_requested = True
        proc = self._active_operation_proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self.set_status("Cancel requested")

    def run_cancellable_adb_operation(self, label, adb_args, serial=None, timeout=1200, widget=None, progress_frame=None, progress_var=None, on_success=None):
        target_widget = widget if widget is not None else self.dashboard_output
        if self._active_operation_proc is not None and self._active_operation_proc.poll() is None:
            messagebox.showwarning(APP_TITLE, "Another cancellable ADB operation is already running. Cancel it or wait for it to finish first.")
            return

        progress_frame = progress_frame or getattr(self, "files_progress_frame", None)
        progress_var = progress_var or getattr(self, "files_progress_var", None)
        self._active_operation_cancel_requested = False
        if progress_frame and progress_var:
            self._set_progress_area(progress_frame, progress_var, True, f"Running: {label}")

        def worker():
            output = ""
            try:
                self.ui_call(lambda l=label: self.set_status(f"Running: {l}"))
                self.ui_call(lambda l=label: self.append_text(target_widget, f"\n>>> Running: {l}"))
                cmd = self.manager.adb_cmd(*adb_args, serial=serial)
                creationflags = subprocess.CREATE_NO_WINDOW if is_windows() and hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                )
                self._active_operation_proc = proc
                deadline = time.monotonic() + timeout if timeout else None
                while True:
                    try:
                        out, _ = proc.communicate(timeout=0.25)
                        output += out or ""
                        break
                    except subprocess.TimeoutExpired:
                        if self._active_operation_cancel_requested:
                            try:
                                proc.terminate()
                                proc.wait(timeout=5)
                            except Exception:
                                try:
                                    proc.kill()
                                except Exception:
                                    pass
                            raise RuntimeError("Operation canceled by user.")
                        if deadline and time.monotonic() > deadline:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                            raise RuntimeError(f"Operation timed out after {timeout} seconds.")
                if proc.returncode != 0:
                    raise RuntimeError(output.strip() or f"ADB operation failed with exit code {proc.returncode}.")
                extra = ""
                if on_success:
                    extra_result = on_success(output)
                    extra = str(extra_result or "")
                final_output = output.strip() or "Command completed with no output."
                if extra.strip():
                    final_output += "\n" + extra.strip()
                self.ui_call(lambda o=final_output: self.append_text(target_widget, o))
                if target_widget is not self.dashboard_output:
                    self.ui_call(lambda l=label, o=final_output: self.append_dashboard_output(l, o))
                self.ui_call(lambda: self.set_status("Ready"))
            except Exception as exc:
                self.ui_call(lambda: self.set_status("Error"))
                self.ui_call(lambda e=exc: self.append_text(target_widget, f"ERROR: {e}"))
                if target_widget is not self.dashboard_output:
                    self.ui_call(lambda l=label, e=exc: self.append_dashboard_output(l, f"ERROR: {e}"))
                self.ui_call(lambda e=exc: messagebox.showerror(APP_TITLE, str(e)))
            finally:
                self._active_operation_proc = None
                self._active_operation_cancel_requested = False
                if progress_frame and progress_var:
                    self.ui_call(lambda: self._set_progress_area(progress_frame, progress_var, False, "No long operation running."))
                self.ui_call(lambda: self.schedule_settings_save(delay_ms=100))

        threading.Thread(target=worker, daemon=True).start()

    # ---------- helpers ----------
    def set_status(self, text):
        if self._closing:
            return
        try:
            self.status_var.set(text)
        except tk.TclError:
            pass

    def ui_call(self, func):
        if self._closing:
            return
        try:
            self.after(0, func)
        except tk.TclError:
            pass

    def clear_text(self, widget):
        if self._closing:
            return
        try:
            widget.delete("1.0", tk.END)
        except tk.TclError:
            pass

    def clear_logcat_output(self):
        self.clear_text(self.logcat_output)
        self.logcat_ui_line_count = 0

    def clear_visible_logcat_only(self):
        # Clear only the visible Text widget. The active session spool file remains
        # untouched, so Save still exports the complete capture.
        self.clear_logcat_output()
        self.set_status("Visible log cleared")

    def is_logcat_running(self):
        return bool(self.logcat_process and self.logcat_process.poll() is None)

    def append_text(self, widget, text):
        if self._closing:
            return
        try:
            text = "" if text is None else str(text)
            widget.insert(tk.END, text + ("\n" if not text.endswith("\n") else ""))
            widget.see(tk.END)
        except tk.TclError:
            pass

    def append_dashboard_output(self, label, text="", command=None):
        if self._closing or not hasattr(self, "dashboard_output"):
            return
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        parts = [f"[{timestamp}] {label}"]
        if command:
            parts.append(f"$ {command}")
        if text:
            parts.append(str(text).rstrip())
        parts.append("-" * 72)
        self.append_text(self.dashboard_output, "\n".join(parts))

    def _note_dropped_logcat_ui_line(self, count=1):
        with self.logcat_queue_lock:
            self.logcat_ui_dropped_lines += count

    def enqueue_logcat_line(self, line):
        # The session spool file already stores every line. The UI queue is
        # bounded so a very noisy device cannot consume unlimited RAM or make
        # Tkinter spend minutes catching up with old visible output.
        try:
            self.logcat_queue.put_nowait(line)
        except queue.Full:
            self._note_dropped_logcat_ui_line(1)

    def enqueue_logcat_event(self, event):
        # Important events such as process exit/reconnect must not be lost just
        # because the visible-log queue is full of ordinary log lines. Make room
        # by dropping old UI-only entries; the full log remains in the spool file.
        for _ in range(1000):
            try:
                self.logcat_queue.put_nowait(event)
                return
            except queue.Full:
                try:
                    self.logcat_queue.get_nowait()
                    self._note_dropped_logcat_ui_line(1)
                except queue.Empty:
                    break
        try:
            self.logcat_queue.put_nowait(event)
        except queue.Full:
            # Last-resort fallback: avoid blocking worker threads forever.
            pass

    def append_logcat_batch(self, lines):
        if self._closing or not lines:
            return
        try:
            text = "".join(self.format_logcat_line(line) for line in lines)
            if not text:
                return
            self.logcat_output.insert(tk.END, text)
            self.logcat_ui_line_count += max(1, text.count("\n"))
            if self.logcat_ui_line_count > LOGCAT_UI_MAX_LINES:
                delete_count = max(0, self.logcat_ui_line_count - LOGCAT_UI_TRIM_TO_LINES)
                if delete_count > 0:
                    self.logcat_output.delete("1.0", f"{delete_count + 1}.0")
                    self.logcat_ui_line_count = LOGCAT_UI_TRIM_TO_LINES
            self.logcat_output.see(tk.END)
        except tk.TclError:
            pass

    def get_selected_serial(self):
        serial = self.selected_serial.get().strip()
        if not serial:
            raise RuntimeError("No device selected.")
        return serial.split(" | ")[0]

    def _device_values(self):
        values = []
        for d in self.devices:
            label = f"{d['serial']} | {d['state']}"
            if d["model"]:
                label += f" | {d['model']}"
            values.append(label)
        return values

    def run_background_action(self, label, func, *args, widget=None):
        target_widget = widget if widget is not None else self.dashboard_output

        def worker():
            try:
                self.ui_call(lambda l=label: self.set_status(f"Running: {l}"))
                self.ui_call(lambda l=label: self.append_text(target_widget, f"\n>>> Running: {l}"))
                result = func(*args)
                output = str(result).rstrip() if result is not None else ""
                if output:
                    self.ui_call(lambda o=output: self.append_text(target_widget, o))
                else:
                    self.ui_call(lambda: self.append_text(target_widget, "Command completed with no output."))
                if target_widget is not self.dashboard_output:
                    self.ui_call(lambda l=label, o=output: self.append_dashboard_output(l, o or "Command completed with no output."))
                self.ui_call(lambda: self.set_status("Ready"))
            except Exception as exc:
                self.ui_call(lambda: self.set_status("Error"))
                self.ui_call(lambda e=exc: self.append_text(target_widget, f"ERROR: {e}"))
                if target_widget is not self.dashboard_output:
                    self.ui_call(lambda l=label, e=exc: self.append_dashboard_output(l, f"ERROR: {e}"))
                self.ui_call(lambda e=exc: messagebox.showerror(APP_TITLE, str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def ensure_device(self):
        self.manager.require_adb()
        return self.get_selected_serial()

    def require_selected_device(self):
        try:
            return self.ensure_device()
        except Exception as exc:
            self.set_status("Error")
            messagebox.showerror(APP_TITLE, str(exc))
            return None

    # ---------- menu actions ----------
    def show_about(self):
        about = tk.Toplevel(self)
        about.title(f"About {APP_TITLE}")
        about.transient(self)
        about.resizable(False, False)
        about.grab_set()

        frame = ttk.Frame(about, padding=16)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=APP_TITLE, font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(frame, text=f"Version: {APP_VERSION}").grid(row=1, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text=f"Release date: {APP_RELEASE_DATE}").grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ttk.Label(frame, text="A desktop GUI for common Android Debug Bridge (ADB) workflows on Windows.", wraplength=460).grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 12))

        ttk.Label(frame, text="Credits", font=("Segoe UI", 10, "bold")).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Label(frame, text=f"{AUTHOR_NAME} ({AUTHOR_ALIAS})").grid(row=5, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, text=f"E-mail: {AUTHOR_EMAIL}").grid(row=6, column=0, columnspan=2, sticky="w")
        github_label = ttk.Label(frame, text=f"GitHub: {AUTHOR_GITHUB}", foreground="blue", cursor="hand2")
        github_label.grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 12))
        github_label.bind("<Button-1>", lambda _event: webbrowser.open(AUTHOR_GITHUB))

        ttk.Label(
            frame,
            text="Built to simplify device management, installers, file transfers, logging, reconnect handling, and common day-to-day ADB tasks.",
            wraplength=460,
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(0, 14))

        def copy_email():
            self.clipboard_clear()
            self.clipboard_append(AUTHOR_EMAIL)
            self.set_status("E-mail copied to clipboard")

        ttk.Button(frame, text="Open GitHub", command=lambda: webbrowser.open(AUTHOR_GITHUB)).grid(row=9, column=0, sticky="w")
        ttk.Button(frame, text="Copy E-mail", command=copy_email).grid(row=9, column=0, padx=(110, 0), sticky="w")
        ttk.Button(frame, text="Close", command=about.destroy).grid(row=9, column=1, sticky="e")

        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=0)

        about.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() // 2) - (about.winfo_width() // 2)
        y = self.winfo_rooty() + (self.winfo_height() // 2) - (about.winfo_height() // 2)
        about.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        about.wait_window()

    def browse_platform_tools_dir(self):
        initial = self.installer_adb_dir_var.get().strip() or DEFAULT_INSTALL_DIR
        path = filedialog.askdirectory(initialdir=initial, mustexist=False, title="Choose Platform-Tools install folder")
        if path:
            self.installer_adb_dir_var.set(path)

    def install_adb_from_tab(self):
        install_dir = self.installer_adb_dir_var.get().strip() or DEFAULT_INSTALL_DIR
        if not install_dir:
            messagebox.showwarning(APP_TITLE, "Choose an install folder first.")
            return
        prompt = (
            "Install Android Platform-Tools to:\n\n"
            f"{install_dir}\n\n"
            "Run this app as Administrator if you want to update the system PATH."
        )
        if not messagebox.askyesno(APP_TITLE, prompt):
            return

        self.clear_text(self.installers_output)

        def worker():
            try:
                self.ui_call(lambda: self.set_status("Installing Platform-Tools..."))
                self.manager.install_adb(
                    install_dir=install_dir,
                    progress_cb=lambda msg: self.ui_call(lambda: self.append_text(self.installers_output, msg)),
                )
                version = self.manager.adb_version()
                self.ui_call(lambda: self.append_text(self.installers_output, "\n" + version))
                self.ui_call(lambda: self.set_status("Platform-Tools installed"))
                self.ui_call(lambda: self.installer_adb_status_var.set("Installed"))
                self.ui_call(self.check_adb_status)
                self.ui_call(self.refresh_devices)
            except Exception as exc:
                self.ui_call(lambda e=exc: self.append_text(self.installers_output, f"ERROR: {e}"))
                self.ui_call(lambda: self.set_status("Install failed"))
                self.ui_call(lambda e=exc: messagebox.showerror(APP_TITLE, str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def check_adb_status(self):
        if not hasattr(self, "installers_output"):
            return
        self.installer_adb_status_var.set("Checking ADB...")

        def worker():
            self.manager.adb_path = self.manager.find_adb()
            adb_path = self.manager.adb_path
            if adb_path:
                try:
                    version = self.manager.adb_version()
                    version_line = version.splitlines()[0].strip() if version else "ADB detected"
                    return {"status": f"Installed: {version_line}", "messages": [f"ADB detected: {adb_path}", version or ""]}
                except Exception as exc:
                    return {"status": f"ADB found but not working: {adb_path}", "messages": [f"ADB executable found but version check failed: {adb_path}", f"ERROR: {exc}"]}
            return {"status": "ADB not found", "messages": ["ADB not found in PATH or the common install folders."]}

        def bg():
            try:
                result = worker()
                self.ui_call(lambda r=result: self.installer_adb_status_var.set(r["status"]))
                for message in result["messages"]:
                    if message:
                        self.ui_call(lambda m=message: self.append_text(self.installers_output, m))
            except Exception as exc:
                self.ui_call(lambda e=exc: self.installer_adb_status_var.set("ADB check failed"))
                self.ui_call(lambda e=exc: self.append_text(self.installers_output, f"ERROR: {e}"))

        threading.Thread(target=bg, daemon=True).start()

    def check_python_status(self):
        if not hasattr(self, "installers_output"):
            return
        self.python_status_var.set("Checking Python...")

        def bg():
            try:
                exe, version_text = self.manager.detect_python()
                if exe and version_text:
                    self.ui_call(lambda vt=version_text: self.python_status_var.set(vt))
                    self.ui_call(lambda vt=version_text, e=exe: self.append_text(self.installers_output, f"Python detected: {vt} ({e})"))
                else:
                    self.ui_call(lambda: self.python_status_var.set("Python not found"))
                    self.ui_call(lambda: self.append_text(self.installers_output, "Python not found in PATH."))
            except Exception as exc:
                self.ui_call(lambda e=exc: self.python_status_var.set("Python check failed"))
                self.ui_call(lambda e=exc: self.append_text(self.installers_output, f"ERROR: {e}"))

        threading.Thread(target=bg, daemon=True).start()

    def install_python_from_tab(self):
        prompt = (
            "Download and run the latest official Python 3 64-bit Windows installer from python.org?\n\n"
            "This installs Python for the current user and requests PATH update."
        )
        if not messagebox.askyesno(APP_TITLE, prompt):
            return

        def worker():
            try:
                self.ui_call(lambda: self.set_status("Installing Python..."))
                self.ui_call(lambda: self.append_text(self.installers_output, "Resolving latest Python installer from python.org..."))
                result = self.manager.install_python_latest(
                    progress_cb=lambda msg: self.ui_call(lambda: self.append_text(self.installers_output, msg))
                )
                exe, version_text = self.manager.detect_python()
                final_text = version_text or result
                self.ui_call(lambda: self.python_status_var.set(final_text))
                if exe:
                    self.ui_call(lambda: self.append_text(self.installers_output, f"Python executable: {exe}"))
                self.ui_call(lambda: self.set_status("Python installed"))
            except Exception as exc:
                self.ui_call(lambda e=exc: self.append_text(self.installers_output, f"ERROR: {e}"))
                self.ui_call(lambda: self.set_status("Python install failed"))
                self.ui_call(lambda e=exc: messagebox.showerror(APP_TITLE, str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def check_7zip_status(self):
        if not hasattr(self, "installers_output"):
            return
        self.seven_zip_status_var.set("Checking 7-Zip...")

        def bg():
            try:
                exe, version_text = self.manager.detect_7zip()
                if exe and version_text:
                    self.ui_call(lambda vt=version_text: self.seven_zip_status_var.set(vt))
                    self.ui_call(lambda vt=version_text, e=exe: self.append_text(self.installers_output, f"7-Zip detected: {vt} ({e})"))
                else:
                    self.ui_call(lambda: self.seven_zip_status_var.set("7-Zip not found"))
                    self.ui_call(lambda: self.append_text(self.installers_output, "7-Zip not found in PATH or common install locations."))
            except Exception as exc:
                self.ui_call(lambda e=exc: self.seven_zip_status_var.set("7-Zip check failed"))
                self.ui_call(lambda e=exc: self.append_text(self.installers_output, f"ERROR: {e}"))

        threading.Thread(target=bg, daemon=True).start()

    def install_7zip_from_tab(self):
        prompt = (
            "Download and run the latest official 7-Zip 64-bit Windows installer from 7-zip.org?\n\n"
            "The installer will run silently using the vendor-supported silent install option."
        )
        if not messagebox.askyesno(APP_TITLE, prompt):
            return

        def worker():
            try:
                self.ui_call(lambda: self.set_status("Installing 7-Zip..."))
                self.ui_call(lambda: self.append_text(self.installers_output, "Resolving latest 7-Zip installer from 7-zip.org..."))
                result = self.manager.install_7zip_latest(
                    progress_cb=lambda msg: self.ui_call(lambda: self.append_text(self.installers_output, msg))
                )
                exe, version_text = self.manager.detect_7zip()
                final_text = version_text or result
                self.ui_call(lambda: self.seven_zip_status_var.set(final_text))
                if exe:
                    self.ui_call(lambda: self.append_text(self.installers_output, f"7-Zip executable: {exe}"))
                self.ui_call(lambda: self.set_status("7-Zip installed"))
            except Exception as exc:
                self.ui_call(lambda e=exc: self.append_text(self.installers_output, f"ERROR: {e}"))
                self.ui_call(lambda: self.set_status("7-Zip install failed"))
                self.ui_call(lambda e=exc: messagebox.showerror(APP_TITLE, str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def check_adb(self):
        def worker():
            return self.manager.adb_version()

        def bg():
            try:
                self.ui_call(lambda: self.set_status("Checking ADB..."))
                version = worker()
                self.ui_call(lambda: self.clear_text(self.adb_status_text))
                self.ui_call(lambda v=version: self.append_text(self.adb_status_text, v))
                self.ui_call(lambda v=version: self.append_dashboard_output("Check ADB", v, command="adb version"))
                self.ui_call(lambda: self.set_status("ADB is available"))
            except Exception as exc:
                self.ui_call(lambda: self.clear_text(self.adb_status_text))
                self.ui_call(lambda e=exc: self.append_text(self.adb_status_text, f"ADB not available: {e}"))
                self.ui_call(lambda e=exc: self.append_dashboard_output("Check ADB", f"ADB not available: {e}", command="adb version"))
                self.ui_call(lambda: self.set_status("ADB not available"))

        threading.Thread(target=bg, daemon=True).start()

    def install_adb(self):
        if not messagebox.askyesno(APP_TITLE, f"Install ADB to {DEFAULT_INSTALL_DIR}?\n\nRun this window as Administrator so PATH can be updated."):
            return

        self.clear_text(self.adb_status_text)

        def worker():
            try:
                self.ui_call(lambda: self.set_status("Installing ADB..."))
                self.manager.install_adb(progress_cb=lambda msg: self.ui_call(lambda: self.append_text(self.adb_status_text, msg)))
                version = self.manager.adb_version()
                self.ui_call(lambda: self.append_text(self.adb_status_text, "\n" + version))
                self.ui_call(lambda: self.set_status("ADB installed"))
            except Exception as exc:
                self.ui_call(lambda e=exc: self.append_text(self.adb_status_text, f"ERROR: {e}"))
                self.ui_call(lambda: self.set_status("Install failed"))
                self.ui_call(lambda e=exc: messagebox.showerror(APP_TITLE, str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _start_server(self):
        return self.manager.run_adb("start-server") or "ADB server started."

    def _kill_server(self):
        return self.manager.run_adb("kill-server") or "ADB server stopped."

    def export_device_info(self):
        serial = self.require_selected_device()
        if not serial:
            return

        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")], initialfile=f"device_info_{serial}.json")
        if not path:
            return

        def worker():
            getprop = self.manager.run_adb("shell", "getprop", serial=serial, timeout=120)
            battery = self.manager.run_adb("shell", "dumpsys", "battery", serial=serial, timeout=120)
            data = {
                "serial": serial,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "getprop": getprop,
                "battery": battery,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return f"Saved device info to: {path}"

        self.run_background_action("export device info", worker, widget=self.dashboard_output)

    # ---------- device/dashboard ----------
    def refresh_devices(self):
        def worker():
            self.manager.require_adb()
            version_text = ""
            version_error = ""
            try:
                version_text = self.manager.adb_version()
            except Exception as exc:
                version_error = str(exc)
            devices = self.manager.list_devices()
            return devices, version_text, version_error

        def on_done(result):
            devices, version_text, version_error = result
            previous_serial = ""
            current_value = self.selected_serial.get().strip()
            if current_value:
                previous_serial = current_value.split(" | ")[0]
            if not previous_serial:
                previous_serial = self.settings.get("last_selected_device_serial", "")
            self.devices = devices
            values = self._device_values()
            self.device_combo["values"] = values
            if values:
                matched = next((v for v in values if v.split(" | ")[0] == previous_serial), values[0])
                self.selected_serial.set(matched)
                self.schedule_settings_save(delay_ms=100)
            else:
                self.selected_serial.set("")
            self.set_status(f"Devices: {len(values)}")
            self.clear_text(self.adb_status_text)
            if version_text:
                self.append_text(self.adb_status_text, version_text)
            elif version_error:
                self.append_text(self.adb_status_text, f"ADB issue: {version_error}")
            self.append_text(self.adb_status_text, "")
            dashboard_lines = []
            if version_text:
                dashboard_lines.append(version_text)
            elif version_error:
                dashboard_lines.append(f"ADB issue: {version_error}")
            dashboard_lines.append("")
            if devices:
                for d in devices:
                    line = f"{d['serial']} | {d['state']} | {d['model']} {d['meta']}"
                    self.append_text(self.adb_status_text, line)
                    dashboard_lines.append(line)
                if not self.remote_tree.get_children():
                    self.after(100, self.refresh_remote_files)
            else:
                self.append_text(self.adb_status_text, "No devices detected.")
                dashboard_lines.append("No devices detected.")
                self.remote_tree.delete(*self.remote_tree.get_children())
                self.remote_entries = []
            self.append_dashboard_output("Refresh Devices", "\n".join(dashboard_lines), command="adb devices -l")

        def bg():
            try:
                self.ui_call(lambda: self.set_status("Refreshing devices..."))
                result = worker()
                self.ui_call(lambda r=result: on_done(r))
            except Exception as exc:
                self.ui_call(lambda: self.set_status("ADB not ready"))
                self.ui_call(lambda: self.clear_text(self.adb_status_text))
                self.ui_call(lambda e=exc: self.append_text(self.adb_status_text, f"ADB not available: {e}"))
                self.ui_call(lambda e=exc: self.append_dashboard_output("Refresh Devices", f"ADB not available: {e}", command="adb devices -l"))
                def clear_device_state():
                    self.devices = []
                    self.device_combo.configure(values=[])
                    self.selected_serial.set("")
                    self.remote_tree.delete(*self.remote_tree.get_children())
                    self.remote_entries = []
                self.ui_call(clear_device_state)

        threading.Thread(target=bg, daemon=True).start()

    def load_device_info(self):
        serial = self.require_selected_device()
        if not serial:
            return

        def worker():
            fields = {
                "ro.product.manufacturer": "Manufacturer",
                "ro.product.model": "Model",
                "ro.product.device": "Device",
                "ro.build.version.release": "Android",
                "ro.build.version.sdk": "SDK",
                "ro.serialno": "Serial",
            }
            result = []
            for prop, label in fields.items():
                value = self.manager.run_adb("shell", "getprop", prop, serial=serial)
                result.append(f"{label}: {value}")
            battery = self.manager.run_adb("shell", "dumpsys", "battery", serial=serial)
            level = next((line.split(":", 1)[1].strip() for line in battery.splitlines() if line.strip().startswith("level:")), "unknown")
            status = next((line.split(":", 1)[1].strip() for line in battery.splitlines() if line.strip().startswith("status:")), "unknown")
            result.append(f"Battery level: {level}")
            result.append(f"Battery status: {status}")
            return "\n".join(result)

        def done(text):
            self.clear_text(self.device_info_text)
            self.append_text(self.device_info_text, text)
            self.append_dashboard_output(f"Device Info: {serial}", text, command="adb shell getprop / dumpsys battery")

        def bg():
            try:
                self.ui_call(lambda: self.set_status("Loading device info..."))
                text = worker()
                self.ui_call(lambda: done(text))
                self.ui_call(lambda: self.set_status("Ready"))
            except Exception as exc:
                self.ui_call(lambda e=exc: messagebox.showerror(APP_TITLE, str(e)))
                self.ui_call(lambda: self.set_status("Error"))

        threading.Thread(target=bg, daemon=True).start()

    def run_adb_reboot_no_hang(self, serial: str, mode: str, timeout: int = 12) -> str:
        args = ["reboot"] if mode == "reboot" else ["reboot", mode]
        cmd = self.manager.adb_cmd(*args, serial=serial)
        creationflags = 0
        if is_windows() and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        try:
            output, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            return f"Reboot command sent to {serial}. ADB did not exit within {timeout}s, so the helper process was killed to keep the GUI responsive."
        output = (output or "").strip()
        if proc.returncode not in (0, None):
            # Reboot often disconnects the device before adb prints a clean success.
            # Treat the command as sent, but keep the diagnostic text visible.
            if output:
                return f"Reboot command sent to {serial}. ADB returned {proc.returncode}:\n{output}"
            return f"Reboot command sent to {serial}. ADB returned {proc.returncode} after device disconnect."
        return output or (f"Reboot command sent to {serial}." if mode == "reboot" else f"Reboot to {mode} command sent to {serial}.")

    def run_adb_root_no_hang(self, serial: str, timeout: int = 12) -> str:
        cmd = self.manager.adb_cmd("root", serial=serial)
        creationflags = 0
        if is_windows() and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )
        try:
            output, _ = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                pass
            return (
                f"ADB root command was sent to {serial}, but adb did not exit within "
                f"{timeout}s. The helper process was killed to keep the GUI responsive. "
                "The device may still be restarting adbd."
            )

        output = (output or "").strip()
        if proc.returncode not in (0, None):
            details = output or "No output returned by adb."
            return f"ADB root returned exit code {proc.returncode} for {serial}:\n{details}"
        return output or f"ADB root command sent to {serial}."

    def device_root(self):
        serial = self.require_selected_device()
        if not serial:
            return

        if self.is_logcat_running() and self.logcat_active_serial == serial:
            self.logcat_expected_reboot_serial = serial
            self.logcat_expected_reboot_mode = "root"
            self.enqueue_logcat_event({
                "event": "logcat_status",
                "message": (
                    f"[logcat] ADB root requested for {serial}; adbd may restart. "
                    "If Logcat disconnects, the current session will be auto-saved "
                    "and the app will try to resume on the same serial."
                ),
            })

        def worker():
            return self.run_adb_root_no_hang(serial)

        self.run_background_action("adb root", worker, widget=self.dashboard_output)

    def device_reboot(self, mode):
        serial = self.require_selected_device()
        if not serial:
            return

        if mode in {"reboot", "recovery", "bootloader"} and self.is_logcat_running() and self.logcat_active_serial == serial:
            self.logcat_expected_reboot_serial = serial
            self.logcat_expected_reboot_mode = mode
            self.enqueue_logcat_event({
                "event": "logcat_status",
                "message": f"[logcat] Reboot mode '{mode}' requested for {serial}; treating the next disconnect as expected and auto-resuming when the same Android serial returns.",
            })

        def worker():
            try:
                return self.run_adb_reboot_no_hang(serial, mode)
            except Exception:
                if serial == self.logcat_expected_reboot_serial:
                    self.logcat_expected_reboot_serial = None
                    self.logcat_expected_reboot_mode = None
                raise

        self.run_background_action(f"reboot {mode}", worker, widget=self.dashboard_output)

    # ---------- shell ----------
    def run_shell_command(self):
        serial = self.require_selected_device()
        if not serial:
            return
        command = self.shell_entry.get().strip()
        if not command:
            messagebox.showwarning(APP_TITLE, "Enter a shell command first.")
            return

        def worker():
            return self.manager.run_adb("shell", command, serial=serial, timeout=120)

        self.run_background_action(f"shell {command}", worker, widget=self.shell_output)

    # ---------- apk ----------
    def browse_apk(self):
        path = filedialog.askopenfilename(filetypes=[("Android Package", "*.apk")])
        if path:
            self.apk_path_var.set(path)

    def install_apk(self):
        serial = self.require_selected_device()
        if not serial:
            return
        apk_path = self.apk_path_var.get().strip()
        if not apk_path or not Path(apk_path).exists():
            messagebox.showwarning(APP_TITLE, "Select a valid APK file.")
            return

        args = ["install"]
        if self.apk_reinstall_var.get():
            args.append("-r")
        if self.apk_grant_var.get():
            args.append("-g")
        args.append(apk_path)
        self.run_cancellable_adb_operation(
            "install APK",
            args,
            serial=serial,
            timeout=1800,
            widget=self.apk_output,
            progress_frame=self.apk_progress_frame,
            progress_var=self.apk_progress_var,
        )

    def uninstall_package(self):
        serial = self.require_selected_device()
        if not serial:
            return
        package = self.uninstall_pkg_var.get().strip()
        if not package:
            sel = self._selected_package_name()
            if sel:
                package = sel
        if not package:
            messagebox.showwarning(APP_TITLE, "Enter or select a package name.")
            return

        def worker():
            return self.manager.run_adb("uninstall", package, serial=serial, timeout=300)

        self.run_background_action(f"uninstall {package}", worker, widget=self.apk_output)

    # ---------- files ----------
    def browse_local_file(self):
        path = filedialog.askopenfilename()
        if path:
            self.local_path_var.set(path)

    def browse_local_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.local_path_var.set(path)

    def push_file(self):
        serial = self.require_selected_device()
        if not serial:
            return
        local_path = self.local_path_var.get().strip()
        remote_path = self.remote_path_var.get().strip()
        if not local_path or not Path(local_path).exists():
            messagebox.showwarning(APP_TITLE, "Choose a valid local file or folder.")
            return
        if not remote_path:
            messagebox.showwarning(APP_TITLE, "Enter a remote path.")
            return

        self.run_cancellable_adb_operation(
            "adb push",
            ["push", local_path, remote_path],
            serial=serial,
            timeout=3600,
            widget=self.files_output,
            progress_frame=self.files_progress_frame,
            progress_var=self.files_progress_var,
        )

    def pull_file(self):
        serial = self.require_selected_device()
        if not serial:
            return
        remote_path = self.remote_path_var.get().strip()
        if not remote_path:
            messagebox.showwarning(APP_TITLE, "Enter a remote path to pull.")
            return
        local_path = filedialog.askdirectory(title="Choose destination folder")
        if not local_path:
            return

        self.run_cancellable_adb_operation(
            "adb pull",
            ["pull", remote_path, local_path],
            serial=serial,
            timeout=3600,
            widget=self.files_output,
            progress_frame=self.files_progress_frame,
            progress_var=self.files_progress_var,
        )

    def remote_go_home(self):
        self.remote_browser_path_var.set("/sdcard/")
        self.remote_go_to_path()

    def remote_go_up(self):
        current = self.remote_browser_path_var.get().strip() or "/sdcard/"
        self.remote_browser_path_var.set(parent_remote_path(current))
        self.remote_go_to_path()

    def remote_go_to_path(self):
        target = self.remote_browser_path_var.get().strip() or self.remote_path_var.get().strip() or "/sdcard/"
        self.remote_browser_path_var.set(target)
        self.refresh_remote_files()

    def refresh_remote_files(self):
        serial = self.require_selected_device()
        if not serial:
            return
        requested_path = self.remote_browser_path_var.get().strip() or self.remote_path_var.get().strip() or "/sdcard/"

        def worker():
            return self.manager.list_remote_dir(requested_path, serial=serial, timeout=180)

        def done(result):
            current_path, entries = result
            self.remote_entries = entries
            self.remote_tree.delete(*self.remote_tree.get_children())
            for index, entry in enumerate(entries):
                icon = "📁" if entry["is_dir"] else "📄"
                item_id = f"item_{index}"
                self.remote_tree.insert("", "end", iid=item_id, text=f"{icon} {entry['name']}", values=("Directory" if entry["is_dir"] else "File", entry["path"]))
            self.remote_browser_path_var.set(current_path)
            self.remote_path_var.set(current_path)
            self.append_text(self.files_output, f"Listed {len(entries)} item(s) in {current_path}")

        def bg():
            try:
                self.ui_call(lambda: self.set_status("Loading remote files..."))
                result = worker()
                self.ui_call(lambda: done(result))
                self.ui_call(lambda: self.set_status("Ready"))
            except Exception as exc:
                self.ui_call(lambda e=exc: self.append_text(self.files_output, f"ERROR: {e}"))
                self.ui_call(lambda e=exc: messagebox.showerror(APP_TITLE, str(e)))
                self.ui_call(lambda: self.set_status("Error"))

        threading.Thread(target=bg, daemon=True).start()

    def _selected_remote_item(self):
        selection = self.remote_tree.selection()
        if not selection:
            return None
        item_id = selection[0]
        values = self.remote_tree.item(item_id, "values")
        if not values:
            return None
        path = values[1]
        type_label = values[0]
        name = self.remote_tree.item(item_id, "text").split(" ", 1)[1]
        return {
            "name": name,
            "is_dir": type_label == "Directory",
            "path": path,
        }

    def _on_remote_item_selected(self, _event=None):
        """Mirror the selected remote item into the Remote path field.

        Selection should be non-destructive: it updates the operation target, but
        it does not automatically navigate away. Folder navigation is handled by
        double-click, Enter, Open Selected Folder, or Use Selected Path.
        """
        item = self._selected_remote_item()
        if not item:
            return
        self.remote_path_var.set(item["path"])
        if item["is_dir"]:
            self.set_status(f"Selected remote folder: {item['path']}")
        else:
            self.set_status(f"Selected remote file: {item['path']}")

    def open_selected_remote_folder(self):
        """Navigate into the selected remote folder and refresh the listing."""
        item = self._selected_remote_item()
        if not item:
            messagebox.showwarning(APP_TITLE, "Select a remote folder first.")
            return
        self.remote_path_var.set(item["path"])
        if not item["is_dir"]:
            messagebox.showwarning(APP_TITLE, "The selected item is a file. Select a folder to open it in the browser.")
            self.set_status(f"Selected remote file: {item['path']}")
            return
        self.remote_browser_path_var.set(item["path"])
        self.set_status(f"Opening remote folder: {item['path']}")
        self.refresh_remote_files()

    def _on_remote_item_activated(self, _event=None):
        # Double-click or Enter should open directories just like a normal file browser.
        self.open_selected_remote_folder()

    def use_selected_remote_path(self):
        item = self._selected_remote_item()
        if not item:
            messagebox.showwarning(APP_TITLE, "Select a remote file or folder first.")
            return
        self.remote_path_var.set(item["path"])
        if item["is_dir"]:
            # Previous behavior only copied the folder to the Remote path field.
            # That made the browser look stale: Current folder stayed on the
            # parent and the list did not refresh. For a directory selection,
            # treat Use Selected Path as an explicit request to enter that folder.
            self.remote_browser_path_var.set(item["path"])
            self.set_status(f"Opening remote folder: {item['path']}")
            self.refresh_remote_files()
        else:
            self.set_status(f"Selected remote path: {item['path']}")

    def pull_selected_remote(self):
        item = self._selected_remote_item()
        if not item:
            messagebox.showwarning(APP_TITLE, "Select a remote file or folder first.")
            return
        self.remote_path_var.set(item["path"])
        self.pull_file()

    def push_to_current_remote(self):
        current_remote = self.remote_browser_path_var.get().strip() or "/sdcard/"
        self.remote_path_var.set(current_remote)
        self.push_file()

    # ---------- logcat session controls ----------
    def new_logcat_session(self):
        serial = self.logcat_active_serial or self.logcat_target_serial
        if not serial:
            serial = self.require_selected_device()
        if not serial:
            return

        self.logcat_next_start_clear_visible = True
        self.logcat_next_start_seed_visible = False

        if self.is_logcat_running():
            self.append_text(self.logcat_output, "[logcat] Starting new session...")
            self.stop_logcat()
            self.after(250, lambda s=serial: self.start_logcat(serial_override=s))
        else:
            self.clear_logcat_output()
            self.start_logcat(serial_override=serial)

    def append_logcat_session(self):
        if self.is_logcat_running():
            messagebox.showinfo(APP_TITLE, "Logcat is already running and appending to the current session.")
            return
        serial = self.logcat_active_serial or self.logcat_target_serial
        if not serial:
            serial = self.require_selected_device()
        if not serial:
            return

        # Keep the visible log and seed the new spool file with current visible
        # content so a later Save includes the appended context as one session.
        self.logcat_next_start_clear_visible = False
        self.logcat_next_start_seed_visible = True
        self.start_logcat(serial_override=serial)

    # ---------- logcat ----------
    def start_logcat(self, serial_override=None, reconnecting=False):
        if self.logcat_process and self.logcat_process.poll() is None:
            if not reconnecting:
                messagebox.showinfo(APP_TITLE, "Logcat is already running.")
            return

        serial = serial_override or self.require_selected_device()
        if not serial:
            return
        visible_seed = ""
        if self.logcat_next_start_seed_visible and not self.logcat_next_start_clear_visible:
            try:
                visible_seed = self.logcat_output.get("1.0", tk.END)
            except tk.TclError:
                visible_seed = ""
        flt = self.logcat_filter_var.get().strip()
        try:
            adb_filter_args, local_filter, filter_description = self.build_logcat_filter_plan(flt)
            cmd = self.manager.adb_cmd("logcat", serial=serial)
            if adb_filter_args:
                cmd.extend(adb_filter_args)
        except Exception as exc:
            if reconnecting:
                self.enqueue_logcat_event({"event": "logcat_status", "message": f"[auto reconnect failed to build logcat command] {exc}"})
            else:
                messagebox.showerror(APP_TITLE, str(exc))
                self.set_status("Error")
            return

        # Create the temporary session spool before launching adb. The reader
        # writes every line emitted by adb into this file immediately, before
        # applying any host-side display filter. This prevents UI throttling or
        # local filters from losing the complete captured log session.
        #
        # Append Session must preserve the existing full spool file. Older builds
        # recreated the spool from only the visible Text widget; under heavy logs
        # the visible widget may intentionally drop lines while the full spool keeps
        # them. Reusing the spool here avoids losing hidden/drop-throttled lines.
        append_mode = bool(self.logcat_next_start_seed_visible and not self.logcat_next_start_clear_visible)
        previous_session_file = self.logcat_session_file
        previous_filtered_file = self.logcat_filtered_session_file
        previous_filter_text = getattr(self, "logcat_filter_text", "")

        reuse_full_spool = append_mode and previous_session_file and Path(previous_session_file).exists()
        session_file_path = str(previous_session_file) if reuse_full_spool else str(self.new_logcat_session_file(serial, kind="unfiltered"))

        reuse_filtered_spool = (
            append_mode
            and local_filter
            and previous_filtered_file
            and Path(previous_filtered_file).exists()
            and previous_filter_text == flt
        )
        if local_filter:
            filtered_session_file_path = str(previous_filtered_file) if reuse_filtered_spool else str(self.new_logcat_session_file(serial, kind="filtered"))
        else:
            filtered_session_file_path = None

        # Clean up stale previous files only after deciding what is being reused.
        # Never delete the reused full spool; it may contain lines that are no
        # longer visible in the UI.
        for stale_path in (previous_session_file, previous_filtered_file):
            if not stale_path:
                continue
            if stale_path in {session_file_path, filtered_session_file_path}:
                continue
            try:
                Path(stale_path).unlink(missing_ok=True)
            except Exception:
                pass

        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if is_windows() else 0
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except Exception as exc:
            for path in (session_file_path, filtered_session_file_path):
                try:
                    if path:
                        Path(path).unlink(missing_ok=True)
                except Exception:
                    pass
            if reconnecting:
                self.enqueue_logcat_event({"event": "logcat_status", "message": f"[auto reconnect failed to start logcat] {exc}"})
            else:
                messagebox.showerror(APP_TITLE, str(exc))
                self.set_status("Error")
            return

        self.logcat_generation += 1
        generation = self.logcat_generation
        self.logcat_process = proc
        self.logcat_active_serial = serial
        self.logcat_target_serial = serial
        self.logcat_stop_requested = False
        self.logcat_session_file = session_file_path
        self.logcat_filtered_session_file = filtered_session_file_path
        self.logcat_filter_description = filter_description
        self.logcat_filter_text = flt
        self.logcat_session_timestamp_enabled = self.logcat_timestamp_var.get()
        if self.logcat_pending_new_session or self.logcat_next_start_clear_visible:
            self.clear_logcat_output()
            self.logcat_pending_new_session = False
        elif visible_seed and visible_seed.strip() and not reuse_full_spool:
            try:
                seed_paths = [self.logcat_session_file, self.logcat_filtered_session_file]
                for seed_path in [p for p in seed_paths if p]:
                    with open(seed_path, "a", encoding="utf-8", errors="replace") as seed_log:
                        seed_log.write(visible_seed)
                        if not visible_seed.endswith("\n"):
                            seed_log.write("\n")
            except Exception as exc:
                self.enqueue_logcat_event({"event": "logcat_status", "message": f"[logcat append warning] Could not seed previous visible log into session file: {exc}"})
        elif reuse_full_spool:
            self.enqueue_logcat_event({"event": "logcat_status", "message": "[logcat append] Reusing the existing full spool file so hidden/drop-throttled lines are preserved."})
        self.logcat_next_start_clear_visible = False
        self.logcat_next_start_seed_visible = False
        self.prepare_logcat_header_async(serial)
        self.enqueue_logcat_event({
            "event": "logcat_status",
            "message": f"[logcat spool] Capturing complete unfiltered ADB output to: {self.logcat_session_file}",
        })
        if self.logcat_filtered_session_file:
            self.enqueue_logcat_event({
                "event": "logcat_status",
                "message": f"[logcat spool] Capturing host-filtered output in parallel to: {self.logcat_filtered_session_file}",
            })
        else:
            self.enqueue_logcat_event({
                "event": "logcat_status",
                "message": "[logcat spool] No host-side text/regex filter active; only the unfiltered ADB output stream is spooled.",
            })

        def reader(proc_ref=proc, serial_ref=serial, session_file_ref=self.logcat_session_file, filtered_file_ref=self.logcat_filtered_session_file, timestamp_enabled=self.logcat_session_timestamp_enabled, generation_ref=generation, local_filter_ref=local_filter):
            try:
                stream = proc_ref.stdout
                line_counter = 0
                with open(session_file_ref, "a", encoding="utf-8", errors="replace", buffering=1) as session_log:
                    filtered_log_cm = open(filtered_file_ref, "a", encoding="utf-8", errors="replace", buffering=1) if filtered_file_ref else None
                    try:
                        if stream is not None:
                            for line in stream:
                                # Always spool the complete adb-emitted stream first.
                                # The host-side local filter only controls what is shown
                                # in the GUI, not what is preserved on disk.
                                formatted_line = self.format_logcat_line_with_timestamp(line, timestamp_enabled)
                                session_log.write(formatted_line)
                                line_counter += 1
                                matched = self.logcat_line_matches_filter(line, local_filter_ref)
                                if matched and filtered_log_cm is not None:
                                    filtered_log_cm.write(formatted_line)
                                if line_counter % LOGCAT_SPOOL_FLUSH_EVERY_LINES == 0:
                                    session_log.flush()
                                    if filtered_log_cm is not None:
                                        filtered_log_cm.flush()
                                if not matched:
                                    continue
                                self.enqueue_logcat_line(line)
                        session_log.flush()
                        if filtered_log_cm is not None:
                            filtered_log_cm.flush()
                    finally:
                        if filtered_log_cm is not None:
                            filtered_log_cm.close()
            except Exception as exc:
                self.enqueue_logcat_event({"event": "logcat_status", "message": f"[logcat reader error] {exc}"})
            finally:
                try:
                    returncode = proc_ref.wait(timeout=1)
                except Exception:
                    returncode = proc_ref.poll()
                self.enqueue_logcat_event({
                    "event": "logcat_exit",
                    "serial": serial_ref,
                    "returncode": returncode,
                    "generation": generation_ref,
                })

        self.logcat_thread = threading.Thread(target=reader, daemon=True)
        self.logcat_thread.start()
        if flt:
            self.enqueue_logcat_event({"event": "logcat_status", "message": f"[logcat filter] {filter_description}"})
        if reconnecting:
            self.enqueue_logcat_event({"event": "logcat_status", "message": f"[auto reconnect] logcat resumed for {serial}"})
            self.set_status(f"Logcat reconnected: {serial}")
        else:
            self.set_status("Logcat running")

    def _handle_logcat_exit(self, serial, returncode, generation=None):
        if generation is not None and generation != self.logcat_generation:
            return
        current_proc = self.logcat_process
        if current_proc is None or current_proc.poll() is not None:
            self.logcat_process = None
            self.logcat_thread = None

        if self.logcat_stop_requested:
            self.logcat_active_serial = None
            self.logcat_target_serial = None
            self.logcat_reconnect_running = False
            self.logcat_pending_new_session = False
            self.logcat_expected_reboot_serial = None
            self.logcat_expected_reboot_mode = None
            self.set_status("Logcat stopped")
            return

        if serial != self.logcat_target_serial:
            return

        expected_reboot = bool(serial and serial == self.logcat_expected_reboot_serial)
        expected_mode = self.logcat_expected_reboot_mode if expected_reboot else None
        is_usb_disconnect = serial and ":" not in serial
        is_wireless_disconnect = serial and ":" in serial
        source_log_path = self.logcat_session_file
        filtered_source_log_path = self.logcat_filtered_session_file
        session_has_files = any(Path(p).exists() and Path(p).stat().st_size > 0 for p in (source_log_path, filtered_source_log_path) if p)
        if serial and session_has_files:
            content = self.logcat_output.get("1.0", tk.END)
            header = self.logcat_cached_header or self.build_fallback_log_header(serial)
            filter_description = self.logcat_filter_description
            # Detach the completed session files before any reconnect path starts.
            # Otherwise a fast reconnect can delete these files when creating the
            # next session, racing the background auto-save worker. This is needed
            # for USB and wireless sessions; older builds only protected USB.
            self.logcat_session_file = None
            self.logcat_filtered_session_file = None
            self.clear_logcat_output()
            self.logcat_pending_new_session = True
            if expected_reboot:
                expected_label = expected_mode or "reboot"
                self.enqueue_logcat_event({"event": "logcat_status", "message": f"[auto save] Expected {expected_label} disconnect detected. Saving closed log session in the background..."})
                save_reason = f"{self.sanitize_filename_part(expected_label, fallback='expected')}_disconnect"
            elif is_usb_disconnect:
                self.enqueue_logcat_event({"event": "logcat_status", "message": "[auto save] USB disconnect detected. Saving closed log session in the background..."})
                save_reason = "usb_disconnect"
            elif is_wireless_disconnect:
                self.enqueue_logcat_event({"event": "logcat_status", "message": "[auto save] Wireless ADB/logcat disconnect detected. Saving closed log session in the background..."})
                save_reason = "wireless_disconnect"
            else:
                self.enqueue_logcat_event({"event": "logcat_status", "message": "[auto save] Logcat exited unexpectedly. Saving closed log session in the background..."})
                save_reason = "logcat_exit"
            self.auto_save_logcat_session_async(
                serial,
                reason=save_reason,
                content=content,
                header=header,
                source_log_path=source_log_path,
                filtered_source_log_path=filtered_source_log_path,
                filter_description=filter_description,
            )

        self.set_status("Logcat disconnected")
        if expected_reboot:
            expected_label = expected_mode or "reboot"
            self.append_text(self.logcat_output, f"[logcat] Expected {expected_label} disconnect for {serial}; waiting for the same Android serial to return.")
        else:
            self.append_text(self.logcat_output, f"[logcat disconnected] Device {serial} disconnected or logcat exited (code: {returncode}).")

        if self.logcat_auto_reconnect_var.get():
            self._start_logcat_reconnect(serial)
        else:
            if expected_reboot:
                self.logcat_expected_reboot_serial = None
                self.logcat_expected_reboot_mode = None
            self.append_text(self.logcat_output, f"[logcat stopped] Auto reconnect is disabled for {serial}.")

    def _start_logcat_reconnect(self, serial):
        if self.logcat_reconnect_running:
            return

        self.logcat_reconnect_running = True

        def worker(target_serial=serial):
            attempt = 0
            try:
                while not self.logcat_stop_requested and self.logcat_target_serial == target_serial:
                    attempt += 1
                    self.enqueue_logcat_event({
                        "event": "logcat_status",
                        "message": f"[auto reconnect] Attempt {attempt}: waiting for {target_serial}",
                    })

                    try:
                        self.manager.run_adb("start-server", timeout=20)
                    except Exception:
                        pass

                    is_wireless = ":" in target_serial
                    if is_wireless:
                        try:
                            output = self.manager.run_adb("connect", target_serial, timeout=20)
                            if output:
                                self.enqueue_logcat_event({
                                    "event": "logcat_status",
                                    "message": f"[auto reconnect] {output.strip()}",
                                })
                        except Exception as exc:
                            self.enqueue_logcat_event({
                                "event": "logcat_status",
                                "message": f"[auto reconnect] adb connect failed: {exc}",
                            })

                    try:
                        devices = self.manager.list_devices()
                    except Exception as exc:
                        self.enqueue_logcat_event({
                            "event": "logcat_status",
                            "message": f"[auto reconnect] unable to refresh devices: {exc}",
                        })
                        time.sleep(2)
                        continue

                    matched = next((d for d in devices if d.get("serial") == target_serial and d.get("state") == "device"), None)
                    if matched:
                        self.enqueue_logcat_event({
                            "event": "logcat_reconnected",
                            "serial": target_serial,
                        })
                        return

                    time.sleep(2)
            finally:
                self.logcat_reconnect_running = False

        self.logcat_reconnect_thread = threading.Thread(target=worker, daemon=True)
        self.logcat_reconnect_thread.start()

    def _resume_logcat_after_reconnect(self, serial):
        if self.logcat_stop_requested:
            return
        if serial == self.logcat_expected_reboot_serial:
            self.logcat_expected_reboot_serial = None
            self.logcat_expected_reboot_mode = None
        self.refresh_devices()
        self.start_logcat(serial_override=serial, reconnecting=True)

    def stop_logcat(self):
        self.logcat_stop_requested = True
        self.logcat_generation += 1
        proc = self.logcat_process
        if not proc or proc.poll() is not None:
            self.logcat_process = None
            self.logcat_thread = None
            self.logcat_active_serial = None
            self.logcat_target_serial = None
            self.logcat_reconnect_running = False
            self.logcat_pending_new_session = False
            self.logcat_expected_reboot_serial = None
            self.logcat_expected_reboot_mode = None
            self.set_status("Logcat not running")
            return
        try:
            if is_windows():
                proc.send_signal(signal.CTRL_BREAK_EVENT)
                proc.wait(timeout=5)
            else:
                proc.terminate()
                proc.wait(timeout=5)
        except Exception:
            proc.kill()
        finally:
            self.logcat_process = None
            self.logcat_thread = None
            self.logcat_active_serial = None
            self.logcat_target_serial = None
            self.logcat_pending_new_session = False
            self.logcat_expected_reboot_serial = None
            self.logcat_expected_reboot_mode = None
        self.set_status("Logcat stopped")

    def save_logcat(self):
        if not find_7zip_executable():
            messagebox.showerror(
                APP_TITLE,
                "7-Zip was not found. Install 7-Zip or add 7z.exe to PATH before saving logs as .7z.",
            )
            return

        device_name = self.get_logcat_filename_serial()
        serial_for_header = self.logcat_active_serial
        if not serial_for_header:
            try:
                serial_for_header = self.get_selected_serial()
            except Exception:
                serial_for_header = "unknown_device"
        archive_path = filedialog.asksaveasfilename(
            defaultextension=".7z",
            filetypes=[("7-Zip Archive", "*.7z")],
            initialfile=f"{device_name}_logcat_{datetime.now().strftime('%Y%m%d_%H%M%S')}.7z",
        )
        if not archive_path:
            return

        content = self.logcat_output.get("1.0", tk.END)
        source_log_path = self.logcat_session_file
        filtered_source_log_path = self.logcat_filtered_session_file
        filter_description = self.logcat_filter_description
        header = self.logcat_cached_header or self.build_fallback_log_header(serial_for_header)

        def worker():
            with self.logcat_save_lock:
                source_specs = self.build_logcat_source_specs(
                    source_log_path,
                    filtered_source_log_path,
                    filter_description=filter_description,
                )
                has_sources = any(Path(spec["path"]).exists() and Path(spec["path"]).stat().st_size > 0 for spec in source_specs)
                if has_sources:
                    archive_path_obj = self.create_logcat_archive_from_files(archive_path, source_specs, serial_for_header=serial_for_header, header_override=header)
                else:
                    archive_path_obj = self.create_logcat_archive(archive_path, content, serial_for_header=serial_for_header, header_override=header)
                return f"Saved logcat archive: {archive_path_obj}"

        self.run_background_action("save logcat to 7z", worker, widget=self.logcat_output)

    # ---------- capture / wireless ----------
    def take_screenshot(self):
        serial = self.require_selected_device()
        if not serial:
            return
        path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG Image", "*.png")], initialfile=f"screenshot_{serial}.png")
        if not path:
            return

        def worker():
            remote = f"/sdcard/screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            self.manager.run_adb("shell", "screencap", "-p", remote, serial=serial, timeout=120)
            self.manager.run_adb("pull", remote, path, serial=serial, timeout=300)
            self.manager.run_adb("shell", "rm", remote, serial=serial, timeout=60)
            return f"Screenshot saved to: {path}"

        self.run_background_action("screenshot", worker, widget=self.capture_output)

    def start_screenrecord(self):
        if self.screenrecord_process and self.screenrecord_process.poll() is None:
            messagebox.showinfo(APP_TITLE, "Screenrecord is already running.")
            return
        serial = self.require_selected_device()
        if not serial:
            return
        remote = f"/sdcard/screenrecord_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        try:
            cmd = self.manager.adb_cmd("shell", "screenrecord", remote, serial=serial)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            self.set_status("Error")
            return
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if is_windows() else 0
        try:
            self.screenrecord_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                creationflags=creationflags,
            )
        except Exception as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            self.set_status("Error")
            return
        self.screenrecord_remote = remote
        self.screenrecord_serial = serial
        self.append_text(self.capture_output, f"Screenrecord started: {remote}")
        self.set_status("Screenrecord running")

    def clear_screenrecord_state_if_current(self, remote_path=None, serial=None):
        if remote_path is not None and self.screenrecord_remote not in (None, remote_path):
            return
        if serial is not None and self.screenrecord_serial not in (None, serial):
            return
        self.screenrecord_process = None
        self.screenrecord_remote = None
        self.screenrecord_serial = None

    def stop_screenrecord(self):
        proc = self.screenrecord_process
        if not proc or proc.poll() is not None:
            self.set_status("Screenrecord not running")
            return
        try:
            if is_windows():
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            proc.kill()

        dest = filedialog.asksaveasfilename(defaultextension=".mp4", filetypes=[("MP4 Video", "*.mp4")], initialfile="screenrecord.mp4")
        if not dest:
            self.append_text(self.capture_output, f"Recording left on device: {self.screenrecord_remote}")
            self.screenrecord_process = None
            self.set_status("Recording stopped")
            return

        remote_path = self.screenrecord_remote
        record_serial = self.screenrecord_serial

        def cleanup_after_pull(_output):
            self.manager.run_adb("shell", "rm", remote_path, serial=record_serial, timeout=60)
            self.ui_call(lambda rp=remote_path, rs=record_serial: self.clear_screenrecord_state_if_current(rp, rs))
            return f"Screenrecord saved to: {dest}"

        self.run_cancellable_adb_operation(
            "pull screenrecord",
            ["pull", remote_path, dest],
            serial=record_serial,
            timeout=3600,
            widget=self.capture_output,
            progress_frame=self.capture_progress_frame,
            progress_var=self.capture_progress_var,
            on_success=cleanup_after_pull,
        )

    def enable_tcpip(self):
        serial = self.require_selected_device()
        if not serial:
            return
        port = self.tcpip_port_var.get().strip() or "5555"

        def worker():
            return self.manager.run_adb("tcpip", port, serial=serial, timeout=120)

        self.run_background_action(f"adb tcpip {port}", worker, widget=self.capture_output)

    def connect_wireless(self):
        target = self.connect_target_var.get().strip()
        if not target:
            messagebox.showwarning(APP_TITLE, "Enter an IP:Port target.")
            return

        def worker():
            return self.manager.run_adb("connect", target, timeout=120)

        self.run_background_action(f"adb connect {target}", worker, widget=self.capture_output)

    # ---------- packages ----------
    def list_packages(self):
        serial = self.require_selected_device()
        if not serial:
            return

        third_party_only = self.third_party_only.get()

        def worker():
            args = ["shell", "pm", "list", "packages"]
            if third_party_only:
                args.append("-3")
            output = self.manager.run_adb(*args, serial=serial, timeout=300)
            pkgs = [line.replace("package:", "").strip() for line in output.splitlines() if line.strip()]
            return pkgs

        def done(pkgs):
            self.package_list.delete(0, tk.END)
            for pkg in pkgs:
                self.package_list.insert(tk.END, pkg)
            self.clear_text(self.package_output)
            self.append_text(self.package_output, f"Loaded {len(pkgs)} package(s).")

        def bg():
            try:
                self.ui_call(lambda: self.set_status("Loading packages..."))
                pkgs = worker()
                self.ui_call(lambda: done(pkgs))
                self.ui_call(lambda: self.set_status("Ready"))
            except Exception as exc:
                self.ui_call(lambda e=exc: messagebox.showerror(APP_TITLE, str(e)))
                self.ui_call(lambda: self.set_status("Error"))

        threading.Thread(target=bg, daemon=True).start()

    def _selected_package_name(self):
        sel = self.package_list.curselection()
        if not sel:
            return ""
        return self.package_list.get(sel[0])

    def _on_package_selected(self, _event=None):
        pkg = self._selected_package_name()
        if pkg:
            self.uninstall_pkg_var.set(pkg)

    def show_package_path(self):
        serial = self.require_selected_device()
        if not serial:
            return
        pkg = self._selected_package_name() or self.uninstall_pkg_var.get().strip()
        if not pkg:
            messagebox.showwarning(APP_TITLE, "Select or enter a package first.")
            return

        def worker():
            return self.manager.run_adb("shell", "pm", "path", pkg, serial=serial, timeout=120)

        self.run_background_action(f"package path {pkg}", worker, widget=self.package_output)

    def load_battery_info(self):
        serial = self.require_selected_device()
        if not serial:
            return

        def worker():
            return self.manager.run_adb("shell", "dumpsys", "battery", serial=serial, timeout=120)

        self.run_background_action("battery info", worker, widget=self.package_output)

    def load_properties(self):
        serial = self.require_selected_device()
        if not serial:
            return

        def worker():
            return self.manager.run_adb("shell", "getprop", serial=serial, timeout=300)

        self.run_background_action("getprop", worker, widget=self.package_output)

    # ---------- raw ----------
    def run_raw_command(self):
        command = self.raw_entry.get().strip()
        if not command:
            messagebox.showwarning(APP_TITLE, "Enter adb arguments, for example: devices -l")
            return

        def worker():
            cmd = [self.manager.require_adb()] + split_user_args(command)
            res = run_quick(cmd, timeout=600)
            text = "$ adb " + command + "\n\n"
            if res.stdout:
                text += res.stdout
            if res.stderr:
                text += ("\n" if not text.endswith("\n") else "") + res.stderr
            if res.returncode != 0:
                raise RuntimeError(text.strip())
            return text.strip()

        self.run_background_action(f"adb {command}", worker, widget=self.raw_output)

    def on_close(self):
        if self.is_logcat_running():
            choice = messagebox.askyesnocancel(
                APP_TITLE,
                "Logcat is still running. Yes = save the current log session and exit. No = stop without saving and exit. Cancel = keep the app open.",
            )
            if choice is None:
                return
            if choice:
                try:
                    serial = self.logcat_active_serial or self.logcat_target_serial or self._selected_serial_value_or_empty() or "unknown_device"
                    source_log_path = self.logcat_session_file
                    filtered_source_log_path = self.logcat_filtered_session_file
                    filter_description = self.logcat_filter_description
                    header = self.logcat_cached_header or self.build_fallback_log_header(serial)
                    content = self.logcat_output.get("1.0", tk.END)
                    reader_thread = self.logcat_thread
                    self.stop_logcat()
                    try:
                        if reader_thread:
                            reader_thread.join(timeout=1.0)
                    except Exception:
                        pass
                    archive_path = self.build_automatic_logcat_archive_path(serial, suffix="logcat_app_close")
                    source_specs = self.build_logcat_source_specs(source_log_path, filtered_source_log_path, filter_description=filter_description)
                    has_sources = any(Path(spec["path"]).exists() for spec in source_specs)
                    if has_sources:
                        self.create_logcat_archive_from_files(archive_path, source_specs, serial_for_header=serial, header_override=header)
                    elif content.strip():
                        self.create_logcat_archive(archive_path, content, serial_for_header=serial, header_override=header)
                    messagebox.showinfo(APP_TITLE, f"Logcat session saved to: {archive_path}")
                except Exception as exc:
                    proceed = messagebox.askyesno(APP_TITLE, f"Could not save the Logcat session before exit: {exc}. Exit anyway?")
                    if not proceed:
                        return
            else:
                try:
                    self.stop_logcat()
                except Exception:
                    pass
        self._closing = True
        try:
            self.save_current_settings()
        except Exception:
            pass
        try:
            if self._active_operation_proc and self._active_operation_proc.poll() is None:
                try:
                    self._active_operation_proc.terminate()
                except Exception:
                    self._active_operation_proc.kill()
        except Exception:
            pass
        try:
            proc = self.screenrecord_process
            if proc and proc.poll() is None:
                if is_windows():
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.terminate()
            self.screenrecord_process = None
            self.screenrecord_remote = None
            self.screenrecord_serial = None
        except Exception:
            pass
        try:
            self.cleanup_logcat_session_file()
        except Exception:
            pass
        self.destroy()

    # ---------- queue polling ----------
    def _poll_queues(self):
        pending_lines = []
        processed = 0
        deadline = time.monotonic() + LOGCAT_POLL_TIME_BUDGET_SEC
        queue_may_have_more = False

        def flush_pending():
            nonlocal pending_lines
            if pending_lines:
                self.append_logcat_batch(pending_lines)
                pending_lines = []

        try:
            while processed < LOGCAT_POLL_MAX_ITEMS and time.monotonic() < deadline:
                try:
                    item = self.logcat_queue.get_nowait()
                except queue.Empty:
                    break
                processed += 1

                if isinstance(item, dict):
                    event = item.get("event")
                    if event == "logcat_exit":
                        flush_pending()
                        self._handle_logcat_exit(item.get("serial"), item.get("returncode"), item.get("generation"))
                        continue
                    if event == "logcat_reconnected":
                        flush_pending()
                        self._resume_logcat_after_reconnect(item.get("serial"))
                        continue
                    if event == "logcat_status":
                        line = str(item.get("message", ""))
                        if line and not line.endswith("\n"):
                            line += "\n"
                    else:
                        line = str(item)
                        if line and not line.endswith("\n"):
                            line += "\n"
                else:
                    line = str(item)

                if line:
                    pending_lines.append(line)

            flush_pending()
            with self.logcat_queue_lock:
                dropped = self.logcat_ui_dropped_lines
                self.logcat_ui_dropped_lines = 0
            if dropped:
                self.append_logcat_batch([f"[logcat UI notice] Dropped {dropped} visible line(s) to keep the GUI responsive. The full session log file still contains all captured lines.\n"])
            queue_may_have_more = processed >= LOGCAT_POLL_MAX_ITEMS or not self.logcat_queue.empty()
        except tk.TclError:
            return
        except Exception as exc:
            try:
                self.append_text(self.logcat_output, f"[logcat UI poll error] {exc}")
            except Exception:
                pass

        if not self._closing:
            delay = LOGCAT_BUSY_POLL_INTERVAL_MS if queue_may_have_more else LOGCAT_POLL_INTERVAL_MS
            try:
                self.after(delay, self._poll_queues)
            except tk.TclError:
                pass


def main():
    app = ADBGui()
    app.mainloop()


if __name__ == "__main__":
    main()
