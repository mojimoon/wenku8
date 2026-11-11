from bs4 import BeautifulSoup
import csv
import time
import random
from urllib.parse import urljoin
import sys
import re
import json
import os
import string
from collections import Counter

import pandas as pd

from merge import merge, read_dl
from utils import LEVELS, Fetcher, FetchError, catalog

BASE_URL = 'https://www.wenku8.net/modules/article/reviewslist.php'
params = { 'keyword': '8691', 'charset': 'utf-8', 'page': 1 }
# 抓取方式见 utils/fetcher.py: 'curl_cffi' | 'requests' | 'playwright' | 'steel' | 'auto'；'none' 表示跳过抓取与合并
# 命令行: python main.py [scraper]。以 _scraper 起步，失败时自动向后升级（curl_cffi -> playwright -> steel）
_scraper = 'curl_cffi'
DOMAIN = 'https://www.wenku8.net'
OUT_DIR = 'out'
PUBLIC_DIR = 'docs'
COOKIE_FILE = os.path.join(os.path.dirname(__file__), 'COOKIE')
POST_LIST_FILE = os.path.join(OUT_DIR, 'post_list.csv')
TXT_LIST_FILE = os.path.join(OUT_DIR, 'txt_list.csv')
DL_FILE = os.path.join(OUT_DIR, 'dl.txt')
MERGED_CSV = os.path.join(OUT_DIR, 'merged.csv')
MERGED_HTML = os.path.join(PUBLIC_DIR, 'index.html')

_fetcher = None

def scrape_page(url: str):
    global _fetcher
    if _fetcher is None:
        _fetcher = Fetcher(_scraper, fallback=True, delay=(0.3, 0.8))
    return _fetcher.get(url)

def close_fetcher():
    global _fetcher
    if _fetcher is not None:
        _fetcher.close()
        _fetcher = None

def build_url_with_params(base_url: str, params: dict):
    if not params:
        return base_url
    query_string = '&'.join(f"{key}={value}" for key, value in params.items())
    # print(f'[DEBUG] Built URL: {base_url}?{query_string}')
    return f"{base_url}?{query_string}"

# ========== Scraping ==========
last_page = 1
def fetch_post(post_link: str) -> str:
    """论坛帖子页有单独的 Cloudflare 规则（见 utils/fetcher.py 的 CFFI_UA），规则再变化导致失败时经 r.jina.ai 读取（仅这一个请求）。
    不复用 _fetcher：它失败时会升级到 playwright/steel，影响后续页面的抓取。"""
    fetcher = Fetcher(_scraper if _scraper in LEVELS else 'curl_cffi')
    try:
        return fetcher.get(post_link)
    except FetchError as e:
        print(f'[WARN] 帖子页抓取失败（{e}），改用 r.jina.ai')
        import requests
        resp = requests.get('https://r.jina.ai/' + post_link, timeout=60)
        resp.raise_for_status()
        return resp.text
    finally:
        fetcher.close()


def get_latest_url(post_link: str):
    """帖子中的下载列表链接。HTML: <a href="https://paste.gentoo.zip">https://paste.gentoo.zip</a>/EsX5Kx8V；
    jina 的 Markdown: [https://paste.gentoo.zip](https://paste.gentoo.zip/)/4btZnXKF；或 https://0x0.st/8QWZ.txt"""
    txt = fetch_post(post_link)
    text = re.sub(r'<[^>]+>', '', re.sub(r'\]\([^)]*\)', '', txt)).replace('[', '')   # 去掉标签与 Markdown 链接目标
    match = re.search(r'https://paste\.[\w.]+/\w+', text) or re.search(r'https://[^\s"]+?\.txt\b', text)
    if not match:
        raise ValueError("[ERROR] Failed to find the latest URL")
    return match.group(0)


def get_latest(url: str):
    # txt = scrape_page(url)
    import subprocess
    try:
        result = subprocess.run(['curl', '-s', url], capture_output=True, timeout=10)
        if result.returncode != 0:
            raise ValueError(f"[ERROR] curl failed with return code {result.returncode}")
        raw = result.stdout
    except Exception as e:
        raise ValueError(f"[ERROR] curl command failed: {e}")
    try:
        txt = raw.decode('utf-8')
    except UnicodeDecodeError:
        txt = raw.decode('utf-8', errors='replace')
    lines = txt.split('\n')
    
    txt = '\n'.join(lines)
    # 不能因下载列表未变就退出：同一次运行还要保存新帖子、刷新目录并合并
    with open(DL_FILE, 'w', encoding='utf-8') as f:
        f.write(txt)

