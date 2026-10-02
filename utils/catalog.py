"""
wenku8 全站小说目录（articlelist.php，约 220 页 / 4300+ 本）：爬取、断点续跑、增量刷新。
目录是 “书名 / 作者 → aid” 的权威来源，用于把论坛帖、TXT 源等不同来源的条目对应到同一本书。

    python -m utils.catalog                 # 补全抓取（断点继续）
    python -m utils.catalog --refresh 3     # 增量：重新抓取最近更新的前 3 页（新书/更新）
    python -m utils.catalog --limit 5       # 本次最多抓 5 页（测试）

状态文件：out/wenku_catalog.json  {"last_page": N, "pages": {"1": [条目...], ...}}
条目字段：aid, title, author, publisher, update, length, status, tags, description(截断), cover_url
"""

import argparse
import json
import os
import re

from bs4 import BeautifulSoup

from .fetcher import DOMAIN, Fetcher, FetchError

CATALOG_FILE = os.path.join('out', 'wenku_catalog.json')
LIST_URL = f'{DOMAIN}/modules/article/articlelist.php?page='


def load_catalog() -> dict:
    if os.path.exists(CATALOG_FILE):
        with open(CATALOG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'last_page': 0, 'pages': {}}


def save_catalog(cat: dict):
    os.makedirs(os.path.dirname(CATALOG_FILE), exist_ok=True)
    tmp = CATALOG_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cat, f, ensure_ascii=False)
    os.replace(tmp, CATALOG_FILE)


def books(cat: dict) -> dict[int, dict]:
    """{aid: 条目}（后出现的覆盖先出现的）"""
    return {it['aid']: it for p in cat['pages'].values() for it in p}


def parse_list_page(html: str) -> tuple[list[dict], int]:
    """返回 (小说条目列表, 总页数)。"""
    soup = BeautifulSoup(html, 'html.parser')
    last = 1
    a = soup.select_one('a.last')
    if a and a.text.strip().isdigit():
        last = int(a.text.strip())

    items = []
    for div in soup.select('td > div[style*="float:left"]'):
        b = div.find('b')
        link = b.find('a') if b else None
        if not link:
            continue
        m = re.search(r'/book/(\d+)\.htm', link.get('href', ''))
        if not m:
            continue
        info = {'aid': int(m.group(1)), 'title': (link.get('title') or link.text).strip()}  # 列表文字被截断，完整书名在 title 属性
        for p in div.find_all('p'):
            t = p.get_text(' ', strip=True)
            if t.startswith('作者:'):
                mm = re.match(r'作者:(.*?)/分类:(.*)$', t)
                if mm:
                    info['author'], info['publisher'] = mm.group(1).strip(), mm.group(2).strip()
            elif t.startswith('更新:'):
                mm = re.match(r'更新:(.*?)/字数:(.*?)/(.*)$', t)
                if mm:
                    info['update'], info['length'], info['status'] = (g.strip() for g in mm.groups())
            elif t.startswith('Tags:'):
                info['tags'] = t[5:].strip()
            elif t.startswith('简介:'):
                info['description'] = p.get_text('\n', strip=True)[3:].strip()
        img = div.find('img')
        if img and img.get('src'):
            info['cover_url'] = img['src']
        items.append(info)
    return items, last


def _fetch_page(fetcher: Fetcher, page: int) -> tuple[list[dict], int]:
    items, last = parse_list_page(
        fetcher.get(LIST_URL + str(page), lambda h: 'articlelist.php' in h and '/book/' in h))
    if not items:
        raise FetchError(f'目录第 {page} 页解析不到条目')
    return items, last


def crawl(fetcher: Fetcher, limit: int = 0) -> dict:
    """补全抓取：只抓尚未抓取的页，每页保存一次（可中断续跑）。"""
    cat = load_catalog()
    fetched = 0
    if not cat['last_page']:
        items, last = _fetch_page(fetcher, 1)
        cat['last_page'], cat['pages']['1'] = last, items
        save_catalog(cat)
        fetched += 1
    todo = [p for p in range(1, cat['last_page'] + 1) if str(p) not in cat['pages']]
    print(f'[catalog] 已完成 {len(cat["pages"])}/{cat["last_page"]} 页，待抓取 {len(todo)} 页')
    for p in todo:
        if limit and fetched >= limit:
            print('[catalog] 达到 --limit，停止')
            break
        cat['pages'][str(p)] = _fetch_page(fetcher, p)[0]
        save_catalog(cat)
        fetched += 1
        print(f'[catalog] page {p}/{cat["last_page"]}')
    return cat


def refresh(fetcher: Fetcher, pages: int = 3) -> dict:
    """增量刷新：目录按最近更新排序，新书和更新的书都在前几页。
    新抓到的条目按 aid 覆盖/追加，旧页保持不变（页码会随更新漂移，只用于存储，不影响按 aid 查找）。"""
    cat = load_catalog()
    old = books(cat)
    known = dict(old)
    new = 0
    for p in range(1, pages + 1):
        items, last = _fetch_page(fetcher, p)
        cat['last_page'] = max(cat['last_page'], last)
        for it in items:
            if it['aid'] not in known:
                new += 1
            known[it['aid']] = it
        cat['pages'][str(p)] = items
    # 页码会随更新漂移：被挤出前几页、又不在后面旧页里的条目放入 "extra"，保证不丢
    fresh = {it['aid'] for p in range(1, pages + 1) for it in cat['pages'][str(p)]}
    for k, page in cat['pages'].items():
        if k.isdigit() and int(k) > pages:
            cat['pages'][k] = [it for it in page if it['aid'] not in fresh]
    present = {it['aid'] for page in cat['pages'].values() for it in page}
    extra = {it['aid']: it for it in cat['pages'].get('extra', [])}
    extra.update({aid: it for aid, it in old.items() if aid not in present})
    cat['pages']['extra'] = [it for aid, it in extra.items() if aid not in present]
    save_catalog(cat)
    print(f'[catalog] 刷新前 {pages} 页，新增 {new} 本，共 {len(books(cat))} 本')
    return cat


def main():
    ap = argparse.ArgumentParser(description='wenku8 全站目录')
    ap.add_argument('--scraper', default='auto')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--refresh', type=int, default=0, help='增量刷新最近更新的前 N 页')
    args = ap.parse_args()
    fetcher = Fetcher(args.scraper, fallback=True)
    try:
        refresh(fetcher, args.refresh) if args.refresh else crawl(fetcher, args.limit)
    except KeyboardInterrupt:
        print('\n[catalog] 已中断，进度已保存')
    finally:
        fetcher.close()


if __name__ == '__main__':
    main()
