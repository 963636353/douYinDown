#!/usr/bin/env python3
"""
抖音视频下载器 (a_bogus 签名版 + 浏览器扫码登录)

使用方法:
  python douyin_downloader.py

首次使用会自动打开浏览器窗口，用抖音 App 扫码即可自动登录。
之后会自动复用已保存的 Cookie。

依赖:
  pip install f2 requests undetected-chromedriver selenium
"""

import os
import re
import sys
import time
import stat
import zipfile
import shutil
import subprocess
import urllib.request
import requests

from f2.utils.abogus import ABogus, BrowserFingerprintGenerator

COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'douyin_cookies.txt')
DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'downloads')

try:
    import undetected_chromedriver as uc
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    HAS_UC = True
except ImportError:
    HAS_UC = False

# 降级方案：Playwright（如果 undetected_chromedriver 不可用）
try:
    from playwright.sync_api import sync_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


def _get_chrome_version():
    """获取当前系统的 Chrome 版本号"""
    try:
        chrome_path = uc.find_chrome_executable()
        # 用 PowerShell 读取文件版本信息（避免直接调用 exe）
        result = subprocess.run(
            ['powershell', '-Command',
             f'(Get-Item "{chrome_path}").VersionInfo.ProductVersion'],
            capture_output=True, text=True, timeout=10
        )
        ver = result.stdout.strip()
        if ver:
            return ver
    except Exception:
        pass
    return None


def _download_chromedriver(version, cache_dir):
    """从多个源下载匹配的 ChromeDriver，返回路径或 None"""
    os.makedirs(cache_dir, exist_ok=True)
    major = version.split('.')[0]

    # 下载源列表（按优先级）
    sources = [
        # 源1: Google Chrome for Testing API（新版 ChromeDriver，需要梯子或运气）
        {
            'url': f'https://storage.googleapis.com/chrome-for-testing-public/{version}/win32/chromedriver-win32.zip',
            'subdir': 'chromedriver-win32',
        },
        # 源2: Google API 备用（不同路径格式）
        {
            'url': f'https://www.googleapis.com/download/storage/v1/b/chrome-for-testing-public/o/{version}%2Fwin32%2Fchromedriver-win32.zip?alt=media',
            'subdir': 'chromedriver-win32',
        },
    ]

    for source in sources:
        try:
            url = source['url']
            subdir = source['subdir']
            print(f'[*] 尝试下载 ChromeDriver: {url[:60]}...')

            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=120)
            data = resp.read()

            zip_path = os.path.join(cache_dir, 'chromedriver.zip')
            with open(zip_path, 'wb') as f:
                f.write(data)

            with zipfile.ZipFile(zip_path, 'r') as z:
                z.extractall(cache_dir)

            driver_path = os.path.join(cache_dir, subdir, 'chromedriver.exe')
            if not os.path.exists(driver_path):
                # 可能直接解压在根目录
                for root, dirs, files in os.walk(cache_dir):
                    if 'chromedriver.exe' in files:
                        driver_path = os.path.join(root, 'chromedriver.exe')
                        break

            if os.path.exists(driver_path):
                os.chmod(driver_path, os.stat(driver_path).st_mode | stat.S_IEXEC)
                print(f'[OK] ChromeDriver 下载完成: {driver_path}')
                return driver_path

        except Exception as e:
            print(f'[*] 此源失败: {e}')
            continue

    # 源3: 兜底 — 使用当前已下载的（如果版本接近也能用）
    existing = os.path.expanduser(f'~/.chromedriver_{major}/chromedriver-win32/chromedriver.exe')
    if os.path.exists(existing):
        print(f'[*] 使用已缓存的 ChromeDriver: {existing}')
        return existing

    return None


