import time
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from core.base_platform import BasePlatform
from core.cookie_manager import save_cookies
from core.chromedriver_util import ensure_chromedriver, find_chrome_executable

try:
    import undetected_chromedriver as uc
    HAS_UC = True
except ImportError:
    HAS_UC = False

try:
    from selenium.webdriver.common.by import By
    from selenium.webdriver.edge.options import Options as EdgeOptions
    from selenium.webdriver.edge.webdriver import WebDriver as EdgeWebDriver
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


_LOGIN_QUERY_PARAMS = (
    ('aid', '6383'),
    ('app_name', 'web_app'),
    ('device_platform', 'webapp'),
    ('channel', 'channel_pc_web'),
    ('pc_client_type', '1'),
    ('version_code', '290100'),
    ('version_name', '29.1.0'),
)


def perform_browser_login(
    platform: BasePlatform,
    status_callback: Optional[callable] = None,
) -> Optional[dict]:
    """Open a browser window for QR-code login.

    Tries undetected_chromedriver first, then Edge/Selenium and Playwright.
    Accepts a status_callback(msg) for GUI progress updates.

    Returns dict of {cookie_name: cookie_value} or None on failure.
    """
    if HAS_UC:
        result = _login_undetected(platform, status_callback)
        if result:
            return result
        _status(status_callback, '[*] uc.Chrome 失败，尝试 Edge/Selenium 兜底...')

    if HAS_SELENIUM:
        result = _login_edge_selenium(platform, status_callback)
        if result:
            return result
        _status(status_callback, '[*] Edge/Selenium 失败，尝试 Playwright 兜底...')

    if HAS_PLAYWRIGHT:
        return _login_playwright(platform, status_callback)

    _status(status_callback, '[!] 未安装任何浏览器自动化库')
    _status(status_callback, '    pip install undetected-chromedriver selenium')
    return None


def _status(cb, msg):
    if cb:
        cb(msg)
    else:
        print(msg)


def _platform_home_url(platform: BasePlatform) -> str:
    return platform.login_url.rstrip('/') + '/'


def _platform_login_url(platform: BasePlatform) -> str:
    return _platform_home_url(platform) + f'login/?{urlencode(_LOGIN_QUERY_PARAMS)}'


def _normalize_platform_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url

    path = parts.path
    while '//' in path:
        path = path.replace('//', '/')
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _is_sso_callback_url(url: str) -> bool:
    return 'passport/sso/login/callback' in url


def _sso_callback_has_ticket(url: str) -> bool:
    if not _is_sso_callback_url(url):
        return False

    tickets = parse_qs(urlsplit(url).query, keep_blank_values=True).get('ticket', [])
    return any(ticket.strip() for ticket in tickets)


def _is_missing_param_error(url: str, page_text: str = '') -> bool:
    text = page_text or ''
    compact_text = text.replace(' ', '')
    return (
        (_is_sso_callback_url(url) and not _sso_callback_has_ticket(url))
        or 'error_code=3' in url
        or '"error_code":3' in compact_text
        or "'error_code':3" in compact_text
        or '缺少参数' in text
    )


def _wait_for_session_cookie(get_cookies_fn, session_names, attempts=10, delay=0.2):
    cookies = []
    for _ in range(attempts):
        cookies = get_cookies_fn()
        found = _find_session_cookie(cookies, session_names)
        if found:
            return cookies, found
        time.sleep(delay)
    return cookies, None


def _selenium_page_text(driver) -> str:
    try:
        return driver.execute_script(
            "return document.body ? document.body.innerText : '';"
        ) or ''
    except Exception:
        return ''


def _playwright_page_text(page) -> str:
    try:
        return page.locator('body').inner_text(timeout=500) or ''
    except Exception:
        return ''


