"""
补全「仅有 TXT 源」小说的 wenku8 元数据（一次性/低频脚本）。

流程（每一步都支持断点继续，状态文件在 out/ 下）:
  1. catalog : 爬取 wenku8 全站小说列表 articlelist.php（约 200+ 页）
               -> out/wenku_catalog.json
  2. match   : 将 merged.csv 中无 novel_link 的 TXT 源条目与目录按「书名 + 作者」匹配（纯本地）
  3. detail  : 对匹配成功的小说抓取详情页（完整简介等）
               -> out/wenku_detail.json
  输出: out/txt_meta.csv（已匹配）、out/txt_unmatched.csv（未匹配/歧义，供人工检查）

爬虫方式见 utils/fetcher.py（requests / curl_cffi / playwright / steel），auto 模式下失败自动升级。

用法:
    python fill_meta.py                       # auto: 从 requests 开始，失败升级
    python fill_meta.py --scraper playwright  # 指定爬虫
    python fill_meta.py --limit 5             # 本次最多抓取 5 个页面（测试用）
    python fill_meta.py --no-detail           # 不抓详情页，只用列表页信息
    python fill_meta.py --match-only          # 只做匹配（不联网）
"""

import argparse
import csv
import json
import os
import re
import sys

import pandas as pd
from bs4 import BeautifulSoup

from utils import DOMAIN, LEVELS, Fetcher, FetchError, LoginExpired

OUT_DIR = 'out'
MERGED_CSV = os.path.join(OUT_DIR, 'merged.csv')
CATALOG_FILE = os.path.join(OUT_DIR, 'wenku_catalog.json')
DETAIL_FILE = os.path.join(OUT_DIR, 'wenku_detail.json')
META_CSV = os.path.join(OUT_DIR, 'txt_meta.csv')
UNMATCHED_CSV = os.path.join(OUT_DIR, 'txt_unmatched.csv')


# ─── 工具 ───────────────────────────────────────────────


def load_json(path: str, default):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return default


def save_json(path: str, data):
    """原子写入，避免中断时损坏状态文件。"""
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def purify(text) -> str:
    """只保留中文、英文和数字（与 main.py 一致），并转小写。"""
    if not isinstance(text, str):
        return ''
    return re.sub(r'[^一-龥a-zA-Z0-9]', '', text).lower()


def split_alt(title: str) -> tuple[str, str]:
    """'A(B)' -> ('A', 'B')"""
    if title.endswith(')') and '(' in title:
        i = title.rfind('(')
        return title[:i], title[i + 1:-1]
    return title, ''


# ─── 解析 ───────────────────────────────────────────────


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
        aid = int(m.group(1))
        info = {'aid': aid, 'title': link.text.strip()}
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


def parse_detail(html: str) -> dict:
    soup = BeautifulSoup(html, 'html.parser')
    content = soup.find(id='content')
    if content is None:
        raise FetchError('详情页缺少 #content')
    text = content.get_text('\n', strip=True)
    result = {}
    for key, pat in [('publisher', r'文库分类[：:]\s*(.+)'), ('author', r'小说作者[：:]\s*(.+)'),
                     ('status', r'文章状态[：:]\s*(.+)'), ('update', r'最后更新[：:]\s*(\d{4}-\d{2}-\d{2})'),
                     ('length', r'全文长度[：:]\s*(\d+)字'), ('tags', r'作品Tags[：:]\s*(.+)')]:
        m = re.search(pat, text)
        if m:
            result[key] = m.group(1).strip()
    m = re.search(r'内容简介[：:]\s*\n(.+?)\n阅读\n小说目录', text, re.DOTALL)
    if m:
        result['description'] = m.group(1).strip()
    t = soup.title.text if soup.title else ''
    if t:
        result['title'] = t.split(' - ')[0].strip()
    return result


# ─── 阶段 1：目录 ───────────────────────────────────────


def stage_catalog(fetcher: Fetcher, limit: int) -> dict:
    cat = load_json(CATALOG_FILE, {'last_page': 0, 'pages': {}})
    url = f'{DOMAIN}/modules/article/articlelist.php?page='
    ok = lambda h: 'articlelist.php' in h and '/book/' in h
    fetched = 0

    if not cat['last_page']:
        items, last = parse_list_page(fetcher.get(url + '1', ok))
        cat['last_page'] = last
        cat['pages']['1'] = items
        save_json(CATALOG_FILE, cat)
        fetched += 1
        print(f'[catalog] 共 {last} 页')

    todo = [p for p in range(1, cat['last_page'] + 1) if str(p) not in cat['pages']]
    print(f'[catalog] 已完成 {len(cat["pages"])}/{cat["last_page"]} 页，待抓取 {len(todo)} 页')
    for p in todo:
        if limit and fetched >= limit:
            print('[catalog] 达到 --limit，停止')
            break
        items, _ = parse_list_page(fetcher.get(f'{url}{p}', ok))
        if not items:
            raise FetchError(f'第 {p} 页解析不到条目')
        cat['pages'][str(p)] = items
        save_json(CATALOG_FILE, cat)
        fetched += 1
        print(f'[catalog] page {p}/{cat["last_page"]} +{len(items)}')
    return cat


# ─── 阶段 2：匹配 ───────────────────────────────────────


def load_txt_only() -> pd.DataFrame:
    df = pd.read_csv(MERGED_CSV, encoding='utf-8-sig', dtype=str)
    df = df[df['novel_link'].isna() & df['download_url'].notna()].copy()
    return df.reset_index(drop=True)


