"""
从 wenku8.net 抓取小说并生成带封面、插图、分卷目录的 EPUB3。

用法:
    python gen_epub.py --aid 129 2700        # 指定小说 ID
    python gen_epub.py --toplist --limit 3   # 最近更新列表（按状态文件跳过无更新的）
    python gen_epub.py --aid 129 --scraper playwright
    python gen_epub.py --aid 129 --max-chapters 3   # 测试用

爬虫方式 requests -> playwright -> steel，auto 模式下失败自动升级（见 fill_meta.Fetcher）。
章节正文与图片缓存在 out/cache/{aid}/，中断后重跑即可断点继续。
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

from epub_maker import Chapter, NovelMeta, Volume, create_epub, sniff_ext
from fill_meta import DOMAIN, Fetcher, FetchError, LoginExpired, parse_detail

EPUB_OUT_DIR = os.path.join('out', 'epub')
CACHE_DIR = os.path.join('out', 'cache')
STATE_FILE = os.path.join('out', 'epub_state.json')
SUMMARY_FILE = os.path.join('out', 'epub_summary.json')
IMAGE_WORKERS = 4


# ─── 小说页面解析 ───────────────────────────────────────


def book_url(aid: int) -> str:
    return f'{DOMAIN}/book/{aid}.htm'


def toc_url(aid: int) -> str:
    return f'{DOMAIN}/novel/{aid // 1000}/{aid}/index.htm'


def cover_url(aid: int) -> str:
    # 站内仅有小图 *s.jpg；有插图时优先用插图首图作封面（见 build_novel）
    return f'http://img.wenku8.com/image/{aid // 1000}/{aid}/{aid}s.jpg'


def parse_toc(html: str, base: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """返回 [(卷名, [(章节名, 章节URL), ...]), ...]。'插图' 章节移到该卷最前。"""
    soup = BeautifulSoup(html, 'html.parser')
    volumes = []
    for td in soup.select('td.vcss, td.ccss'):
        if 'vcss' in td.get('class', []):
            volumes.append((td.get_text(strip=True), []))
        else:
            a = td.find('a')
            if a is None or not a.get('href') or not volumes:
                continue
            item = (a.get_text(strip=True), urljoin(base, a['href']))
            if item[0] == '插图':
                volumes[-1][1].insert(0, item)
            else:
                volumes[-1][1].append(item)
    return [v for v in volumes if v[1]]


def parse_chapter(content_html: str) -> list[tuple[str, str]]:
    """
    #content 的 HTML -> 内容块 [('p', 文本) | ('img', 原始URL)]。
    正文是被 <br> 分隔的文本节点；插图是 div.divimage > a > img。
    """
    soup = BeautifulSoup(content_html, 'html.parser')
    root = soup.find(id='content') or soup
    blocks = []

    def add_text(text: str):
        text = text.replace('\xa0', ' ').strip(' \t\r\n　')
        if not text or text.startswith('本文来自') or 'wenku8' in text.lower():
            return
        blocks.append(('p', text))

    def walk(node):
        for child in node.children:
            if isinstance(child, NavigableString):
                add_text(str(child))
            elif isinstance(child, Tag):
                if child.name == 'br' or child.get('id') == 'contentdp':
                    continue
                if child.name == 'img' and child.get('src'):
                    blocks.append(('img', child['src']))
                elif child.find('img'):
                    walk(child)
                else:
                    add_text(child.get_text())

    walk(root)
    return blocks


# ─── 抓取（带缓存） ─────────────────────────────────────


def cache_path(aid: int, *parts: str) -> str:
    return os.path.join(CACHE_DIR, str(aid), *parts)


def fetch_chapter(fetcher: Fetcher, aid: int, url: str) -> str:
    """返回章节 #content 的 HTML（命中缓存则不联网）。"""
    cid = re.search(r'/(\d+)\.htm', url).group(1)
    path = cache_path(aid, f'{cid}.html')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    html = fetcher.get(url, lambda h: 'id="content"' in h, encoding='gbk')
    content = BeautifulSoup(html, 'html.parser').find(id='content')
    if content is None:
        raise FetchError('章节缺少 #content')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(str(content))
    return str(content)