def ensure_chromedriver():
    """确保 ChromeDriver 可用，自动下载。返回路径或 None"""
    # 1. 检查 PATH
    path = shutil.which('chromedriver')
    if path:
        print(f'[*] 使用系统 PATH 中的 ChromeDriver: {path}')
        return path

    # 2. 获取 Chrome 版本
    chrome_ver = _get_chrome_version()
    if not chrome_ver:
        print('[!] 无法检测 Chrome 版本')
        return None

    major = chrome_ver.split('.')[0]
    cache_dir = os.path.expanduser(f'~/.chromedriver_{major}')

    # 3. 检查本地缓存
    cached_path = os.path.join(cache_dir, 'chromedriver-win32', 'chromedriver.exe')
    if os.path.exists(cached_path):
        print(f'[*] 使用缓存 ChromeDriver ({chrome_ver}): {cached_path}')
        return cached_path

    # 4. 下载
    print(f'[*] 未找到 ChromeDriver v{major}，正在下载（国内网络可能较慢）...')
    return _download_chromedriver(chrome_ver, cache_dir)


def load_cookies(filepath):
    """从 Netscape 格式的 cookies.txt 加载 cookies"""
    cookies = {}
    try:
        with open(filepath, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) >= 7:
                    cookies[parts[5]] = parts[6]
    except FileNotFoundError:
        return None
    return cookies


def save_cookies_to_file(cookies, filepath):
    """将 cookie 列表保存为 Netscape 格式文件（兼容 Selenium 和 Playwright 格式）"""
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write('# Netscape HTTP Cookie File\n')
        f.write('# https://www.douyin.com\n\n')
        for c in cookies:
            domain = c.get('domain', '.douyin.com')
            if not domain.startswith('.'):
                domain = '.' + domain
            path = c.get('path', '/')
            secure = 'TRUE' if c.get('secure', False) else 'FALSE'
            # Selenium 用 expiry，Playwright 用 expires
            exp = str(int(c.get('expiry') or c.get('expires') or time.time() + 86400 * 30))
            f.write(f'{domain}\tTRUE\t{path}\t{secure}\t{exp}\t{c["name"]}\t{c["value"]}\n')
    print(f'[OK] Cookie 已保存到 {filepath}')


