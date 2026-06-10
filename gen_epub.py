"""
EPUB 自动生成管道
从 wenku8.net 最近更新列表爬取小说，生成 EPUB3 文件。

用法:
    python gen_epub.py              # 默认: 检查更新并生成
    python gen_epub.py --force      # 强制重新生成所有
    python gen_epub.py --limit 3    # 限制处理数量
"""

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import os
import re
import json
import time
import random
import hashlib
import datetime
import argparse
from pathlib import Path

from epub_maker import NovelMeta, Chapter, make_epub_from_raw

# ─── 配置 ──────────────────────────────────────────────

DOMAIN = 'https://www.wenku8.net'
EPUB_OUT_DIR = os.path.join('out', 'epub')
STATE_FILE = os.path.join('out', 'epub_state.json')
COOKIE_FILE = os.path.join(os.path.dirname(__file__), 'COOKIE')
CHAPTER_DELAY = (0.5, 1.5)  # 章节间延迟范围 (秒)

SESSION = None

# ─── Session 管理 ──────────────────────────────────────


def _parse_cookie_line(line: str) -> dict:
    """解析 COOKIE 文件中的 cookie 行"""
    cookie_dict = {}
    for part in line.strip().split(';'):
        part = part.strip()
        if not part or '=' not in part:
            continue
        k, v = part.split('=', 1)
        cookie_dict[k.strip()] = v.strip()
    return cookie_dict


def _init_session() -> requests.Session:
    """初始化带 Cookie 的 requests Session"""
    ses = requests.Session()
    ses.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/109.0.0.0 Safari/537.36',
        'Referer': 'https://www.wenku8.net/',
    })

    cookie_line = None
    # 优先从环境变量读取（CI/CD 场景）
    env_cookie = os.environ.get('WENKU8_COOKIE', '')
    if env_cookie:
        cookie_line = env_cookie
    elif os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
            cookie_line = f.readline()

    if cookie_line:
        jar = requests.utils.cookiejar_from_dict(_parse_cookie_line(cookie_line))
        ses.cookies.update(jar)

    return ses


def _fetch(url: str, timeout: int = 20) -> requests.Response:
    """统一的页面请求"""
    global SESSION
    if SESSION is None:
        SESSION = _init_session()
    resp = SESSION.get(url, timeout=timeout, allow_redirects=True)
    resp.encoding = 'utf-8'
    if '/login.php' in resp.url:
        raise RuntimeError(f'被重定向到登录页，Cookie 可能已过期: {resp.url}')
    resp.raise_for_status()
    return resp


# ─── 状态管理 ──────────────────────────────────────────


