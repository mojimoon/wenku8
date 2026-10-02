"""
合并三个来源，生成 out/merged.csv（页面与 EPUB 生成的唯一数据源）。

来源：
  out/post_list.csv  论坛帖（novel_link 里有 aid）          \\
  out/dl.txt         蓝奏云下载列表（按书名对应帖子）        |->  蓝奏 EPUB
  out/txt_list.csv   TXT 源（只有书名、作者，没有 aid）     ->   TXT
  out/wenku_catalog.json  wenku8 全站目录（书名、作者 -> aid，权威来源）

做法：以 aid 为唯一标识。把每个来源的条目先对应到 aid，再按 aid 合并成一条：
  - 同一本书的多个别名 / 多个 TXT 版本不会再出现重复条目，TXT 只保留最新版本
  - 书名、作者统一取 wenku8 目录里的写法
  - 对应不上 aid 的 TXT 条目单独保留（按书名+作者去重，取最新）
人工别名表：out/txt_alias.csv（列：title,aid），用于自动匹配无法解决的少数条目。
"""

import csv
import os
import re
import sys

import pandas as pd

from utils import catalog as wcat
from utils.names import Resolver, purify, similarity, split_alt, title_keys

OUT_DIR = 'out'
POST_LIST_FILE = os.path.join(OUT_DIR, 'post_list.csv')
TXT_LIST_FILE = os.path.join(OUT_DIR, 'txt_list.csv')
DL_FILE = os.path.join(OUT_DIR, 'dl.txt')
ALIAS_FILE = os.path.join(OUT_DIR, 'txt_alias.csv')
MERGED_CSV = os.path.join(OUT_DIR, 'merged.csv')
BOOK_URL = 'https://www.wenku8.net/book/{}.htm'
COLUMNS = ['author', 'download_url', 'volume', 'dl_label', 'dl_pwd', 'dl_update', 'dl_remark',
           'novel_link', 'update', 'main', 'alt', 'txt_update']


def _aid(link) -> int | None:
    m = re.search(r'/book/(\d+)', link) if isinstance(link, str) else None
    return int(m.group(1)) if m else None


def load_alias() -> dict:
    if not os.path.exists(ALIAS_FILE):
        return {}
    df = pd.read_csv(ALIAS_FILE, encoding='utf-8-sig', dtype=str)
    return {purify(t): int(a) for t, a in zip(df['title'], df['aid']) if isinstance(t, str) and isinstance(a, str)}


