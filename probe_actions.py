"""
GitHub Actions 可行性探针：在 runner 上测试能否访问 wenku8 / 图片站，以及限流情况。
结果输出到 stdout，并写入 $GITHUB_STEP_SUMMARY。用法见 .github/workflows/probe.yml
"""
import os
import re
import sys
import time

import requests

AID = 129
COOKIE = ''
if os.path.exists('COOKIE'):
    COOKIE = open('COOKIE', encoding='utf-8').readline().strip()
H = {'User-Agent': 'Mozilla/5.0'}
HC = {**H, 'Cookie': COOKIE} if COOKIE else H
lines = []


def log(s=''):
    print(s, flush=True)
    lines.append(s)


def head(r):
    return f'{r.status_code} {len(r.content)}B cf-mitigated={r.headers.get("cf-mitigated")}'


def req(url, headers=HC):
    try:
        return requests.get(url, headers=headers, timeout=20, allow_redirects=False)
    except Exception as e:
        log(f'  ERR {e}')
        return None


log('## Runner')
try:
    log('IP: ' + requests.get('https://ipinfo.io/json', timeout=10).text.replace('\n', ' '))
except Exception as e:
    log(f'ip lookup failed: {e}')

log('\n## requests: 基本可达性')
tests = {
    'book(无cookie)': (f'https://www.wenku8.net/book/{AID}.htm', H),
    'book(cookie)': (f'https://www.wenku8.net/book/{AID}.htm', HC),
    'list(cookie)': ('https://www.wenku8.net/modules/article/articlelist.php?page=1', HC),
    'toc(cookie)': (f'https://www.wenku8.net/novel/0/{AID}/index.htm', HC),
    'browser-UA(预期被challenge)': (f'https://www.wenku8.net/book/{AID}.htm', {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/109.0 Safari/537.36'}),
    'image': ('https://pic.777743.xyz/0/129/105408/128837.jpg', H),
}
for name, (u, h) in tests.items():
    r = req(u, h)
    log(f'- {name}: ' + (head(r) if r is not None else 'failed'))

log('\n## requests: 连续抓取 25 章（间隔 1.3s），观察 429')
r = req(f'https://www.wenku8.net/novel/0/{AID}/index.htm')
cids = re.findall(r'href="(\d+\.htm)"', r.content.decode('gbk', 'replace')) if r is not None else []
codes = []
t0 = time.time()
for c in cids[:25]:
    r = req(f'https://www.wenku8.net/novel/0/{AID}/{c}')
    codes.append(r.status_code if r is not None else 0)
    time.sleep(1.3)
log(f'状态码序列: {codes}  耗时 {time.time() - t0:.0f}s')

log('\n## playwright')
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=['--no-sandbox'])
        ctx = b.new_context(user_agent='Mozilla/5.0')
        pg = ctx.new_page()
        pg.goto(f'https://www.wenku8.net/book/{AID}.htm', wait_until='domcontentloaded', timeout=30000)
        for _ in range(20):
            if 'Just a moment' not in pg.title():
                break
            time.sleep(1)
        log(f'- title: {pg.title()[:40]!r} len={len(pg.content())}')
        b.close()
except Exception as e:
    log(f'- playwright 失败: {e}')

summary = os.environ.get('GITHUB_STEP_SUMMARY')
if summary:
    with open(summary, 'a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
