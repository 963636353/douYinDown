import json
from dataclasses import dataclass

import requests


APP_VERSION = '20261017'
UPDATE_CHECK_URL = (
    'https://douyindown.secs.cc/videoDown.txt'
)


@dataclass
class UpdateInfo:
    version_number: str
    baidu_url: str
    quark_url: str
    info: str
    forced_upgrade: bool


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {'true', '1', 'yes', 'y'}


def fetch_update_info(url: str = UPDATE_CHECK_URL, timeout: int = 5) -> UpdateInfo:
    response = requests.get(
        url,
        timeout=timeout,
        headers={'User-Agent': f'QingningVideoDownloader/{APP_VERSION}'},
    )
    response.raise_for_status()

    raw_text = response.text.lstrip('\ufeff').strip()
    data = json.loads(raw_text)

    return UpdateInfo(
        version_number=str(data.get('version_number', '')).strip(),
        baidu_url=str(data.get('百度', '')).strip(),
        quark_url=str(data.get('夸克', '')).strip(),
        info=str(data.get('info', '')).strip(),
        forced_upgrade=_to_bool(data.get('forced_upgrade')),
    )


def has_new_version(update_info: UpdateInfo) -> bool:
    remote = update_info.version_number
    # 日期数字版本只提示更高版本，避免测试新版被旧的线上配置引导降级。
    if remote.isdigit() and APP_VERSION.isdigit():
        return int(remote) > int(APP_VERSION)
    return bool(remote and remote != APP_VERSION)