def read_dl() -> tuple[str, list[dict]]:
    """dl.txt：首行为网址前缀，第二行为表头，其后每行 `日期 网址后缀 密码 [注释] 名称`。"""
    with open(DL_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    prefix = lines[0].split('：')[-1].strip()
    rows = []
    for line in lines[2:]:
        p = line.split()
        if len(p) < 4:
            continue
        remark = p[3] if len(p) > 4 else ''
        # 注释：仅“更新台版/更新网译”去掉前两字，其余（补全旧作、更新短篇、修正错误……）完整保留
        remark = remark[2:] if remark in ('更新台版', '更新网译') else remark
        rows.append({'date': p[0], 'label': p[1], 'pwd': p[2], 'remark': remark, 'name': p[-1]})
    return prefix, rows


def merge(verbose: bool = True) -> pd.DataFrame:
    cat = wcat.books(wcat.load_catalog())
    posts = pd.read_csv(POST_LIST_FILE, encoding='utf-8-sig', dtype=str)
    posts['aid'] = posts['novel_link'].map(_aid)
    posts = posts[posts['aid'].notna()].copy()
    posts['aid'] = posts['aid'].astype(int)
    prefix, dl_rows = read_dl()

    # 论坛帖偶尔会链接到错误的 aid（如“追逐彗星的吸血鬼”链到了别的书）：
    # 帖子书名与该 aid 的目录书名几乎无关时，改按书名在目录中重新对应
    cat_resolver = Resolver({a: [b['title']] for a, b in cat.items()}, {a: b.get('author', '') for a, b in cat.items()})
    fixed = {}
    for title, aid in set(zip(posts['novel_title'], posts['aid'])):
        if aid in cat and similarity(title_keys(title), title_keys(cat[aid]['title'])) < 0.5:
            new = cat_resolver.resolve(title, strict_generic=False)
            if new is not None and new != aid:
                fixed[(title, aid)] = new
                if verbose:
                    print(f'[merge] 帖子链接修正：《{title}》 {aid}（{cat[aid]["title"][:20]}） -> {new}（{cat[new]["title"][:20]}）')
    posts['aid'] = [fixed.get((t, a), a) for t, a in zip(posts['novel_title'], posts['aid'])]
    txt = pd.read_csv(TXT_LIST_FILE, encoding='utf-8-sig', dtype=str).fillna('')
    alias = load_alias()

    # ── 每个 aid 的各种写法（帖子标题 + 目录标题）──
    titles, authors = {}, {}
    for aid, b in cat.items():
        titles[aid] = [b['title']]
        authors[aid] = b.get('author', '')
    post_titles = {}
    for aid, t in zip(posts['aid'], posts['novel_title']):
        post_titles.setdefault(aid, []).append(t)
        titles.setdefault(aid, []).append(t)

    # ── 1. 蓝奏：dl.txt 的名称 -> aid（只在有帖子的书里找）──
    post_resolver = Resolver({a: titles[a] for a in post_titles}, {a: authors.get(a, '') for a in post_titles}, alias)
    rid = {}   # aid -> 最新帖子 rid，用于 dl.txt 名称有歧义时取最新
    for aid, link in zip(posts['aid'], posts['post_link']):
        rid[aid] = max(rid.get(aid, 0), int(re.search(r'rid=(\d+)', link).group(1)) if 'rid=' in link else 0)
    lanzou, unresolved_dl = {}, []
    for r in dl_rows:
        aid = post_resolver.resolve(r['name'], strict_generic=False, global_min=0.5, prefer=rid)
        if aid is None:
            unresolved_dl.append(r['name'])
        elif aid not in lanzou or r['date'] > lanzou[aid]['date']:
            lanzou[aid] = r
    # 每本书最新帖子的标题作为“最新卷”（post_list 按时间倒序）
    volume = {}
    for aid, t in zip(posts['aid'], posts['post_title']):
        volume.setdefault(aid, t.strip())

    # ── 2. TXT -> aid（全站目录 + 帖子标题）──
    resolver = Resolver(titles, authors, alias)
    txt_by_aid, orphans = {}, {}
    n_unresolved = 0
    for r in txt.to_dict('records'):
        aid = resolver.resolve(r['title'], r.get('author'))
        if aid is None:
            n_unresolved += 1
            key = (purify(split_alt(r['title'])[0]), purify(r.get('author')))
            if key not in orphans or r['date'] > orphans[key]['date']:
                orphans[key] = r
        elif aid not in txt_by_aid or r['date'] > txt_by_aid[aid]['date']:
            txt_by_aid[aid] = r      # 同一本书只保留最新的 TXT 版本

    # ── 3. 按 aid 合并 ──
    rows = []
    for aid in sorted(set(lanzou) | set(txt_by_aid)):
        l, t = lanzou.get(aid), txt_by_aid.get(aid)
        title = cat[aid]['title'] if aid in cat else (post_titles.get(aid) or [t['title']])[0]
        main, alt = split_alt(title)
        author = authors.get(aid) or (t['author'] if t else '')
        rows.append({
            'author': author, 'download_url': t['download_url'] if t else '',
            'volume': volume.get(aid, '') if l else '',
            'dl_label': l['label'] if l else '', 'dl_pwd': l['pwd'] if l else '',
            'dl_update': l['date'] if l else '', 'dl_remark': l['remark'] if l else '',
            'novel_link': BOOK_URL.format(aid),
            'update': max(l['date'] if l else '', t['date'] if t else ''), 'main': main, 'alt': alt,
            'txt_update': t['date'] if t else '',
        })
    for r in orphans.values():
        main, alt = split_alt(r['title'])
        rows.append({'author': r.get('author') or '', 'download_url': r['download_url'], 'volume': '',
                     'dl_label': '', 'dl_pwd': '', 'dl_update': '', 'dl_remark': '', 'novel_link': '',
                     'update': r['date'], 'main': main, 'alt': alt, 'txt_update': r['date']})

    df = pd.DataFrame(rows, columns=COLUMNS).fillna('')
    df = df.sort_values(by='update', ascending=False, kind='stable')
    df.to_csv(MERGED_CSV, index=False, encoding='utf-8-sig')

    if verbose:
        print(f'[merge] 蓝奏 {len(lanzou)} 本（dl.txt 未对应 {len(unresolved_dl)}），TXT {len(txt)} 条 -> '
              f'{len(txt_by_aid)} 本（去重）+ 无法对应 aid {len(orphans)} 条，共 {len(df)} 行')
        if unresolved_dl:
            print('[merge] dl.txt 未对应：', unresolved_dl[:10])
        if orphans:
            pd.DataFrame(list(orphans.values()))[['title', 'author', 'date', 'download_url']].to_csv(
                os.path.join(OUT_DIR, 'txt_unmatched.csv'), index=False, encoding='utf-8-sig')
    return df


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    merge()