def perform_browser_login():
    """打开浏览器 → 用户扫码登录 → 自动获取 Cookie（使用 undetected_chromedriver 绕过检测）"""
    if not HAS_UC:
        print('[!] 未安装 undetected_chromedriver')
        print('    pip install undetected-chromedriver selenium')
        # 降级到 Playwright
        return _perform_login_playwright()

    print('[*] 正在启动浏览器窗口（undetected_chromedriver）...')

    try:
        options = uc.ChromeOptions()
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--no-first-run')
        options.add_argument('--no-default-browser-check')
        options.add_argument('--disable-infobars')
        options.add_argument('--disable-notifications')

        # 自动检测/下载 ChromeDriver
        chromedriver_path = ensure_chromedriver()
        if chromedriver_path:
            print(f'[*] 使用 ChromeDriver: {chromedriver_path}')
            driver = uc.Chrome(
                options=options,
                headless=False,
                use_subprocess=True,
                driver_executable_path=chromedriver_path,
            )
        else:
            print('[*] 未找到 ChromeDriver，尝试自动检测...')
            driver = uc.Chrome(
                options=options,
                headless=False,
                use_subprocess=True,
            )
    except Exception as e:
        print(f'[!] uc.Chrome 启动失败: {e}')
        print('[*] 降级到 Playwright...')
        return _perform_login_playwright()

    try:
        driver.get('https://www.douyin.com/')
        time.sleep(3)

        # 尝试点击登录按钮
        try:
            login_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//span[contains(text(),'登录')]"))
            )
            login_btn.click()
            print('[*] 已点击登录按钮')
        except Exception:
            # 如果按钮点不到，直接访问带参登录页
            print('[*] 尝试直接打开登录页...')
            driver.get('https://www.douyin.com/login/?aid=6383&app_name=web_app&device_platform=webapp')

        time.sleep(2)

        print()
        print('=' * 50)
        print('  请在浏览器窗口中使用抖音 App 扫码登录')
        print('  登录后脚本将自动检测并继续')
        print('=' * 50)
        print()

        # 轮询检测登录状态（每 0.5 秒检测一次）
        start_time = time.time()
        while time.time() - start_time < 120:
            time.sleep(0.5)

            # 先检测 Cookie！扫码成功后 Cookie 可能在 SSO 跳转前就已就绪
            selenium_cookies = driver.get_cookies()
            found = None
            for c in selenium_cookies:
                if c['name'] in ('sessionid', 'sid_tt') and c.get('value'):
                    found = c
                    break

            if found:
                elapsed = int(time.time() - start_time)
                print(f'\n[OK] 登录成功！({elapsed}秒)')

                login_cookies = [
                    c for c in selenium_cookies
                    if 'douyin.com' in c.get('domain', '') or 'iesdouyin.com' in c.get('domain', '')
                ]

                save_cookies_to_file(login_cookies, COOKIES_FILE)
                driver.quit()
                return {c['name']: c['value'] for c in login_cookies}

            # Cookie 未就绪，检测是否进入了 SSO 回调错误页
            try:
                _url = driver.current_url
                if 'passport/sso/login/callback' in _url and 'ticket=' in _url:
                    # 先在当前页面快速轮询 1 秒（Cookie 可能已在浏览器中）
                    for _ in range(10):
                        time.sleep(0.1)
                        _cookies = driver.get_cookies()
                        for _c in _cookies:
                            if _c['name'] in ('sessionid', 'sid_tt') and _c.get('value'):
                                found = _c
                                break
                        if found:
                            break

                    if not found:
                        # 还没找到，用 JS 快速导航（不等待页面完全加载）
                        driver.execute_script("window.location.replace('https://www.douyin.com/')")
                        # 导航后密集轮询 2 秒等待 Cookie 就绪
                        for _ in range(20):
                            time.sleep(0.1)
                            _cookies = driver.get_cookies()
                            for _c in _cookies:
                                if _c['name'] in ('sessionid', 'sid_tt') and _c.get('value'):
                                    found = _c
                                    break
                            if found:
                                break

                    if found:
                        elapsed = int(time.time() - start_time)
                        print(f'\n[OK] 登录成功！({elapsed}秒)')
                        login_cookies = [
                            c for c in driver.get_cookies()
                            if 'douyin.com' in c.get('domain', '') or 'iesdouyin.com' in c.get('domain', '')
                        ]
                        save_cookies_to_file(login_cookies, COOKIES_FILE)
                        driver.quit()
                        return {c['name']: c['value'] for c in login_cookies}

                    # 回到主循环继续
                    continue
            except Exception:
                pass

            elapsed = int(time.time() - start_time)
            print(f'\r[*] 等待登录中... ({elapsed}秒/120秒)', end='')

        print('\n[!] 登录超时')
    except Exception as e:
        print(f'\n[!] 登录过程出错: {e}')
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    return None


