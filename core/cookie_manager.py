import os
import time
from http.cookiejar import MozillaCookieJar

from core.paths import runtime_path


COOKIE_DIR = runtime_path('cookies')
os.makedirs(COOKIE_DIR, exist_ok=True)


def _cookie_path(platform_name: str) -> str:
    return os.path.join(COOKIE_DIR, f'{platform_name}_cookies.txt')


def _old_cookie_path(platform_name: str) -> str:
    return os.path.join(os.path.dirname(COOKIE_DIR), f'{platform_name}_cookies.txt')


def load_cookies(platform_name: str):
    """Load cookies from Netscape-format file for a given platform.

    Returns unexpired cookies as {name: value}, or None if file not found.
    """
    filepath = _cookie_path(platform_name)
    if not os.path.exists(filepath):
        old_path = _old_cookie_path(platform_name)
        if os.path.exists(old_path):
            filepath = old_path

    jar = MozillaCookieJar(filepath)
    try:
        # Keep session cookies, but never reuse cookies past their expiry date.
        jar.load(ignore_expires=False, ignore_discard=True)
        return {c.name: c.value for c in jar if c.name}
    except (FileNotFoundError, OSError):
        return None


def save_cookies(cookies_list: list, platform_name: str):
    """Save a list of cookie dicts (from selenium/playwright) to Netscape-format file."""
    jar = MozillaCookieJar(_cookie_path(platform_name))
    now = time.time()
    for c in cookies_list:
        jar.set_cookie(
            _make_cookie(c, platform_name, now)
        )
    jar.save(ignore_expires=True, ignore_discard=True)
    print(f'[OK] Cookie 已保存到 {_cookie_path(platform_name)}')


def _make_cookie(c, platform_name, now):
    import http.cookies
    from http.cookiejar import Cookie
    domain = c.get('domain', f'.{platform_name}.com')
    if not domain.startswith('.'):
        domain = '.' + domain
    expires = int(c.get('expiry') or c.get('expires') or now + 86400 * 30)
    return Cookie(
        version=0, name=c['name'], value=c['value'],
        port=None, port_specified=False,
        domain=domain, domain_specified=True, domain_initial_dot=domain.startswith('.'),
        path=c.get('path', '/'), path_specified=True,
        secure=c.get('secure', False),
        expires=expires, discard=False,
        comment=None, comment_url=None,
        rest={'HttpOnly': c.get('httpOnly', '')},
        rfc2109=False,
    )


def clear_cookies(platform_name: str):
    """Delete the cookie file for a given platform."""
    filepath = _cookie_path(platform_name)
    try:
        os.remove(filepath)
        print(f'[OK] Cookie 已清除: {filepath}')
    except FileNotFoundError:
        pass