def _click_selenium_login_button(driver) -> bool:
    xpaths = (
        "//span[normalize-space()='登录']",
        "//*[normalize-space()='登录']",
        "//*[contains(normalize-space(),'登录')]",
    )
    for xpath in xpaths:
        try:
            login_btn = WebDriverWait(driver, 4).until(
                EC.element_to_be_clickable((By.XPATH, xpath))
            )
            login_btn.click()
            return True
        except Exception:
            pass

    try:
        elements = driver.find_elements(By.XPATH, "//*[contains(normalize-space(),'登录')]")
        for element in elements[:12]:
            try:
                if element.is_displayed():
                    driver.execute_script("arguments[0].click();", element)
                    return True
            except Exception:
                continue
    except Exception:
        pass

    return False


def _click_playwright_login_button(page) -> bool:
    selectors = (
        "text=登录",
        "xpath=//span[normalize-space()='登录']",
        "xpath=//*[normalize-space()='登录']",
        "xpath=//*[contains(normalize-space(),'登录')]",
    )
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 12)
            for index in range(count):
                item = locator.nth(index)
                if item.is_visible(timeout=500):
                    item.click(timeout=2000)
                    return True
        except Exception:
            pass

    return False


def _handle_login_success(cookies_source, platform, status_cb, closer, get_cookies_fn=None):
    """Save cookies, close browser, return cookie dict. Shared by uc and Playwright paths."""
    login_cookies = [
        c for c in cookies_source
        if platform.cookie_domain in c.get('domain', '')
    ]
    save_cookies(login_cookies, platform.name)
    closer()
    return {c['name']: c['value'] for c in login_cookies}


def _run_selenium_login(driver, platform: BasePlatform, status_cb, browser_name: str) -> Optional[dict]:
    try:
        home_url = _platform_home_url(platform)
        login_url = _platform_login_url(platform)

        driver.get(home_url)
        time.sleep(3)

        try:
            if not _click_selenium_login_button(driver):
                raise Exception('login button not found')
            _status(status_cb, '[*] 已点击登录按钮')
        except Exception:
            _status(status_cb, '[*] 尝试直接打开登录页...')
            driver.get(login_url)

        time.sleep(2)
        _status(status_cb, '\n请在浏览器窗口中使用 App 扫码登录\n')

        start_time = time.time()
        while time.time() - start_time < 120:
            time.sleep(0.5)
            selenium_cookies = driver.get_cookies()
            found = _find_session_cookie(selenium_cookies, platform.session_cookie_names)

            if found:
                elapsed = int(time.time() - start_time)
                _status(status_cb, f'\n[OK] 登录成功！({elapsed}秒)')
                return _handle_login_success(
                    selenium_cookies, platform, status_cb, driver.quit
                )

            # Handle SSO callback edge cases after checking cookies first.
            try:
                current_url = _normalize_platform_url(driver.current_url)
                if current_url != driver.current_url:
                    driver.get(current_url)
                    time.sleep(1)

                if _sso_callback_has_ticket(current_url):
                    _cookies, found = _wait_for_session_cookie(
                        driver.get_cookies,
                        platform.session_cookie_names,
                        attempts=30,
                        delay=0.1,
                    )
                    if not found:
                        driver.execute_script(f"window.location.replace('{home_url}')")
                        _cookies, found = _wait_for_session_cookie(
                            driver.get_cookies,
                            platform.session_cookie_names,
                            attempts=20,
                            delay=0.1,
                        )
                    if found:
                        elapsed = int(time.time() - start_time)
                        _status(status_cb, f'\n[OK] 登录成功！({elapsed}秒)')
                        return _handle_login_success(
                            _cookies, platform, status_cb, driver.quit
                        )
                elif _is_missing_param_error(current_url, _selenium_page_text(driver)):
                    _status(status_cb, '[!] 登录回调参数异常，正在尝试读取 Cookie...')
                    _cookies, found = _wait_for_session_cookie(
                        driver.get_cookies,
                        platform.session_cookie_names,
                        attempts=15,
                        delay=0.2,
                    )
                    if found:
                        elapsed = int(time.time() - start_time)
                        _status(status_cb, f'\n[OK] 登录成功！({elapsed}秒)')
                        return _handle_login_success(
                            _cookies, platform, status_cb, driver.quit
                        )

                    _status(status_cb, '[*] 未取到 Cookie，重新打开登录入口...')
                    driver.get(home_url)
                    time.sleep(2)
                    if not _click_selenium_login_button(driver):
                        driver.get(login_url)
                    time.sleep(2)
                    continue
            except Exception:
                pass

            elapsed = int(time.time() - start_time)
            _status(status_cb, f'\r[*] 等待登录中... ({elapsed}秒/120秒)')

        _status(status_cb, '\n[!] 登录超时')
    except Exception as e:
        _status(status_cb, f'\n[!] {browser_name} 登录过程出错: {e}')
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    return None


