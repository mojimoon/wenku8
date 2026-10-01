"""
GitHub Actions 可行性探针 v2：在 runner 上用多种方案访问 wenku8，成功退出码 0，被拦截退出码 1。
用法: python probe_actions.py <strategy>
策略: requests | curl_cffi | chrome_headed | patchright | camoufox | steel | worker
"""
import os
import sys
import time

import requests

AID = 129
BOOK = f'https://www.wenku8.net/book/{AID}.htm'
TOC = f'https://www.wenku8.net/novel/0/{AID}/index.htm'
UA = 'Mozilla/5.0'
COOKIE = open('COOKIE', encoding='utf-8').readline().strip() if os.path.exists('COOKIE') else ''


def ok_page(html: str) -> bool:
    return '小说作者' in html or 'vcss' in html


def decode(url: str, raw: bytes) -> str:
    return raw.decode('utf-8' if url == BOOK else 'gbk', 'replace')


def log(*a):
    print(*a, flush=True)


def run_requests() -> bool:
    for url in (BOOK, TOC):
        r = requests.get(url, headers={'User-Agent': UA, 'Cookie': COOKIE}, timeout=20)
        log(url, r.status_code, r.headers.get('cf-mitigated'))
        if not ok_page(decode(url, r.content)):
            return False
    return True


def run_curl_cffi() -> bool:
    from curl_cffi import requests as cr
    for url in (BOOK, TOC):
        r = cr.get(url, impersonate='chrome', headers={'Cookie': COOKIE}, timeout=20)
        log(url, r.status_code, r.headers.get('cf-mitigated'))
        if not ok_page(decode(url, r.content)):
            return False
    return True


def browse(page) -> bool:
    for url in (BOOK, TOC):
        page.goto(url, wait_until='domcontentloaded', timeout=45000)
        for _ in range(30):
            if 'Just a moment' not in page.title():
                break
            time.sleep(1)
        log(url, page.title()[:30])
        if not ok_page(page.content()):
            return False
    return True


def add_cookies(ctx):
    pairs = [c.strip().split('=', 1) for c in COOKIE.split(';') if '=' in c]
    if pairs:
        ctx.add_cookies([{'name': k, 'value': v, 'domain': 'www.wenku8.net', 'path': '/'} for k, v in pairs])


def run_chrome_headed() -> bool:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel='chrome', headless=False,
                              args=['--no-sandbox', '--disable-blink-features=AutomationControlled'])
        ctx = b.new_context(user_agent=UA)
        add_cookies(ctx)
        res = browse(ctx.new_page())
        b.close()
        return res


def run_patchright() -> bool:
    from patchright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel='chrome', headless=False, args=['--no-sandbox'])
        ctx = b.new_context(no_viewport=True)
        add_cookies(ctx)
        res = browse(ctx.new_page())
        b.close()
        return res


def run_camoufox() -> bool:
    from camoufox.sync_api import Camoufox
    with Camoufox(headless='virtual') as b:
        ctx = b.new_context()
        add_cookies(ctx)
        return browse(ctx.new_page())


def run_steel() -> bool:
    from steel import Steel
    from playwright.sync_api import sync_playwright
    key = os.environ['STEEL_API_KEY']
    client = Steel(steel_api_key=key)
    s = client.sessions.create(api_timeout=120000)
    try:
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp(f'wss://connect.steel.dev?apiKey={key}&sessionId={s.id}')
            ctx = b.contexts[0] if b.contexts else b.new_context()
            add_cookies(ctx)
            res = browse(ctx.new_page())
            b.close()
            return res
    finally:
        client.sessions.release(s.id)


def run_worker() -> bool:
    base, token = os.environ['WORKER_URL'], os.environ.get('WORKER_TOKEN', '')
    for url in (BOOK, TOC):
        r = requests.get(base, params={'url': url}, headers={'X-Token': token, 'X-Cookie': COOKIE}, timeout=30)
        log(url, r.status_code)
        if not ok_page(decode(url, r.content)):
            return False
    return True


if __name__ == '__main__':
    name = sys.argv[1]
    try:
        log('IP:', requests.get('https://ipinfo.io/ip', timeout=10).text.strip())
    except Exception:
        pass
    try:
        passed = globals()['run_' + name]()
    except Exception as e:
        log(f'EXCEPTION {type(e).__name__}: {e}')
        passed = False
    log('RESULT', name, 'PASS' if passed else 'BLOCKED')
    sys.exit(0 if passed else 1)