def get_rid(post_link: str) -> int:
    match = re.search(r'rid=(\d+)', post_link)
    return int(match.group(1)) if match else 0

def parse_page(page_num: int, known_links: set = None, max_known_rid: int = 0):
    params['page'] = page_num
    url = build_url_with_params(BASE_URL, params)
    txt = scrape_page(url)
    # print(txt)
    soup = BeautifulSoup(txt, 'html.parser')
    table = soup.find_all('table', class_='grid')[1]
    rows = table.find_all('tr')[1:]  # skip header row

    entries = []
    for (i, tr) in enumerate(rows):
        cols = tr.find_all('td')
        if len(cols) < 2:
            continue
        a_post = cols[0].find('a')
        raw_title = a_post.text.strip()
        if not raw_title.endswith(' epub'):
            continue
        post_title = raw_title[:-5] if raw_title.endswith(' epub') else raw_title
        post_link = a_post['href'] if a_post['href'].startswith('http') else urljoin(DOMAIN, a_post['href'])

        # 列表按 rid 降序：遇到不比已知最大 rid 新的帖子即停止
        # （不能只匹配单个最新链接，该帖子被删除后会永远匹配不到，导致全量爬取）
        if max_known_rid and get_rid(post_link) <= max_known_rid:
            return entries, True  # 返回当前已收集的entries，并标记停止
        if known_links and post_link in known_links:
            continue

        a_novel = cols[1].find('a')
        novel_title = a_novel.text.strip()
        novel_link = urljoin(DOMAIN, a_novel['href'])

        post_title = '"' + post_title + '"'
        novel_title = '"' + novel_title + '"'
        entries.append([post_title, post_link, novel_title, novel_link])

        if page_num == 1 and i == 0:
            get_latest(get_latest_url(post_link))

    if page_num == 1:
        last = soup.find('a', class_='last')
        global last_page
        last_page = int(last.text) if last else 1
    return entries, False

def scrape():
    if _scraper == 'none':
        print('[INFO] Skipping scraping.')
        return

    # 读取POST_LIST_FILE中所有已知的post_link
    known_links = set()
    try:
        with open(POST_LIST_FILE, 'r', encoding='utf-8', newline='') as f:
            reader = csv.reader(f)
            next(reader, None)  # skip header
            for row in reader:
                if len(row) > 1:
                    known_links.add(row[1])
            file_exists = True
    except FileNotFoundError:
        file_exists = False
    max_known_rid = max((get_rid(link) for link in known_links), default=0)
    print(f'[INFO] known posts: {len(known_links)}, max rid: {max_known_rid}')

    all_entries = []
    stop = False

    try:
        # 先爬第一页
        print('[INFO] scrape (1)')
        entries, found = parse_page(1, known_links, max_known_rid)
        all_entries.extend(entries)
        stop = found

        # 继续爬剩余页数，直到遇到已存在帖子
        page = 2
        while not stop and page <= last_page:
            print(f'[INFO] scrape ({page}/{last_page})')
            entries, found = parse_page(page, known_links, max_known_rid)
            all_entries.extend(entries)
            stop = found
            if stop:
                break
            page += 1
            time.sleep(random.uniform(1, 3))
    finally:
        # 异常退出时也要释放 Steel 会话
        close_fetcher()  # 释放 playwright / Steel 会话
    print(f'[INFO] new posts: {len(all_entries)}')

    # 新内容在前，拼接后写入
    # with open(POST_LIST_FILE, 'w', encoding='utf-8', newline='') as f:
    #     f.write('post_title,post_link,novel_title,novel_link\n')
    #     for entry in all_entries:
    #         f.write(','.join(entry) + '\n')
    if not file_exists:
        with open(POST_LIST_FILE, 'w', encoding='utf-8', newline='') as f:
            f.write('post_title,post_link,novel_title,novel_link\n')
            for entry in all_entries:
                f.write(','.join(entry) + '\n')
    else:
        with open(POST_LIST_FILE, 'r+', encoding='utf-8', newline='') as f:
            # insert between header and first line
            lines = f.readlines()
            lines = lines[:1] + [','.join(entry) + '\n' for entry in all_entries] + lines[1:]
            f.seek(0)
            f.writelines(lines)