def _login_undetected(platform: BasePlatform, status_cb) -> Optional[dict]:
    """Login using undetected_chromedriver."""
    if not HAS_SELENIUM:
        _status(status_cb, '[!] 未安装 selenium，无法启动 uc.Chrome')
        return None

    _status(status_cb, '[*] 正在启动浏览器窗口 (undetected_chromedriver)...')

    try:
        options = uc.ChromeOptions()
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--no-first-run')
        options.add_argument('--no-default-browser-check')
        options.add_argument('--disable-infobars')
        options.add_argument('--disable-notifications')

        chrome_path = find_chrome_executable(status_cb)
        if not chrome_path:
            _status(status_cb, '[!] 未检测到 Google Chrome，跳过 uc.Chrome')
            return None
        _status(status_cb, f'[*] 使用 Chrome 浏览器: {chrome_path}')

        chromedriver_path = ensure_chromedriver(status_cb)
        if chromedriver_path:
            _status(status_cb, f'[*] 使用 ChromeDriver: {chromedriver_path}')
            driver = uc.Chrome(
                options=options, headless=False, use_subprocess=True,
                driver_executable_path=chromedriver_path,
                browser_executable_path=chrome_path,
            )
        else:
            _status(status_cb, '[*] 尝试由 undetected_chromedriver 自动检测 ChromeDriver...')
            driver = uc.Chrome(
                options=options, headless=False, use_subprocess=True,
                browser_executable_path=chrome_path,
            )
    except Exception as e:
        _status(status_cb, f'[!] uc.Chrome 启动失败: {e}')
        return None

    return _run_selenium_login(driver, platform, status_cb, 'uc.Chrome')


def _login_edge_selenium(platform: BasePlatform, status_cb) -> Optional[dict]:
    """Fallback login using Microsoft Edge and Selenium Manager."""
    _status(status_cb, '[*] 正在启动浏览器窗口 (Microsoft Edge)...')

    try:
        options = EdgeOptions()
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--no-first-run')
        options.add_argument('--no-default-browser-check')
        options.add_argument('--disable-infobars')
        options.add_argument('--disable-notifications')
        driver = EdgeWebDriver(options=options)
    except Exception as e:
        _status(status_cb, f'[!] Microsoft Edge 启动失败: {e}')
        _status(status_cb, '[!] 请确认已安装 Microsoft Edge，或安装 Google Chrome 后重试')
        return None

    return _run_selenium_login(driver, platform, status_cb, 'Microsoft Edge')


