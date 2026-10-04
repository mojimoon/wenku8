"""
wenku8 页面抓取：多种方式可切换，供 main.py / utils/catalog.py / gen_epub.py / build_batch.py 共用。

抓取方式（LEVELS，按"成本从低到高"排列，失败时可自动向后升级）:
    requests    普通 requests。本机/国内 IP 可用（需 UA 为简短的 Mozilla/5.0，完整浏览器 UA 反而会被 challenge）
    curl_cffi   模拟 Chrome TLS 指纹。GitHub Actions 等数据中心 IP 上实测可通过 Cloudflare（推荐）
    playwright  本地 headless Chromium
    steel       Steel 云端浏览器（需 STEEL_API_KEY），最稳但有额度限制

用法:
    fetcher = Fetcher('curl_cffi', fallback=True)   # 从 curl_cffi 起步，失败时升级到 playwright / steel
    html = fetcher.get(url, validate=lambda h: 'xxx' in h, encoding='gbk')
    fetcher.close()

scraper 取值: 'auto'（从 requests 起步并允许升级）或 LEVELS 中的任一项（默认不升级，fallback=True 则允许）。
"""

import os
import random
import time

import requests

DOMAIN = 'https://www.wenku8.net'
COOKIE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'COOKIE')

# 注意：wenku8 的 Cloudflare 会对"完整浏览器 UA"弹 challenge，而简短的 Mozilla/5.0 可直接通过
REQUESTS_UA = 'Mozilla/5.0'
DELAY = (0.8, 1.8)       # 请求间隔（秒）
LEVELS = ['requests', 'curl_cffi', 'playwright', 'steel']
STEEL_TIMEOUT = 15 * 60  # Steel 会话最长存活秒数


def read_cookie() -> dict:
    """读取项目根目录 COOKIE 文件（单行 "k1=v1; k2=v2"）；也可用环境变量 WENKU8_COOKIE。"""
    line = os.environ.get('WENKU8_COOKIE', '')
    if not line and os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
            line = f.readline()
    cookies = {}
    for part in line.strip().split(';'):
        if '=' in part:
            k, v = part.split('=', 1)
            cookies[k.strip()] = v.strip()
    return cookies


class LoginExpired(RuntimeError):
    pass


IMPERSONATE = ('chrome', 'edge', 'safari', 'firefox')

class FetchError(RuntimeError):
    pass


class RateLimited(FetchError):
    def __init__(self, retry_after: float = 0):
        super().__init__('HTTP 429')
        self.retry_after = retry_after


def is_challenge(html: str) -> bool:
    return 'Just a moment' in html[:2000] or ('Ray ID' in html and 'cloudflare' in html.lower() and len(html) < 20000)