_img_session = requests.Session()
_img_session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': DOMAIN + '/'})


def fetch_image(aid: int, url: str) -> bytes | None:
    """下载图片（磁盘缓存）；失败返回 None。图片站无需 Cloudflare 校验，直接用 requests。"""
    path = cache_path(aid, 'img', hashlib.md5(url.encode()).hexdigest())
    if os.path.exists(path):
        with open(path, 'rb') as f:
            return f.read()
    for attempt in range(3):
        try:
            r = _img_session.get(url, timeout=30)
            if r.status_code == 200 and len(r.content) > 100:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'wb') as f:
                    f.write(r.content)
                return r.content
        except requests.RequestException:
            pass
        time.sleep(1 + attempt * 2)
    return None


def safe_filename(name: str) -> str:
    safe = re.sub(r'[\\/*?:"<>|\s]', '', name)
    return safe[:100]


# ─── 单本小说 ───────────────────────────────────────────


def build_novel(fetcher: Fetcher, aid: int, max_chapters: int = 0, skip_update: str = '') -> dict | None:
    detail = parse_detail(fetcher.get(book_url(aid), lambda h: 'id="content"' in h))
    title = detail.get('title') or str(aid)
    if skip_update and detail.get('update') == skip_update:
        print('    无更新，跳过')
        return None
    print(f'    {title} / {detail.get("author", "")} / {detail.get("update", "")} / {detail.get("status", "")}')

    toc = parse_toc(fetcher.get(toc_url(aid), lambda h: 'vcss' in h, encoding='gbk'), toc_url(aid))
    total = sum(len(v[1]) for v in toc)
    if not toc:
        raise FetchError('目录为空')
    print(f'    {len(toc)} 卷 {total} 章')

    # 1. 章节正文
    chapter_blocks = {}  # url -> blocks
    n = 0
    for _, chapters in toc:
        for ctitle, url in chapters:
            if max_chapters and n >= max_chapters:
                break
            n += 1
            chapter_blocks[url] = parse_chapter(fetch_chapter(fetcher, aid, url))
            if n % 20 == 0 or n == total:
                print(f'      章节 {n}/{total}')

    # 2. 图片：按出现顺序编号，并发下载
    urls = []
    for blocks in chapter_blocks.values():
        for kind, v in blocks:
            if kind == 'img' and v not in urls:
                urls.append(v)
    print(f'    下载 {len(urls)} 张插图...')
    with ThreadPoolExecutor(IMAGE_WORKERS) as ex:
        results = list(ex.map(lambda u: fetch_image(aid, u), urls))
    images, name_of, failed = {}, {}, 0
    for u, data in zip(urls, results):
        if data is None:
            failed += 1
            continue
        name = f'img{len(images) + 1:04d}.{sniff_ext(data)}'
        images[name] = data
        name_of[u] = name
    if failed:
        print(f'    [WARN] {failed} 张图片下载失败，已跳过')

    # 3. 组装
    volumes = []
    for vtitle, chapters in toc:
        vol = Volume(vtitle)
        for ctitle, url in chapters:
            if url not in chapter_blocks:
                continue
            blocks = [('img', name_of[v]) if k == 'img' else (k, v)
                      for k, v in chapter_blocks[url] if k != 'img' or v in name_of]
            if blocks:  # 跳过空章节（如图片全部失败的插图）
                vol.chapters.append(Chapter(ctitle, blocks))
        if vol.chapters:
            volumes.append(vol)

    # 4. 封面：优先第一张插图（站内封面只有小图），否则用小图
    cover = None
    for vol in volumes:
        for ch in vol.chapters:
            if ch.title == '插图' and any(k == 'img' for k, _ in ch.blocks):
                first = next(v for k, v in ch.blocks if k == 'img')
                cover = images[first]
                break
        if cover:
            break
    if cover is None:
        cover = fetch_image(aid, cover_url(aid))

    meta = NovelMeta(
        title=title,
        author=detail.get('author', ''),
        source_url=book_url(aid),
        description=detail.get('description', ''),
        publisher=detail.get('publisher', ''),
        subjects=detail.get('tags', '').split(),
        status=detail.get('status', ''),
        identifier=f'urn:wenku8:{aid}',
        modified=detail.get('update', ''),
    )
    os.makedirs(EPUB_OUT_DIR, exist_ok=True)
    out = os.path.join(EPUB_OUT_DIR, f'{safe_filename(title)}.epub')
    create_epub(meta, volumes, images, cover, out)
    print(f'    完成: {out} ({os.path.getsize(out) / 1024:.0f} KB, {len(images)} 图)')
    return {'aid': aid, 'title': title, 'author': meta.author, 'last_update': meta.modified,
            'epub_file': out, 'chapter_count': sum(len(v.chapters) for v in volumes)}