def names_of(title: str) -> set[str]:
    main, alt = split_alt(title)
    return {n for n in (purify(title), purify(main), purify(alt)) if n}


def stage_match(cat: dict) -> tuple[list[dict], list[dict]]:
    books = {}
    for items in cat['pages'].values():
        for it in items:
            books[it['aid']] = it
    index = {}  # purified name -> set(aid)
    for aid, it in books.items():
        for n in names_of(it['title']):
            index.setdefault(n, set()).add(aid)

    matched, unmatched = [], []
    for row in load_txt_only().to_dict('records'):
        title = row['main'] if isinstance(row['main'], str) else ''
        if isinstance(row.get('alt'), str) and row['alt']:
            title = f'{title}({row["alt"]})'
        cands = set()
        for n in names_of(title):
            cands |= index.get(n, set())
        author = purify(row.get('author'))
        reason = ''
        pick = None
        if not cands:
            reason = 'no_candidate'
        else:
            by_author = [a for a in cands if author and purify(books[a].get('author')) == author]
            if len(by_author) == 1:
                pick, kind = by_author[0], 'title+author'
            elif len(by_author) > 1:
                reason = 'ambiguous'
            elif len(cands) == 1:
                pick, kind = next(iter(cands)), 'title_only'
            else:
                reason = 'ambiguous'
        if pick is None:
            unmatched.append({**row, 'reason': reason,
                              'candidates': ' '.join(str(a) for a in sorted(cands))})
        else:
            matched.append({**row, 'aid': pick, 'match': kind})
    print(f'[match] 匹配 {len(matched)}，未匹配 {len(unmatched)} '
          f'(title_only {sum(m["match"] == "title_only" for m in matched)})')
    return matched, unmatched


# ─── 阶段 3：详情 ───────────────────────────────────────


def stage_detail(fetcher: Fetcher, aids: list[int], limit: int) -> dict:
    detail = load_json(DETAIL_FILE, {})
    todo = [a for a in aids if str(a) not in detail]
    print(f'[detail] 已完成 {len(aids) - len(todo)}/{len(aids)}，待抓取 {len(todo)}')
    for n, aid in enumerate(todo, 1):
        if limit and n > limit:
            print('[detail] 达到 --limit，停止')
            break
        html = fetcher.get(f'{DOMAIN}/book/{aid}.htm', lambda h: 'id="content"' in h)
        detail[str(aid)] = parse_detail(html)
        save_json(DETAIL_FILE, detail)
        print(f'[detail] {n}/{len(todo)} aid={aid} {detail[str(aid)].get("title", "")}')
    return detail


# ─── 输出 ───────────────────────────────────────────────


def write_outputs(cat: dict, matched: list[dict], unmatched: list[dict], detail: dict):
    books = {it['aid']: it for items in cat['pages'].values() for it in items}
    rows = []
    for m in matched:
        b = {**books[m['aid']], **{k: v for k, v in detail.get(str(m['aid']), {}).items() if v}}
        cover = b.get('cover_url', '')
        rows.append({
            'download_url': m['download_url'],
            'aid': m['aid'],
            'novel_link': f'{DOMAIN}/book/{m["aid"]}.htm',
            'match': m['match'],
            'title': b.get('title', ''),
            'author': b.get('author', ''),
            'publisher': b.get('publisher', ''),
            'status': b.get('status', ''),
            'update': b.get('update', ''),
            'length': b.get('length', ''),
            'tags': b.get('tags', ''),
            'cover_url': cover,
            'description': b.get('description', ''),
        })
    pd.DataFrame(rows).to_csv(META_CSV, index=False, encoding='utf-8-sig', quoting=csv.QUOTE_NONNUMERIC)
    pd.DataFrame(unmatched).to_csv(UNMATCHED_CSV, index=False, encoding='utf-8-sig')
    print(f'[output] {META_CSV} ({len(rows)} 行)，{UNMATCHED_CSV} ({len(unmatched)} 行)')


def main():
    ap = argparse.ArgumentParser(description='补全仅有 TXT 源的小说的 wenku8 元数据')
    ap.add_argument('--scraper', choices=['auto', *LEVELS], default='auto')
    ap.add_argument('--limit', type=int, default=0, help='每个阶段本次最多抓取的页面数（0=不限）')
    ap.add_argument('--no-detail', action='store_true', help='跳过详情页')
    ap.add_argument('--match-only', action='store_true', help='只用已有目录做匹配，不联网')
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    fetcher = Fetcher(args.scraper)
    try:
        cat = load_json(CATALOG_FILE, {'last_page': 0, 'pages': {}})
        if not args.match_only:
            cat = stage_catalog(fetcher, args.limit)
        if not cat['pages']:
            sys.exit('目录为空，请先运行抓取')
        complete = len(cat['pages']) == cat['last_page']
        if not complete:
            print(f'[WARN] 目录未抓完 ({len(cat["pages"])}/{cat["last_page"]})，匹配结果不完整')
        matched, unmatched = stage_match(cat)
        detail = load_json(DETAIL_FILE, {})
        if not args.match_only and not args.no_detail:
            detail = stage_detail(fetcher, [m['aid'] for m in matched], args.limit)
        write_outputs(cat, matched, unmatched, detail)
    except KeyboardInterrupt:
        print('\n[INFO] 已中断，进度已保存，重新运行即可继续')
    finally:
        fetcher.close()


if __name__ == '__main__':
    main()