class Fetcher:
    def __init__(self, scraper: str = 'auto', fallback: bool | None = None, delay: tuple = DELAY):
        if scraper != 'auto' and scraper not in LEVELS:
            raise ValueError(f'未知爬虫方式: {scraper}（可选: auto, {", ".join(LEVELS)}）')
        self.cookies = read_cookie()
        self._imp = 0
        self.fallback = (scraper == 'auto') if fallback is None else fallback
        self.level = 0 if scraper == 'auto' else LEVELS.index(scraper)
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': REQUESTS_UA, 'Referer': DOMAIN + '/'})
        self.session.cookies.update(self.cookies)
        self._cffi = None
        self._pw = None
        self._browser = None
        self._context = None
        self._steel = None  # (client, session_id)
        self._last = 0.0
        self._delay_scale = 1.0  # 触发 429 后放慢请求节奏

    @property
    def scraper(self) -> str:
        return LEVELS[self.level]

    # --- 各级实现 ---

    def _get_requests(self, url: str, encoding: str, impersonate: bool) -> str:
        if impersonate:
            # curl_cffi 模拟浏览器 TLS 指纹，可通过数据中心 IP（如 GitHub Actions）上的 Cloudflare 检测。
            # Cloudflare 有时只拦截部分指纹（如论坛帖页拦 chrome、放行 edge）：403 时换下一个指纹，成功的沿用
            for _ in IMPERSONATE:
                if self._cffi is None:
                    from curl_cffi import requests as cffi
                    self._cffi = cffi.Session(impersonate=IMPERSONATE[self._imp], headers={'Referer': DOMAIN + '/'})
                    self._cffi.cookies.update(self.cookies)
                resp = self._cffi.get(url, timeout=20, allow_redirects=True)
                if resp.status_code != 403:
                    break
                self._imp = (self._imp + 1) % len(IMPERSONATE)
                self._cffi.close()
                self._cffi = None
                print(f'[WARN] curl_cffi 403，改用指纹 {IMPERSONATE[self._imp]}')
        else:
            resp = self.session.get(url, timeout=15, allow_redirects=True)
        if '/login.php' in resp.url:
            raise LoginExpired(f'被重定向到登录页，请更新 COOKIE: {resp.url}')
        if resp.status_code == 429:
            ra = resp.headers.get('Retry-After', '')
            raise RateLimited(float(ra) if ra.isdigit() else 0)
        if resp.status_code != 200:
            raise FetchError(f'HTTP {resp.status_code}')
        resp.encoding = encoding
        return resp.text

    def _open_context(self, steel: bool):
        from playwright.sync_api import sync_playwright
        if self._pw is None:
            self._pw = sync_playwright().start()
        if steel:
            from steel import Steel
            from dotenv import dotenv_values
            key = dotenv_values().get('STEEL_API_KEY', '') or os.environ.get('STEEL_API_KEY', '')
            if not key:
                raise FetchError('缺少 STEEL_API_KEY')
            client = Steel(steel_api_key=key)
            sess = client.sessions.create(api_timeout=STEEL_TIMEOUT * 1000)
            print(f'[INFO] Steel session: {sess.id}')
            self._steel = (client, sess.id)
            self._browser = self._pw.chromium.connect_over_cdp(
                f'wss://connect.steel.dev?apiKey={key}&sessionId={sess.id}')
            self._context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
        else:
            self._browser = self._pw.chromium.launch(
                headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
            self._context = self._browser.new_context(user_agent=REQUESTS_UA)
        if self.cookies:
            self._context.add_cookies([
                {'name': k, 'value': v, 'domain': 'www.wenku8.net', 'path': '/'}
                for k, v in self.cookies.items()])

    def _get_browser(self, url: str, steel: bool) -> str:
        if self._context is None:
            self._open_context(steel)
        page = self._context.new_page()
        try:
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=30000)
            except Exception as e:
                print(f'[WARN] goto: {e}')
            # 等待 Cloudflare challenge 通过
            for _ in range(20):
                if 'Just a moment' not in page.title():
                    break
                time.sleep(1)
            if '/login.php' in page.url:
                raise LoginExpired(f'被重定向到登录页，请更新 COOKIE: {page.url}')
            return page.content()
        finally:
            page.close()

    # --- 对外接口 ---

    def get(self, url: str, validate=None, encoding: str = 'utf-8') -> str:
        """抓取 url；validate(html)->bool 用于判断内容是否是期望页面。失败时按 fallback 设置自动升级。"""
        wait = (self.delay[0] + random.random() * (self.delay[1] - self.delay[0])) * self._delay_scale \
            - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        try:
            rl_count = 0
            while True:
                name = self.scraper
                last_err = None
                attempt = 0
                while attempt < 2:
                    attempt += 1
                    try:
                        if name in ('requests', 'curl_cffi'):
                            html = self._get_requests(url, encoding, impersonate=(name == 'curl_cffi'))
                        else:
                            html = self._get_browser(url, steel=(name == 'steel'))
                        if is_challenge(html):
                            raise FetchError('Cloudflare challenge')
                        if validate and not validate(html):
                            raise FetchError('页面内容不符合预期')
                        return html
                    except LoginExpired:
                        raise
                    except RateLimited as e:
                        # 被限流：退避等待后重试（不计入失败次数），并放慢后续节奏
                        rl_count += 1
                        if rl_count > 6:
                            last_err = e
                            break
                        wait_s = max(e.retry_after, min(15 * 2 ** (rl_count - 1), 180))
                        self._delay_scale = min(self._delay_scale * 1.5, 4.0)
                        print(f'[WARN] 429 限流，等待 {wait_s:.0f}s 后重试 (节奏 x{self._delay_scale:.1f})')
                        time.sleep(wait_s)
                        attempt -= 1
                    except Exception as e:
                        last_err = e
                        print(f'[WARN] {name} 第 {attempt} 次失败: {e}')
                        time.sleep(2 + attempt * 3)
                if self.fallback and self.level < len(LEVELS) - 1:
                    self.level += 1
                    print(f'[INFO] 升级爬虫: {self.scraper}')
                    continue
                raise FetchError(f'{name} 抓取失败: {last_err}')
        finally:
            self._last = time.time()

    def close(self):
        for fn in (lambda: self._browser and self._browser.close(),
                   lambda: self._steel and self._steel[0].sessions.release(self._steel[1]),
                   lambda: self._pw and self._pw.stop()):
            try:
                fn()
            except Exception:
                pass
        self._browser = self._context = self._steel = self._pw = None
