import os
import json
import stat
import zipfile
import shutil
import subprocess
import urllib.request
from typing import Optional

try:
    import undetected_chromedriver as uc
    HAS_UC = True
except ImportError:
    HAS_UC = False


def _status(cb, msg):
    if cb:
        cb(msg)
    else:
        print(msg)


def _subprocess_startup_kwargs():
    """Hide helper subprocess windows in PyInstaller windowed builds on Windows."""
    if os.name != 'nt':
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return {
        'startupinfo': startupinfo,
        'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    }


def _read_chrome_version_from_registry() -> Optional[str]:
    if os.name != 'nt':
        return None

    try:
        import winreg
    except Exception:
        return None

    registry_locations = (
        (winreg.HKEY_CURRENT_USER, r'SOFTWARE\Google\Chrome\BLBeacon'),
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Google\Chrome\BLBeacon'),
        (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon'),
    )
    for root, key_path in registry_locations:
        try:
            with winreg.OpenKey(root, key_path) as key:
                version, _ = winreg.QueryValueEx(key, 'version')
                if version:
                    return str(version)
        except OSError:
            continue
    return None


def _read_app_path_from_registry(executable_name: str) -> Optional[str]:
    if os.name != 'nt':
        return None

    try:
        import winreg
    except Exception:
        return None

    registry_locations = (
        (winreg.HKEY_CURRENT_USER, rf'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable_name}'),
        (winreg.HKEY_LOCAL_MACHINE, rf'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable_name}'),
        (winreg.HKEY_LOCAL_MACHINE, rf'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\{executable_name}'),
    )
    for root, key_path in registry_locations:
        try:
            with winreg.OpenKey(root, key_path) as key:
                path, _ = winreg.QueryValueEx(key, None)
                normalized = _normalize_executable_path(path)
                if normalized:
                    return normalized
        except OSError:
            continue
    return None


def _normalize_executable_path(path) -> Optional[str]:
    if not isinstance(path, str):
        return None

    normalized = os.path.expandvars(path.strip().strip('"'))
    if os.path.exists(normalized):
        return normalized
    return None


def _candidate_existing_path(paths) -> Optional[str]:
    for path in paths:
        normalized = _normalize_executable_path(path)
        if normalized:
            return normalized
    return None


def find_chrome_executable(status_callback=None) -> Optional[str]:
    """Return the Google Chrome executable path as a real string, if installed."""
    registry_path = _read_app_path_from_registry('chrome.exe')
    if registry_path:
        return registry_path

    env = os.environ
    common_paths = (
        os.path.join(env.get('ProgramFiles', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(env.get('ProgramFiles(x86)', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(env.get('LocalAppData', ''), 'Google', 'Chrome', 'Application', 'chrome.exe'),
    )
    common_path = _candidate_existing_path(common_paths)
    if common_path:
        return common_path

    path_chrome = shutil.which('chrome') or shutil.which('chrome.exe')
    normalized = _normalize_executable_path(path_chrome)
    if normalized:
        return normalized

    if HAS_UC:
        try:
            uc_path = uc.find_chrome_executable()
            normalized = _normalize_executable_path(uc_path)
            if normalized:
                return normalized
        except Exception as exc:
            _status(status_callback, f'[!] undetected_chromedriver 定位 Chrome 失败: {exc}')

    _status(status_callback, '[!] 未检测到 Google Chrome 可执行文件路径')
    return None


def _get_chrome_version(status_callback=None):
    """Detect the locally installed Chrome version without showing helper windows."""
    registry_version = _read_chrome_version_from_registry()
    if registry_version:
        return registry_version

    try:
        chrome_path = find_chrome_executable(status_callback)
        if not chrome_path:
            return None

        result = subprocess.run(
            [
                'powershell',
                '-NoProfile',
                '-NonInteractive',
                '-ExecutionPolicy',
                'Bypass',
                '-Command',
                f'(Get-Item -LiteralPath "{chrome_path}").VersionInfo.ProductVersion',
            ],
            capture_output=True,
            text=True,
            timeout=10,
            **_subprocess_startup_kwargs(),
        )
        ver = result.stdout.strip()
        if ver:
            return ver
        if result.stderr.strip():
            _status(status_callback, f'[!] 读取 Chrome 版本失败: {result.stderr.strip()}')
    except Exception as exc:
        _status(status_callback, f'[!] 读取 Chrome 版本失败: {exc}')
    return None


def _resolve_chromedriver_version(version, status_callback=None):
    """Match regular Chrome to the driver release using Google's CfT index."""
    parts = version.split('.')
    if len(parts) < 3 or not parts[0].isdigit() or int(parts[0]) < 115:
        return version

    indexes = (
        ('latest-patch-versions-per-build.json', 'builds', '.'.join(parts[:3])),
        ('latest-versions-per-milestone.json', 'milestones', parts[0]),
    )
    for filename, collection, key in indexes:
        try:
            url = f'https://googlechromelabs.github.io/chrome-for-testing/{filename}'
            with urllib.request.urlopen(url, timeout=15) as response:
                data = json.load(response)
            driver_version = (data.get(collection, {}).get(key) or {}).get('version')
            if driver_version and driver_version.split('.')[0] == parts[0]:
                _status(status_callback, f'[*] Chrome {version} 匹配 ChromeDriver {driver_version}')
                return driver_version
        except Exception as exc:
            _status(status_callback, f'[!] 查询 ChromeDriver 版本失败: {exc}')

    _status(status_callback, '[!] 官方索引暂未找到匹配驱动，尝试浏览器原版本号')
    return version


def _download_chromedriver(version, cache_dir, status_callback=None):
    """Download matching ChromeDriver from Google storage. Returns path or None."""
    os.makedirs(cache_dir, exist_ok=True)
    major = version.split('.')[0]
    driver_version = _resolve_chromedriver_version(version, status_callback)

    sources = [
        {
            'url': f'https://storage.googleapis.com/chrome-for-testing-public/{driver_version}/win32/chromedriver-win32.zip',
            'subdir': 'chromedriver-win32',
        },
        {
            'url': f'https://www.googleapis.com/download/storage/v1/b/chrome-for-testing-public/o/{driver_version}%2Fwin32%2Fchromedriver-win32.zip?alt=media',
            'subdir': 'chromedriver-win32',
        },
    ]

    for source in sources:
        try:
            url = source['url']
            subdir = source['subdir']
            _status(status_callback, f'[*] 尝试下载 ChromeDriver: {url[:60]}...')

            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()

            zip_path = os.path.join(cache_dir, 'chromedriver.zip')
            with open(zip_path, 'wb') as f:
                f.write(data)

            with zipfile.ZipFile(zip_path, 'r') as z:
                z.extractall(cache_dir)

            driver_path = os.path.join(cache_dir, subdir, 'chromedriver.exe')
            if not os.path.exists(driver_path):
                for root, dirs, files in os.walk(cache_dir):
                    if 'chromedriver.exe' in files:
                        driver_path = os.path.join(root, 'chromedriver.exe')
                        break

            if os.path.exists(driver_path):
                os.chmod(driver_path, os.stat(driver_path).st_mode | stat.S_IEXEC)
                _status(status_callback, f'[OK] ChromeDriver 下载完成: {driver_path}')
                return driver_path
        except Exception as e:
            _status(status_callback, f'[*] 此源失败: {e}')
            continue

    existing = os.path.expanduser(f'~/.chromedriver_{major}/chromedriver-win32/chromedriver.exe')
    if os.path.exists(existing):
        _status(status_callback, f'[*] 使用已缓存的 ChromeDriver: {existing}')
        return existing
    return None


def ensure_chromedriver(status_callback=None):
    """Ensure ChromeDriver is available, auto-download if needed. Returns path or None."""
    path = shutil.which('chromedriver')
    if path:
        _status(status_callback, f'[*] 使用系统 PATH 中的 ChromeDriver: {path}')
        return path

    chrome_ver = _get_chrome_version(status_callback)
    if not chrome_ver:
        _status(status_callback, '[!] 无法检测 Chrome 版本，无法自动匹配 ChromeDriver')
        return None

    major = chrome_ver.split('.')[0]
    cache_dir = os.path.expanduser(f'~/.chromedriver_{major}')

    cached_path = os.path.join(cache_dir, 'chromedriver-win32', 'chromedriver.exe')
    if os.path.exists(cached_path):
        _status(status_callback, f'[*] 使用缓存 ChromeDriver ({chrome_ver}): {cached_path}')
        return cached_path

    _status(status_callback, f'[*] 未找到 ChromeDriver v{major}，正在下载...')
    return _download_chromedriver(chrome_ver, cache_dir, status_callback)