def _login_playwright(platform: BasePlatform, status_cb) -> Optional[dict]:
    """Fallback login using Playwright."""
    if not HAS_PLAYWRIGHT:
        _status(status_cb, '[!] 未安装 playwright')
        return None

    _status(status_cb, '[*] 正在启动浏览器窗口 (Playwright 降级方案)...')

    channels = ['chrome', 'msedge', 'chromium']
    try:
        with sync_playwright() as p:
            browser = None
            for channel in channels:
                try:
                    browser = p.chromium.launch(
                        channel=channel, headless=False,
                        args=['--disable-blink-features=AutomationControlled']
                    )
                    _status(status_cb, f'[*] 已打开 {channel}')
                    break
                except Exception:
                    continue

            if not browser:
                _status(status_cb, '[!] 无法启动系统浏览器')
                return None

            context = browser.new_context(viewport={'width': 1280, 'height': 720}, locale='zh-CN')
            page = context.new_page()

            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
            """)
            page.set_extra_http_headers({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept-Language': 'zh-CN,zh;q=0.9',
            })

            home_url = _platform_home_url(platform)
            login_url = _platform_login_url(platform)

            page.goto(home_url, wait_until='networkidle', timeout=30000)
            page.wait_for_timeout(2000)

            try:
                if not _click_playwright_login_button(page):
                    raise Exception('login button not found')
                page.wait_for_timeout(3000)
                _status(status_cb, '[*] 已点击登录按钮')
            except Exception:
                _status(status_cb, '[*] 尝试直接打开登录页...')
                page.goto(login_url, wait_until='domcontentloaded', timeout=30000)

            _status(status_cb, '\n请在浏览器窗口中使用 App 扫码登录\n')

            start_time = time.time()
            while time.time() - start_time < 120:
                for _ in range(10):
                    time.sleep(0.2)
                    cookies = context.cookies()
                    found = _find_session_cookie(cookies, platform.session_cookie_names)
                    if found:
                        elapsed = int(time.time() - start_time)
                        _status(status_cb, f'\n[OK] 登录成功！({elapsed}秒)')
                        return _handle_login_success(
                            cookies, platform, status_cb, browser.close
                        )

                    try:
                        current_url = _normalize_platform_url(page.url)
                        if current_url != page.url:
                            page.goto(current_url, wait_until='domcontentloaded')
                            page.wait_for_timeout(1000)

                        if _sso_callback_has_ticket(current_url):
                            cookies, found = _wait_for_session_cookie(
                                context.cookies,
                                platform.session_cookie_names,
                                attempts=15,
                                delay=0.2,
                            )
                            if found:
                                elapsed = int(time.time() - start_time)
                                _status(status_cb, f'\n[OK] 登录成功！({elapsed}秒)')
                                return _handle_login_success(
                                    cookies, platform, status_cb, browser.close
                                )

                            page.goto(home_url, wait_until='domcontentloaded')
                            page.wait_for_timeout(2000)
                            cookies, found = _wait_for_session_cookie(
                                context.cookies,
                                platform.session_cookie_names,
                                attempts=10,
                                delay=0.2,
                            )
                            if found:
                                elapsed = int(time.time() - start_time)
                                _status(status_cb, f'\n[OK] 登录成功！({elapsed}秒)')
                                return _handle_login_success(
                                    cookies, platform, status_cb, browser.close
                                )
                        elif _is_missing_param_error(current_url, _playwright_page_text(page)):
                            _status(status_cb, '[!] 登录回调参数异常，正在尝试读取 Cookie...')
                            cookies, found = _wait_for_session_cookie(
                                context.cookies,
                                platform.session_cookie_names,
                                attempts=15,
                                delay=0.2,
                            )
                            if found:
                                elapsed = int(time.time() - start_time)
                                _status(status_cb, f'\n[OK] 登录成功！({elapsed}秒)')
                                return _handle_login_success(
                                    cookies, platform, status_cb, browser.close
                                )

                            _status(status_cb, '[*] 未取到 Cookie，重新打开登录入口...')
                            page.goto(home_url, wait_until='domcontentloaded')
                            page.wait_for_timeout(2000)
                            if not _click_playwright_login_button(page):
                                page.goto(login_url, wait_until='domcontentloaded')
                            page.wait_for_timeout(2000)
                            continue
                    except Exception:
                        pass

                elapsed = int(time.time() - start_time)
                _status(status_cb, f'\r[*] 等待登录中... ({elapsed}秒/120秒)')

            _status(status_cb, '\n[!] 登录超时')
            return None
    except Exception as e:
        _status(status_cb, f'\n[!] 浏览器登录失败: {e}')
        return None


def _find_session_cookie(cookies, session_names):
    for c in cookies:
        if c['name'] in session_names and c.get('value'):
            return c
    return None