# ========== HTML Generation ==========
CN_NUM = {'零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}

def chinese_to_arabic(cn: str) -> int:
    if cn == '十':
        return 10
    if cn.startswith('十'):
        return 10 + CN_NUM.get(cn[1], 0)
    if cn.endswith('十'):
        return CN_NUM.get(cn[0], 0) * 10
    if '十' in cn:
        a, b = cn.split('十')
        return CN_NUM.get(a, 0) * 10 + CN_NUM.get(b, 0)
    return CN_NUM.get(cn, 0)

def replace_chinese_numerals(s: str) -> str:
    """“第十三卷”->“13”，“第 3.5 卷”->“3.5”；其他（短篇集、外传 2……）原样返回"""
    m = re.fullmatch(r'第\s*([一二三四五六七八九十零]{1,3})\s*卷', s.strip())
    if m:
        return str(chinese_to_arabic(m.group(1)))
    m = re.fullmatch(r'第\s*([0-9.]+)\s*卷', s.strip())
    return m.group(1) if m else s

GH_PROXY = 'https://gh-proxy.org/'   # 所有 GitHub 下载统一走该代理（页面内拼接）
RAW_PREFIX = 'https://raw.githubusercontent.com/'
TEMPLATE_FILE = os.path.join('source', 'template.html')
EPUB_INDEX_FILE = os.path.join(OUT_DIR, 'epub_index.json')

def _s(v):
    """NaN/None -> ''，其余转 str 并去空白"""
    return '' if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()

VARIANT_ORDER = ['orig', '1600', '1400', '1000', '800', '600', 'noimg']
STORE = 'mojimoon/wenku8-epub'   # 重制版 Release 的默认存放仓库（与之不同时才写 r）
TXT_PREFIX = 'ixinzhi/lightnovel-'
TAG_CODES = string.digits + string.ascii_letters   # 标签 -> 单个字符（wenku8 固定约 50 个标签）


def compact_versions(aid: str, e: dict) -> list:
    """重制版各版本（按分辨率从高到低：原图 > 1600 > 1400 > 1000（默认，批量生成）> 800 > 600 > 无图）。
    省略可推导的字段：d 缺省为 1000，t 缺省为 book-{aid}，r 缺省为 STORE；卷为 [卷名, KB]，
    卷序号不是 1..n 时才写 i；附件名由前端按 {aid}-[{d}-]v{序号:02}.epub 拼出。"""
    versions = []
    slots = [('1000', e)] + list(e.get('variants', {}).items())
    for name, slot in sorted(slots, key=lambda x: VARIANT_ORDER.index(x[0]) if x[0] in VARIANT_ORDER else 99):
        vols = slot.get('volumes')
        if not vols or not slot.get('tag'):
            continue
        ver = {'u': (slot.get('built_at') or '')[:10], 'v': [[v['title'], -(-v['size'] // 1024)] for v in vols]}
        if name != '1000':
            ver['d'] = name
        if slot['tag'] != f'book-{aid}':
            ver['t'] = slot['tag']
        if slot.get('repo', 'mojimoon/wenku8') != STORE:
            ver['r'] = slot.get('repo', 'mojimoon/wenku8')
        idx = [v['index'] for v in vols]
        if idx != list(range(1, len(vols) + 1)):
            ver['i'] = idx
        if len(vols) < slot.get('total', 0):
            ver['n'] = slot['total']   # 只生成了部分卷
        versions.append(ver)
    return versions


def compact_txt(item: dict, url: str) -> str:
    """TXT 源 ixinzhi/lightnovel-{年份}/master/{书名(别名)} - {作者} - {日期}.epub：
    文件名可由条目推出时只写年份段（如 2025），否则写 年份段/文件名；其他来源写完整地址。"""
    if not url.startswith(RAW_PREFIX):
        return url
    m = re.fullmatch(re.escape(TXT_PREFIX) + r'([\dto]+)/master/(.+)', url[len(RAW_PREFIX):])
    if not m:
        return url
    t = item['t'] + (f"({item['a']})" if item['a'] else '')
    if m.group(2) == f"{t} - {item['au']} - {item['xu'].replace('-', '')}.epub":
        return m.group(1)
    return m.group(1) + '/' + m.group(2)


def status_code(st: str):
    """'已完结/ 已动画化' -> 1|2：bit0 已完结，bit1 已动画化；未知 -> ''"""
    return '' if not st else ('已完结' in st) + 2 * ('动画化' in st)


def create_data():
    """合并后的条目 + 重制版 EPUB 索引 + wenku8 目录信息 -> 页面内嵌的 JSON 数据（字段说明见 source/template.html）。"""
    df = pd.read_csv(MERGED_CSV, encoding='utf-8-sig', dtype=str)
    cat = catalog.books(catalog.load_catalog())
    tags = [t for t, _ in Counter(t for b in cat.values() for t in b.get('tags', '').split()).most_common()]
    tag_code = dict(zip(tags, TAG_CODES))   # ponytail: 超过 62 个标签时多出的不编码，到时再扩充字母表
    pubs = [p for p, _ in Counter(b.get('publisher', '') for b in cat.values()).most_common() if p]
    pub_idx = {p: i for i, p in enumerate(pubs)}

    built, blocked = {}, set()
    if os.path.exists(EPUB_INDEX_FILE):
        with open(EPUB_INDEX_FILE, 'r', encoding='utf-8') as f:
            for aid, e in json.load(f).items():
                if e.get('blocked'):
                    blocked.add(aid)
                elif vs := compact_versions(aid, e):
                    built[aid] = vs

    items = []
    for row in df.to_dict('records'):
        link = _s(row['novel_link'])
        m = re.search(r'/book/(\d+)', link)
        aid = m.group(1) if m else ''
        # 显示用的更新日期 = max(du, xu)，由前端计算
        item = {'t': _s(row['main']), 'a': _s(row['alt']), 'au': _s(row['author']),
                'n': aid, 'l': _s(row['dl_label']), 'p': _s(row['dl_pwd']), 'v': _s(row['volume']),
                'r': _s(row['dl_remark']), 'du': _s(row['dl_update']), 'xu': _s(row.get('txt_update'))}
        item['x'] = compact_txt(item, _s(row['download_url']))
        if item['v']:
            vs = replace_chinese_numerals(item['v']).strip()   # “第七卷”->“7”，用于列表按钮
            if vs != item['v']:
                item['vs'] = vs
        if aid in built and not item['l']:   # 重制版仅用于没有蓝奏 EPUB 源的条目
            item['B'] = built[aid]
        elif aid in blocked and not item['l']:   # wenku8 版权下架，无法重制
            item['k'] = 1
        if b := cat.get(int(aid) if aid else -1):
            # wenku8 目录：w 最后更新，c 字数（K），s 状态码，g 标签码，f 文库序号，nc 无封面（封面地址由 aid 推出）
            # 版权下架的书目录里没有更新、字数、状态
            item.update({'w': b.get('update', ''), 'c': int(b['length'][:-1]) if b.get('length', '').endswith('K') else '',
                         's': status_code(b.get('status', '')),
                         'g': ''.join(tag_code.get(t, '') for t in b.get('tags', '').split()),
                         'f': pub_idx.get(b.get('publisher'), '')})
            if not b.get('cover_url', '').endswith(f'/{aid}s.jpg'):
                item['nc'] = 1
        items.append({k: v for k, v in item.items() if v != ''})
    lz = 'https://' + read_dl()[0].rstrip('/') + '/'
    return {'items': items, 'lz': lz, 'gh': GH_PROXY, 'st': STORE, 'tg': tags[:len(TAG_CODES)], 'pb': pubs}

def create_html():
    with open(TEMPLATE_FILE, 'r', encoding='utf-8') as f:
        tpl = f.read()
    data = json.dumps(create_data(), ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    html = tpl.replace('__DATE__', time.strftime('%Y-%m-%d', time.localtime())).replace('__DATA__', data)
    with open(MERGED_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    # 旧版「仅 EPUB」页面已合并，保留跳转以兼容旧链接
    with open(os.path.join(PUBLIC_DIR, 'epub.html'), 'w', encoding='utf-8') as f:
        f.write('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
                '<meta http-equiv="refresh" content="0;url=./index.html?f=epub"><title>轻小说文库 EPUB 下载</title></head>'
                '<body><a href="./index.html?f=epub">前往新版页面</a></body></html>')

def main():
    if not os.path.exists(OUT_DIR):
        os.mkdir(OUT_DIR)
    if not os.path.exists(PUBLIC_DIR):
        os.mkdir(PUBLIC_DIR)
    
    scrape()
    if _scraper != 'none':
        fetcher = Fetcher(_scraper, fallback=True)
        try:   # 增量刷新 wenku8 全站目录（新书/更新都在最近更新的前几页）：对应 aid，并提供更新日期、字数、标签等
            catalog.refresh(fetcher, pages=5)
        except Exception as e:
            print(f'[WARN] 目录刷新失败（沿用现有目录）: {e}')
        finally:
            fetcher.close()
    merge()
    create_html()

if __name__ == '__main__':
    if len(sys.argv) > 1:
        _scraper = sys.argv[1]
        if _scraper not in ('none', 'auto', *LEVELS):
            sys.exit(f"Unknown scraper: {_scraper} (可选: none, auto, {', '.join(LEVELS)})")
    main()