def _load_state() -> dict:
    """加载处理状态"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {'novels': {}}


def _save_state(state: dict):
    """保存处理状态"""
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ─── 爬虫：Toplist ────────────────────────────────────


def crawl_toplist() -> list[dict]:
    """
    爬取最近更新列表。

    Returns:
        [{'aid': int, 'title': str, 'book_url': str}, ...]
    """
    url = f'{DOMAIN}/modules/article/toplist.php?sort=lastupdate'
    resp = _fetch(url)
    soup = BeautifulSoup(resp.text, 'html.parser')

    # 提取所有 /book/XXXX.htm 链接，按 book ID 分组
    book_groups = {}  # {aid: [(text, href), ...]}

    for a in soup.find_all('a', href=True):
        href = a['href']
        m = re.search(r'/book/(\d+)\.htm', href)
        if not m:
            continue
        aid = int(m.group(1))
        text = a.text.strip()
        if aid not in book_groups:
            book_groups[aid] = []
        book_groups[aid].append((text, href))

    # 从分组中提取书名（取非空且非操作文本的链接）
    novels = []
    seen = set()
    for aid, entries in book_groups.items():
        if aid in seen:
            continue
        seen.add(aid)

        # 找书名链接（文本不为空且不是操作文本）
        operation_texts = {'我要阅读', '加入书架', '推荐本书'}
        title = ''
        for text, href in entries:
            if text and text not in operation_texts:
                title = text
                break

        if not title:
            continue

        novels.append({
            'aid': aid,
            'title': title,
            'book_url': f'{DOMAIN}/book/{aid}.htm',
        })

    return novels


# ─── 爬虫：详情页 ─────────────────────────────────────


def crawl_detail(book_url: str) -> dict:
    """
    爬取小说详情页。
    注意：此页面的元数据不是结构化 HTML，需要从文本中提取。

    Returns:
        {
            'title': str, 'author': str, 'publisher': str,
            'status': str, 'last_update': str, 'length': str,
            'tags': [str], 'description': str,
            'toc_url': str, 'cover_url': str,
        }
    """
    resp = _fetch(book_url)
    soup = BeautifulSoup(resp.text, 'html.parser')

    # 从 <title> 提取书名、作者、文库
    title_tag = soup.find('title')
    page_title = title_tag.text.strip() if title_tag else ''
    # 格式: "书名 - 作者 - 文库 - 轻小说文库" 或 "书名(别名) - 作者 - 文库 - ..."
    title_parts = [p.strip() for p in page_title.split(' - ')]

    result = {
        'title': title_parts[0] if len(title_parts) > 0 else '',
        'author': title_parts[1] if len(title_parts) > 1 else '',
        'publisher': title_parts[2] if len(title_parts) > 2 else '',
        'status': '',
        'last_update': '',
        'length': '',
        'tags': [],
        'description': '',
        'toc_url': '',
        'cover_url': '',
    }

    # 从页面文本中提取结构化元数据
    body_text = soup.get_text('\n', strip=True)

    # 文库分类
    m = re.search(r'文库分类[：:]\s*(.+)', body_text)
    if m:
        result['publisher'] = m.group(1).strip()

    # 小说作者
    m = re.search(r'小说作者[：:]\s*(.+)', body_text)
    if m:
        result['author'] = m.group(1).strip()

    # 文章状态
    m = re.search(r'文章状态[：:]\s*(.+)', body_text)
    if m:
        result['status'] = m.group(1).strip()

    # 最后更新
    m = re.search(r'最后更新[：:]\s*(\d{4}-\d{2}-\d{2})', body_text)
    if m:
        result['last_update'] = m.group(1).strip()

    # 全文长度
    m = re.search(r'全文长度[：:]\s*(\d+)字', body_text)
    if m:
        result['length'] = f"{m.group(1)}字"

    # Tags
    m = re.search(r'作品Tags[：:]\s*(.+)', body_text)
    if m:
        result['tags'] = [t.strip() for t in m.group(1).split() if t.strip()]

    # 内容简介
    m = re.search(r'内容简介[：:]\s*\n?(.+?)(?:阅读\s*小说目录|\Z)', body_text, re.DOTALL)
    if m:
        result['description'] = m.group(1).strip()[:500]

    # 目录链接
    for a in soup.find_all('a', href=True):
        href = a['href']
        text = a.text.strip()
        if text == '小说目录' and 'reader.php' in href:
            result['toc_url'] = urljoin(DOMAIN, href)
            break

    # 封面图片
    for img in soup.find_all('img'):
        src = img.get('src', '')
        if src and ('/files/' in src or '/image/' in src or 'cover' in src.lower()):
            result['cover_url'] = urljoin(DOMAIN, src) if not src.startswith('http') else src
            break

    return result


# ─── 爬虫：目录页 ─────────────────────────────────────


def crawl_toc(toc_url: str) -> list[dict]:
    """
    爬取小说目录页，提取章节列表。

    Returns:
        [{'title': str, 'url': str, 'cid': int}, ...]
    """
    resp = _fetch(toc_url)
    soup = BeautifulSoup(resp.text, 'html.parser')

    chapters = []
    seen_cids = set()

    for a in soup.find_all('a', href=True):
        href = a['href']
        m = re.search(r'cid=(\d+)', href)
        if not m:
            continue
        cid = int(m.group(1))
        if cid in seen_cids:
            continue
        seen_cids.add(cid)

        title = a.text.strip()
        full_url = urljoin(DOMAIN, href) if not href.startswith('http') else href

        chapters.append({
            'title': title,
            'url': full_url,
            'cid': cid,
        })

    return chapters


# ─── 爬虫：正文页 ─────────────────────────────────────


def crawl_chapter(chapter_url: str) -> str:
    """
    爬取单个章节的正文内容。

    Returns:
        纯文本内容
    """
    resp = _fetch(chapter_url)
    soup = BeautifulSoup(resp.text, 'html.parser')

    content_div = soup.find('div', id='content')
    if not content_div:
        return ''

    text = content_div.get_text('\n', strip=True)
    return text


# ─── 封面下载 ─────────────────────────────────────────


def download_cover(cover_url: str) -> bytes | None:
    """下载封面图片"""
    if not cover_url:
        return None
    try:
        resp = _fetch(cover_url, timeout=15)
        if resp.status_code == 200 and len(resp.content) > 1000:
            return resp.content
    except Exception:
        pass
    return None


# ─── 文件名安全化 ─────────────────────────────────────


def _safe_filename(name: str) -> str:
    """生成安全的文件名"""
    safe = re.sub(r'[\\/*?:"<>|]', '', name)
    safe = safe.replace('/', '_').replace(' ', '')
    return safe[:120]


# ─── 主管道 ───────────────────────────────────────────


def run_pipeline(force: bool = False, limit: int = 0):
    """
    运行完整的 EPUB 生成管道。

    Args:
        force: 是否强制重新生成所有小说
        limit: 最大处理数量（0 = 无限制）
    """
    print(f'[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] 开始 EPUB 生成管道')

    os.makedirs(EPUB_OUT_DIR, exist_ok=True)

    state = _load_state()
    novels_info = state.get('novels', {})

    # Step 1: 爬取最近更新列表
    print('[1/5] 爬取最近更新列表...')
    try:
        toplist = crawl_toplist()
    except RuntimeError as e:
        print(f'  [ERROR] {e}')
        return
    print(f'  获取到 {len(toplist)} 本小说')

    # Step 2: 筛选需要处理的小说
    targets = []
    for novel in toplist:
        aid = str(novel['aid'])
        # 如果 force 模式，或该小说从未处理过
        if force or aid not in novels_info:
            targets.append(novel)
        else:
            # 跳过最近 3 天内已处理过的
            prev_info = novels_info[aid]
            prev_date = prev_info.get('last_update', '')
            if prev_date < (datetime.date.today() - datetime.timedelta(days=3)).isoformat():
                targets.append(novel)

    if limit > 0:
        targets = targets[:limit]

    if not targets:
        print('  没有需要处理的小说，退出。')
        return

    print(f'  需要处理 {len(targets)} 本小说')

    # Step 3: 逐本处理
    generated = []
    for idx, novel in enumerate(targets):
        aid = str(novel['aid'])
        print(f'\n[3.{idx+1}/{len(targets)}] 处理: {novel["title"][:50]} (aid={aid})')

        try:
            # 3a. 爬取详情页
            print(f'    爬取详情页...')
            detail = crawl_detail(novel['book_url'])
            time.sleep(random.uniform(0.3, 1.0))

            # 检查是否有更新（非强制模式下）
            if not force and aid in novels_info:
                prev = novels_info[aid]
                if prev.get('last_update', '') == detail.get('last_update', '') and \
                   prev.get('epub_version', 0) > 0:
                    print(f'    无更新，跳过。')
                    continue

            print(f'    标题: {detail["title"][:40]}')
            print(f'    作者: {detail["author"]}')
            print(f'    更新: {detail["last_update"]}')
            print(f'    状态: {detail["status"]}')
            print(f'    标签: {", ".join(detail["tags"][:5])}')

            if not detail['toc_url']:
                print(f'    [WARN] 未找到目录页链接，跳过。')
                continue

            # 3b. 爬取目录页
            print(f'    爬取目录页...')
            chapters_meta = crawl_toc(detail['toc_url'])
            time.sleep(random.uniform(0.3, 1.0))

            # 过滤掉插图、后记等特殊章节（保留它们但标记）
            print(f'    共 {len(chapters_meta)} 个章节')

            if not chapters_meta:
                print(f'    [WARN] 未找到章节，跳过。')
                continue

            # 3c. 爬取各章节正文
            print(f'    爬取章节正文...')
            chapters_data = []
            for ci, ch_meta in enumerate(chapters_meta):
                if ci > 0:
                    time.sleep(random.uniform(*CHAPTER_DELAY))
                try:
                    content = crawl_chapter(ch_meta['url'])
                    if content:
                        chapters_data.append((ch_meta['title'], content))
                except Exception as e:
                    print(f'      [WARN] 章节 "{ch_meta["title"]}" 爬取失败: {e}')

                if (ci + 1) % 20 == 0:
                    print(f'      已爬取 {ci + 1}/{len(chapters_meta)} 个章节...')

            print(f'    成功爬取 {len(chapters_data)}/{len(chapters_meta)} 个章节')

            if not chapters_data:
                print(f'    [WARN] 无章节内容，跳过。')
                continue

            # 3d. 下载封面
            print(f'    下载封面...')
            cover_data = download_cover(detail['cover_url'])

            # 3e. 生成 EPUB
            print(f'    生成 EPUB...')
            safe_title = _safe_filename(detail['title'])
            epub_path = os.path.join(EPUB_OUT_DIR, f'{safe_title}.epub')

            # 检查是否已存在旧版，版本号递增
            version = 1
            if aid in novels_info:
                version = novels_info[aid].get('epub_version', 0) + 1

            make_epub_from_raw(
                title=detail['title'],
                author=detail['author'],
                chapters_raw=chapters_data,
                description=detail.get('description', ''),
                source_url=novel['book_url'],
                publisher=detail.get('publisher', ''),
                subjects=detail.get('tags', []),
                cover_image_data=cover_data,
                output_path=epub_path,
                last_update=detail.get('last_update', ''),
            )

            file_size = os.path.getsize(epub_path)
            print(f'    完成: {epub_path} ({file_size / 1024:.1f} KB)')

            # 3f. 更新状态
            novels_info[aid] = {
                'aid': novel['aid'],
                'title': detail['title'],
                'author': detail['author'],
                'last_update': detail.get('last_update', ''),
                'updated_at': datetime.datetime.now().isoformat(),
                'epub_version': version,
                'epub_file': epub_path,
                'chapter_count': len(chapters_data),
            }

            generated.append({
                'aid': aid,
                'title': detail['title'],
                'author': detail['author'],
                'epub_path': epub_path,
                'epub_version': version,
            })

        except Exception as e:
            print(f'    [ERROR] 处理失败: {e}')
            import traceback
            traceback.print_exc()
            continue

    # Step 4: 保存状态
    state['novels'] = novels_info
    state['last_run'] = datetime.datetime.now().isoformat()
    _save_state(state)

    # Step 5: 输出摘要
    print(f'\n[5/5] 管道完成!')
    print(f'  本次生成: {len(generated)} 本 EPUB')
    for g in generated:
        print(f'    - {g["title"]} (v{g["epub_version"]}) -> {g["epub_path"]}')

    # 输出 JSON 格式的摘要（供 CI/CD 使用）
    summary = {
        'timestamp': datetime.datetime.now().isoformat(),
        'generated': generated,
        'total_count': len(generated),
    }
    summary_file = os.path.join('out', 'epub_summary.json')
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return summary


# ─── CLI ───────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description='wenku8 EPUB 自动生成管道')
    parser.add_argument('--force', action='store_true', help='强制重新生成所有')
    parser.add_argument('--limit', type=int, default=0, help='限制处理数量')
    parser.add_argument('--max-chapters', type=int, default=0,
                        help='每个小说的最大章节数（0=无限制）')
    args = parser.parse_args()

    run_pipeline(force=args.force, limit=args.limit)


if __name__ == '__main__':
    main()