def _perform_login_playwright():
    """降级方案：用 Playwright 登录（原始方案 + 隐藏自动化标记）"""
    if not HAS_PLAYWRIGHT:
        print('[!] 也未安装 playwright，无法启动浏览器')
        print('    请运行: pip install undetected-chromedriver selenium')
        return None

    print('[*] 正在启动浏览器窗口（Playwright 降级方案）...')
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
                    print(f'[*] 已打开 {channel}')
                    break
                except Exception:
                    continue

            if not browser:
                print('[!] 无法启动系统浏览器')
                return None

            context = browser.new_context(
                viewport={'width': 1280, 'height': 720},
                locale='zh-CN'
            )
            page = context.new_page()

            # 注入脚本隐藏自动化标记
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
            """)

            page.set_extra_http_headers({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
                'Accept-Language': 'zh-CN,zh;q=0.9',
            })

            # 访问首页
            page.goto('https://www.douyin.com/', wait_until='networkidle', timeout=30000)
            page.wait_for_timeout(2000)

            try:
                login_btn = page.locator('text=登录').first
                if login_btn.is_visible(timeout=3000):
                    login_btn.click()
                    page.wait_for_timeout(3000)
                    print('[*] 已点击登录按钮')
                else:
                    raise Exception('not visible')
            except Exception:
                print('[*] 尝试直接打开登录页...')
                page.goto('https://www.douyin.com/login/?aid=6383&app_name=web_app&device_platform=webapp&channel=channel_pc_web&pc_client_type=1&version_code=290100&version_name=29.1.0', wait_until='domcontentloaded', timeout=30000)

            print()
            print('=' * 50)
            print('  请在浏览器窗口中使用抖音 App 扫码登录')
            print('  登录后脚本将自动检测并继续')
            print('=' * 50)
            print()

            start_time = time.time()
            while time.time() - start_time < 120:
                for _ in range(10):
                    time.sleep(0.2)

                    # 检测 SSO 回调错误页，跳回首页取 cookie
                    try:
                        _u = page.url
                        if 'passport/sso/login/callback' in _u and 'ticket=' in _u:
                            print('\n[*] SSO 回调缺少 ticket，跳回首页取 Cookie...')
                            page.goto('https://www.douyin.com/', wait_until='domcontentloaded')
                            time.sleep(2)
                    except Exception:
                        pass

                    cookies = context.cookies()
                    for c in cookies:
                        if c['name'] in ('sessionid', 'sid_tt') and c.get('value'):
                            elapsed = int(time.time() - start_time)
                            print(f'\n[OK] 登录成功！({elapsed}秒)')
                            login_cookies = [
                                c for c in cookies
                                if 'douyin.com' in c.get('domain', '') or 'iesdouyin.com' in c.get('domain', '')
                            ]
                            save_cookies_to_file(login_cookies, COOKIES_FILE)
                            browser.close()
                            return {c['name']: c['value'] for c in login_cookies}

                elapsed = int(time.time() - start_time)
                print(f'\r[*] 等待登录中... ({elapsed}秒/120秒)', end='')

            print('\n[!] 登录超时')
            return None
    except Exception as e:
        print(f'\n[!] 浏览器登录失败: {e}')
        return None


def try_load_or_login():
    """尝试加载已有 Cookie，没有则通过浏览器登录"""
    cookies = load_cookies(COOKIES_FILE)
    if cookies:
        print(f'[OK] 已加载 {len(cookies)} 个 cookie')
        return cookies

    print('[*] 未找到 Cookie 文件，需要登录抖音账号')
    print('[*] 登录后 Cookie 将自动保存，下次无需重复登录')
    print()

    # 重试循环
    for attempt in range(3):
        cookies = perform_browser_login()
        if cookies:
            print(f'[OK] 登录成功，已获取 {len(cookies)} 个 cookie')
            return cookies
        if attempt < 2:
            print()
            print('[*] 重试前请确保已在浏览器中完成登录')
        else:
            print()
            print('[!] 多次登录失败')
            print('    方式一: 安装 Chrome 或 Edge 后重试')
            print('    方式二: 手动导出抖音 Cookie 保存为 douyin_cookies.txt')
            sys.exit(1)


def extract_aweme_id(url):
    """从抖音分享链接中提取视频 ID"""
    url = url.strip().strip('"\'')
    # Short URL: v.douyin.com/xxxx
    if 'v.douyin.com' in url:
        try:
            r = requests.get(url, allow_redirects=True, timeout=10,
                             headers={'User-Agent': 'Mozilla/5.0'})
            url = r.url
        except:
            pass
    # Full URL: douyin.com/video/xxxx or douyin.com/note/xxxx
    m = re.search(r'douyin\.com/(?:video|note)/(\d+)', url)
    if m:
        return m.group(1)
    return None


def generate_abogus(params_str, user_agent):
    """生成 a_bogus 签名"""
    fp = BrowserFingerprintGenerator.generate_fingerprint('Chrome')
    ab = ABogus(user_agent=user_agent, fp=fp)
    return ab.generate_abogus(params_str, 'GET')[0]


def build_params(aweme_id):
    """构建 API 请求参数"""
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


def sanitize_filename(name):
    """清理文件名中的非法字符"""
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:80] or 'untitled'


def download_video(video_url, filepath, headers):
    """下载视频文件"""
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    r = requests.get(video_url, headers=headers, stream=True, timeout=60)
    if r.status_code != 200:
        raise Exception(f'下载失败: HTTP {r.status_code}')
    total = int(r.headers.get('Content-Length', 0))
    downloaded = 0
    with open(filepath, 'wb') as f:
        for chunk in r.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f'\r  下载进度: {pct}% ({downloaded//1024**2}MB/{total//1024**2}MB)', end='')
    print()
    return downloaded


def main():
    if not os.path.exists(DOWNLOAD_DIR):
        os.makedirs(DOWNLOAD_DIR)

    print('=' * 50)
    print('  抖音视频下载器')
    print('=' * 50)
    print()

    cookies = try_load_or_login()
    print()

    while True:
        raw = input('>> 粘贴抖音分享链接 (q 退出) > ').strip()
        if raw.lower() in ('q', 'quit', 'exit', ''):
            print('再见!')
            break

        aweme_id = extract_aweme_id(raw)
        if not aweme_id:
            print('[!] 无法识别链接，请粘贴抖音分享链接')
            print('    例如: https://v.douyin.com/xxxx/')
            continue

        print(f'[*] 视频 ID: {aweme_id}')
        print(f'[*] 正在获取视频信息...')

        # 构建请求
        params = build_params(aweme_id)
        params_str = '&'.join([f'{k}={v}' for k, v in params.items()])
        ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36'

        # 生成 a_bogus 签名
        signed_params = generate_abogus(params_str, ua)
        api_url = f'https://www.douyin.com/aweme/v1/web/aweme/detail/?{signed_params}'

        headers = {
            'User-Agent': ua,
            'Referer': 'https://www.douyin.com/',
            'Accept': 'application/json, text/plain, */*',
        }

        # 调用 API
        try:
            r = requests.get(api_url, headers=headers, cookies=cookies, timeout=15)
            data = r.json()
        except Exception as e:
            print(f'[!] API 请求失败: {e}')
            continue

        if data.get('status_code') != 0:
            print(f'[!] API 返回错误: status_code={data.get("status_code")}')
            continue

        detail = data.get('aweme_detail')
        if not detail:
            reason = data.get('filter_detail', {}).get('filter_reason', 'unknown')
            print(f'[!] 视频不可用 (filter_reason: {reason})')
            continue

        # 提取视频信息
        desc = detail.get('desc', '无标题') or '无标题'
        video_info = detail.get('video', {})
        play_addr = video_info.get('play_addr', {})

        # 取最佳质量的视频 URL
        url_list = play_addr.get('url_list', [])
        if not url_list:
            print('[!] 未找到视频下载链接')
            continue

        video_url = url_list[0]
        print(f'[*] 标题: {desc[:60]}')
        print(f'[*] 开始下载...')

        # 下载视频
        filename = f'{sanitize_filename(desc)}_{aweme_id}.mp4'
        filepath = os.path.join(DOWNLOAD_DIR, filename)

        try:
            size = download_video(video_url, filepath, headers)
            print(f'[OK] 下载完成: {filepath}')
            print(f'    大小: {size / 1024 / 1024:.1f} MB')
        except Exception as e:
            print(f'[!] 下载失败: {e}')

        print()


if __name__ == '__main__':
    main()
