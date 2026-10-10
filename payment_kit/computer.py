"""Windows 电脑标识。原始硬件值仅用于本机生成 UUID，不发送至服务器。"""

import json
import os
import subprocess
import time
import uuid
from pathlib import Path


def normalize(value):
    try:
        result = uuid.UUID(str(value).strip())
        return result.hex if result.int not in (0, (1 << 128) - 1) else None
    except (ValueError, TypeError, AttributeError):
        return None


def machine_guid():
    if os.name != "nt":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return normalize(winreg.QueryValueEx(key, "MachineGuid")[0])
    except OSError:
        return None


def smbios_uuid():
    if os.name != "nt":
        return None
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        result = subprocess.run(
            [str(executable), "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance -ClassName Win32_ComputerSystemProduct -ErrorAction Stop).UUID"],
            capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return normalize(result.stdout.decode("utf-8-sig", errors="replace")) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def computer_uuid(source, value):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"qingning-video-downloader/device/v1/{source}/{value}").hex


class ComputerIdentity:
    def __init__(self, path, *, smbios_reader=None, guid_reader=None):
        self.path = Path(path)
        self._smbios = smbios_reader or smbios_uuid
        self._guid = guid_reader or machine_guid
        anchor = normalize(self._guid())
        if self.path.exists():
            self._load(anchor)
            return
        hardware = normalize(self._smbios())
        source, value = ("smbios", hardware) if hardware else ("machine_guid", anchor)
        if not value:
            raise ValueError("无法读取电脑标识，请稍后重试或联系客服；不会生成新的试用身份")
        data = {"source": source, "value": value, "anchor": anchor, "uuid": computer_uuid(source, value)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            self._load(anchor)
        else:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
            self.uuid = data["uuid"]

    def _load(self, anchor):
        for attempt in range(10):
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                source, value = data["source"], normalize(data["value"])
                if source not in {"smbios", "machine_guid"} or not value or data["uuid"] != computer_uuid(source, value):
                    raise ValueError()
                break
            except (ValueError, KeyError, TypeError) as exc:
                if attempt == 9:
                    raise ValueError("本机电脑标识记录损坏，请联系客服恢复") from exc
                time.sleep(0.05)
        current = normalize(self._smbios()) if source == "smbios" else anchor
        if current and current != value:
            raise ValueError("电脑标识已变化，请联系客服恢复已有授权")
        if not current and not (source == "smbios" and anchor and anchor == data.get("anchor")):
            raise ValueError("暂时无法确认电脑标识，请稍后重试")
        self.uuid = data["uuid"]
