import random
import string

from f2.utils.abogus import ABogus, BrowserFingerprintGenerator

_USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
               'AppleWebKit/537.36 (KHTML, like Gecko) '
               'Chrome/130.0.0.0 Safari/537.36')

API_DETAIL = 'https://www.douyin.com/aweme/v1/web/aweme/detail/'
API_USER_POST = 'https://www.douyin.com/aweme/v1/web/aweme/post/'


def get_user_agent() -> str:
    return _USER_AGENT


def build_params(aweme_id: str) -> dict:
    """Build the query parameters for the Douyin video detail API."""
    return {
        'device_platform': 'webapp',
        'aid': '6383',
        'channel': 'channel_pc_web',
        'pc_client_type': '1',
        'version_code': '290100',
        'version_name': '29.1.0',
        'cookie_enabled': 'true',
        'screen_width': '1920',
        'screen_height': '1080',
        'browser_language': 'zh-CN',
        'browser_platform': 'Win32',
        'browser_name': 'Chrome',
        'browser_version': '130.0.0.0',
        'browser_online': 'true',
        'engine_name': 'Blink',
        'engine_version': '130.0.0.0',
        'os_name': 'Windows',
        'os_version': '10',
        'cpu_core_num': '12',
        'device_memory': '8',
        'platform': 'PC',
        'downlink': '10',
        'effective_type': '4g',
        'round_trip_time': '100',
        'aweme_id': aweme_id,
    }


def build_user_post_params(
    sec_user_id: str,
    max_cursor: int = 0,
    count: int = 20,
) -> dict:
    """Build the query parameters for the Douyin user post API."""
    return {
        'device_platform': 'webapp',
        'aid': '6383',
        'channel': 'channel_pc_web',
        'pc_client_type': '1',
        'publish_video_strategy_type': '2',
        'pc_libra_divert': 'Windows',
        'version_code': '290100',
        'version_name': '29.1.0',
        'cookie_enabled': 'true',
        'screen_width': '1920',
        'screen_height': '1080',
        'browser_language': 'zh-CN',
        'browser_platform': 'Win32',
        'browser_name': 'Chrome',
        'browser_version': '130.0.0.0',
        'browser_online': 'true',
        'engine_name': 'Blink',
        'engine_version': '130.0.0.0',
        'os_name': 'Windows',
        'os_version': '10',
        'cpu_core_num': '12',
        'device_memory': '8',
        'platform': 'PC',
        'downlink': '10',
        'effective_type': '4g',
        'round_trip_time': '100',
        'max_cursor': str(max_cursor),
        'count': str(count),
        'sec_user_id': sec_user_id,
        'msToken': _gen_ms_token(),
    }


def _gen_ms_token() -> str:
    alphabet = string.ascii_letters + string.digits + '-_'
    return ''.join(random.choice(alphabet) for _ in range(126)) + '=='


def generate_abogus(params_str: str) -> str:
    """Generate a_bogus signature for the given parameter string."""
    fp = BrowserFingerprintGenerator.generate_fingerprint('Chrome')
    ab = ABogus(user_agent=_USER_AGENT, fp=fp)
    return ab.generate_abogus(params_str, 'GET')[0]


def build_headers(cookies: dict = None) -> dict:
    """Build HTTP headers for Douyin API requests."""
    headers = {
        'User-Agent': _USER_AGENT,
        'Referer': 'https://www.douyin.com/',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }
    return headers