# ─── 最近更新列表 ───────────────────────────────────────


def crawl_toplist(fetcher: Fetcher) -> list[dict]:
    html = fetcher.get(f'{DOMAIN}/modules/article/toplist.php?sort=lastupdate',
                       lambda h: '/book/' in h, encoding='gbk')
    soup = BeautifulSoup(html, 'html.parser')
    seen, novels = set(), []
    for a in soup.find_all('a', href=re.compile(r'/book/\d+\.htm')):
        aid = int(re.search(r'/book/(\d+)\.htm', a['href']).group(1))
        if aid not in seen:
            seen.add(aid)
            novels.append({'aid': aid})
    return novels


# ─── CLI ────────────────────────────────────────────────


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'novels': {}}


def save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    state['last_run'] = datetime.datetime.now().isoformat()
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser(description='wenku8 EPUB 生成')
    ap.add_argument('--aid', type=int, nargs='*', default=[], help='小说 ID')
    ap.add_argument('--toplist', action='store_true', help='处理最近更新列表')
    ap.add_argument('--scraper', choices=['auto', 'requests', 'playwright', 'steel'], default='auto')
    ap.add_argument('--force', action='store_true', help='忽略状态文件，强制重新生成')
    ap.add_argument('--limit', type=int, default=0, help='最多处理的小说数（0=不限）')
    ap.add_argument('--max-chapters', type=int, default=0, help='每本最多抓取章节数（测试用）')
    args = ap.parse_args()
    if not args.aid and not args.toplist:
        ap.error('请指定 --aid 或 --toplist')

    fetcher = Fetcher(args.scraper)
    state = load_state()
    generated = []
    try:
        aids = list(args.aid)
        if args.toplist:
            aids += [n['aid'] for n in crawl_toplist(fetcher) if n['aid'] not in aids]
            print(f'[INFO] 最近更新列表 {len(aids)} 本')
        if args.limit:
            aids = aids[:args.limit]
        for i, aid in enumerate(aids, 1):
            print(f'\n[{i}/{len(aids)}] aid={aid}')
            try:
                prev = state['novels'].get(str(aid), {})
                skip = '' if args.force or not args.toplist else prev.get('last_update', '')
                info = build_novel(fetcher, aid, args.max_chapters, skip)
                if info is None:
                    continue
            except LoginExpired:
                raise
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f'    [ERROR] {e}')
                continue
            state['novels'][str(aid)] = {**info, 'updated_at': datetime.datetime.now().isoformat()}
            generated.append(info)
            save_state(state)
    except KeyboardInterrupt:
        print('\n[INFO] 已中断，缓存已保留，重新运行即可继续')
    finally:
        fetcher.close()
        os.makedirs('out', exist_ok=True)
        with open(SUMMARY_FILE, 'w', encoding='utf-8') as f:
            json.dump({'timestamp': datetime.datetime.now().isoformat(), 'generated': generated,
                       'total_count': len(generated)}, f, ensure_ascii=False, indent=2)
    print(f'\n[DONE] 生成 {len(generated)} 本')


if __name__ == '__main__':
    main()
