import json
from dataclasses import dataclass

import requests


APP_VERSION = '20260609'
UPDATE_CHECK_URL = (
    'https://program-control.oss-rg-china-mainland.aliyuncs.com/videoDown.txt'
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
    return bool(update_info.version_number and update_info.version_number != APP_VERSION)
