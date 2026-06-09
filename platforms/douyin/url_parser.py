import re
import requests
from urllib.parse import parse_qs, unquote, urlparse


def _resolve_short_url(url: str):
    """Resolve a v.douyin.com short URL to its canonical form."""
    url = url.strip().strip('"\'')
    if 'v.douyin.com' in url:
        try:
            r = requests.get(url, allow_redirects=True, timeout=10,
                             headers={'User-Agent': 'Mozilla/5.0'})
            return r.url
        except Exception:
            pass
    return url


def extract_aweme_id(url: str):
    """Extract numeric video/note ID from a Douyin share URL."""
    url = _resolve_short_url(url)
    m = re.search(r'douyin\.com/(?:video|note)/(\d+)', url)
    if m:
        return m.group(1)
    if re.search(r'(?:douyin|iesdouyin)\.com/(?:share/)?user/', url):
        return None
    # Fallback: modal_id query param (/jingxuan?modal_id=xxx, /discover?modal_id=xxx, etc.)
    m = re.search(r'[?&]modal_id=(\d+)', url)
    return m.group(1) if m else None


def extract_sec_user_id(url: str):
    """Extract sec_user_id from a Douyin creator homepage URL."""
    url = _resolve_short_url(url)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    for key in ('sec_uid', 'sec_user_id'):
        values = query.get(key)
        if values and values[0]:
            return unquote(values[0])

    m = re.search(r'(?:douyin|iesdouyin)\.com/(?:share/)?user/([^/?#]+)', url)
    if m:
        sec_user_id = unquote(m.group(1))
        return sec_user_id if sec_user_id != 'self' else None

    return None


def sanitize_filename(name: str) -> str:
    """Strip filesystem-unsafe characters and truncate."""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:80] or 'untitled'


def resolve_short_url(url: str):
    """Follow short URL redirect to get canonical URL. Public alias."""
    return _resolve_short_url(url)
