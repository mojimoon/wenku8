"""
EPUB 自动生成管道
从 wenku8.net 最近更新列表爬取小说，生成 EPUB3 文件。

用法:
    python gen_epub.py              # 默认: steel 模式检查更新并生成
    python gen_epub.py --scraper requests  # 使用 requests 模式
    python gen_epub.py --force      # 强制重新生成所有
    python gen_epub.py --limit 3    # 限制处理数量
"""

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import os
import re
import json
import time
import random
import datetime
import argparse
from pathlib import Path

from epub_maker import NovelMeta, Chapter, make_epub_from_raw

# ─── 全局配置 ──────────────────────────────────────────

DOMAIN = 'https://www.wenku8.net'
EPUB_OUT_DIR = os.path.join('out', 'epub')
STATE_FILE = os.path.join('out', 'epub_state.json')
COOKIE_FILE = os.path.join(os.path.dirname(__file__), 'COOKIE')
CHAPTER_DELAY = (0.5, 1.5)  # 章节间延迟范围 (秒)

# 爬虫模式: 'steel' | 'playwright' | 'requests'
_scraper = 'steel'

user_agents = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15',
]

HEADERS = {
    'User-Agent': random.choice(user_agents),
    'Referer': 'https://www.wenku8.net/',
}

# ─── Cookie 解析 ───────────────────────────────────────


def _parse_cookie_line(line: str) -> dict:
    """解析 COOKIE 文件 / 环境变量中的 cookie 行"""
    cookie_dict = {}
    for part in line.strip().split(';'):
        part = part.strip()
        if not part or '=' not in part:
            continue
        k, v = part.split('=', 1)
        cookie_dict[k.strip()] = v.strip()
    return cookie_dict


def _get_cookie_line() -> str | None:
    """获取 cookie 字符串，优先环境变量 → 文件"""
    env_cookie = os.environ.get('WENKU8_COOKIE', '')
    if env_cookie:
        return env_cookie
    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
            return f.readline().strip()
    return None


# ─── Scraper 基础设施（复刻 main.py 架构）──────────────

# requests 模式的全局 session（带重试）
retry_strategy = Retry(total=5, status_forcelist=[500, 502, 503, 504], backoff_factor=2)
req_session = requests.Session()
req_session.mount('http://', HTTPAdapter(max_retries=retry_strategy))
req_session.mount('https://', HTTPAdapter(max_retries=retry_strategy))
req_session.headers.update(HEADERS)
_cookie_line = _get_cookie_line()
if _cookie_line:
    jar = requests.utils.cookiejar_from_dict(_parse_cookie_line(_cookie_line))
    req_session.cookies.update(jar)

# playwright / steel 全局状态
browser = None
playwright_ctx_cookie_dict = None
steel_dict = None
_global_cookie_line = _cookie_line  # 供 playwright 注入用


def _to_playwright_cookies(cookie_line: str) -> list[dict]:
    """将 cookie 字符串转为 Playwright cookie 对象列表"""
    cookie_dict = _parse_cookie_line(cookie_line)
    return [
        {"name": k, "value": v, "domain": "www.wenku8.net", "path": "/"}
        for k, v in cookie_dict.items()
    ]


def init_playwright():
    """初始化本地 Playwright 浏览器"""
    from playwright.sync_api import sync_playwright
    global browser, playwright_ctx_cookie_dict
    if browser is None:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        if _global_cookie_line:
            playwright_ctx_cookie_dict = _to_playwright_cookies(_global_cookie_line)
        else:
            playwright_ctx_cookie_dict = []
    return browser


def init_steel():
    """初始化 Steel 云端浏览器"""
    from steel import Steel
    from dotenv import dotenv_values
    from playwright.sync_api import sync_playwright
    global browser, playwright_ctx_cookie_dict, steel_dict

    steel_api_key = dotenv_values().get('STEEL_API_KEY', '') or os.environ.get('STEEL_API_KEY', '')
    if not steel_api_key:
        raise RuntimeError('[ERROR] Steel 模式需要 STEEL_API_KEY 环境变量或 .env 文件')

    client = Steel(steel_api_key=steel_api_key)
    steel_session = client.sessions.create(api_timeout=40000)
    print(f'[INFO] Running Steel session: {steel_session.id}')
    steel_dict = {
        'api_key': steel_api_key,
        'session_id': steel_session.id,
        'client': client
    }

    if browser is None:
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(
            f'wss://connect.steel.dev?apiKey={steel_api_key}&sessionId={steel_session.id}'
        )
        if _global_cookie_line:
            playwright_ctx_cookie_dict = _to_playwright_cookies(_global_cookie_line)
        else:
            playwright_ctx_cookie_dict = []

    return browser


def exit_steel():
    """释放 Steel 会话"""
    global browser, steel_dict
    if steel_dict:
        try:
            browser.close()
        except Exception:
            pass
        try:
            steel_dict['client'].sessions.release(steel_dict['session_id'])
        except Exception:
            pass
        steel_dict = None
    browser = None


def scrape_page_playwright(url: str) -> str:
    """通过 Playwright（本地或 Steel 远程）获取页面 HTML"""
    global browser, playwright_ctx_cookie_dict
    if browser is None:
        browser = (init_steel() if _scraper == 'steel' else init_playwright())

    with browser.new_context() as context:
        if playwright_ctx_cookie_dict:
            context.add_cookies(playwright_ctx_cookie_dict)
        page = context.new_page()
        try:
            page.goto(url, wait_until='domcontentloaded')
        except Exception as e:
            print(f"[WARN] Page.goto encountered an error or timeout, attempting to proceed: {e}")

        if "/login.php" in page.url:
            raise RuntimeError(f"[ERROR] Playwright 模式被重定向到登录页，Cookie 可能已过期: {page.url}")
        html_content = page.content()
        page.close()
    return html_content


def scrape_page_requests(url: str) -> str:
    """通过 requests 获取页面 HTML"""
    resp = req_session.get(url, timeout=15, allow_redirects=True)
    final_url = resp.url
    if '/login.php' in final_url:
        raise RuntimeError(f"[ERROR] Requests 模式被重定向到登录页，Cookie 可能已过期: {final_url}")
    resp.raise_for_status()
    resp.encoding = 'utf-8'
    return resp.text


def scrape_page(url: str) -> str:
    """统一的页面爬取入口"""
    if _scraper in ('playwright', 'steel'):
        return scrape_page_playwright(url)
    elif _scraper == 'requests':
        return scrape_page_requests(url)
    else:
        raise ValueError(f"Unknown _scraper: {_scraper}")


def _fetch_binary(url: str, timeout: int = 15) -> bytes | None:
    """下载二进制文件（封面图），始终用 requests"""
    try:
        resp = req_session.get(url, timeout=timeout)
        if resp.status_code == 200 and len(resp.content) > 1000:
            return resp.content
    except Exception:
        pass
    return None


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
    html = scrape_page(url)
    soup = BeautifulSoup(html, 'html.parser')

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
    operation_texts = {'我要阅读', '加入书架', '推荐本书'}
    for aid, entries in book_groups.items():
        if aid in seen:
            continue
        seen.add(aid)

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

    Returns:
        {
            'title': str, 'author': str, 'publisher': str,
            'status': str, 'last_update': str, 'length': str,
            'tags': [str], 'description': str,
            'toc_url': str, 'cover_url': str,
        }
    """
    html = scrape_page(book_url)
    soup = BeautifulSoup(html, 'html.parser')

    # 从 <title> 提取书名、作者、文库
    title_tag = soup.find('title')
    page_title = title_tag.text.strip() if title_tag else ''
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

    m = re.search(r'文库分类[：:]\s*(.+)', body_text)
    if m:
        result['publisher'] = m.group(1).strip()

    m = re.search(r'小说作者[：:]\s*(.+)', body_text)
    if m:
        result['author'] = m.group(1).strip()

    m = re.search(r'文章状态[：:]\s*(.+)', body_text)
    if m:
        result['status'] = m.group(1).strip()

    m = re.search(r'最后更新[：:]\s*(\d{4}-\d{2}-\d{2})', body_text)
    if m:
        result['last_update'] = m.group(1).strip()

    m = re.search(r'全文长度[：:]\s*(\d+)字', body_text)
    if m:
        result['length'] = f"{m.group(1)}字"

    m = re.search(r'作品Tags[：:]\s*([^\n]+)', body_text)
    if m:
        result['tags'] = [t.strip() for t in m.group(1).split() if t.strip()]

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
    html = scrape_page(toc_url)
    soup = BeautifulSoup(html, 'html.parser')

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
    html = scrape_page(chapter_url)
    soup = BeautifulSoup(html, 'html.parser')

    content_div = soup.find('div', id='content')
    if not content_div:
        return ''

    text = content_div.get_text('\n', strip=True)
    return text


# ─── 封面下载 ─────────────────────────────────────────


def download_cover(cover_url: str) -> bytes | None:
    """下载封面图片（二进制，始终用 requests）"""
    if not cover_url:
        return None
    return _fetch_binary(cover_url)


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
    print(f'[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] 开始 EPUB 生成管道 (scraper={_scraper})')

    os.makedirs(EPUB_OUT_DIR, exist_ok=True)

    state = _load_state()
    novels_info = state.get('novels', {})
    generated = []
    pipeline_error = None
    toplist = []

    try:
        # Step 1: 爬取最近更新列表
        print('[1/5] 爬取最近更新列表...')
        toplist = crawl_toplist()
        print(f'  获取到 {len(toplist)} 本小说')
    except RuntimeError as e:
        pipeline_error = f'toplist 爬取失败: {e}'
        print(f'  [ERROR] {e}')
        import traceback
        traceback.print_exc()

    if toplist:
        # Step 2: 筛选需要处理的小说
        targets = []
        for novel in toplist:
            aid = str(novel['aid'])
            if force or aid not in novels_info:
                targets.append(novel)
            else:
                prev_info = novels_info[aid]
                prev_date = prev_info.get('last_update', '')
                if prev_date < (datetime.date.today() - datetime.timedelta(days=3)).isoformat():
                    targets.append(novel)

        if limit > 0:
            targets = targets[:limit]

        if not targets:
            print('  没有需要处理的小说，退出。')

        if targets:
            print(f'  需要处理 {len(targets)} 本小说')

            # Step 3: 逐本处理
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

    # ── 以下始终执行，确保 workflow 能读到 summary ──

    # Step 4: 保存状态
    state['novels'] = novels_info
    state['last_run'] = datetime.datetime.now().isoformat()
    _save_state(state)

    # Step 5: 清理 scraper
    _cleanup()

    # Step 6: 输出摘要（无论成功/失败都写入文件）
    print(f'\n[5/5] 管道完成!')
    print(f'  本次生成: {len(generated)} 本 EPUB')
    for g in generated:
        print(f'    - {g["title"]} (v{g["epub_version"]}) -> {g["epub_path"]}')

    if pipeline_error:
        print(f'  错误: {pipeline_error}')

    summary = {
        'timestamp': datetime.datetime.now().isoformat(),
        'generated': generated,
        'total_count': len(generated),
    }
    if pipeline_error:
        summary['error'] = pipeline_error
    summary_file = os.path.join('out', 'epub_summary.json')
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return summary


def _cleanup():
    """清理 scraper 资源"""
    global _scraper
    if _scraper == 'steel':
        exit_steel()


# ─── CLI ───────────────────────────────────────────────


def _detect_scraper() -> str:
    """自动检测可用的爬虫模式（优先 Steel → Playwright → Requests）"""
    # 检查 Steel
    steel_key = os.environ.get('STEEL_API_KEY', '')
    if not steel_key:
        try:
            from dotenv import dotenv_values
            steel_key = dotenv_values().get('STEEL_API_KEY', '')
        except ImportError:
            pass
    if steel_key:
        return 'steel'

    # 检查 Playwright
    try:
        from playwright.sync_api import sync_playwright
        return 'playwright'
    except ImportError:
        pass

    # 回退到 requests（wenku8 有 Cloudflare，可能 403）
    print('[WARN] Steel 和 Playwright 均不可用，使用 requests 模式（可能被 Cloudflare 拦截）')
    return 'requests'


def main():
    parser = argparse.ArgumentParser(description='wenku8 EPUB 自动生成管道')
    parser.add_argument('--scraper', choices=['steel', 'playwright', 'requests', 'auto'],
                        default='auto', help='爬虫模式: auto=自动检测, steel/playwright/requests')
    parser.add_argument('--force', action='store_true', help='强制重新生成所有')
    parser.add_argument('--limit', type=int, default=0, help='限制处理数量')
    parser.add_argument('--max-chapters', type=int, default=0,
                        help='每个小说的最大章节数（0=无限制）')
    args = parser.parse_args()

    global _scraper
    if args.scraper == 'auto':
        _scraper = _detect_scraper()
    else:
        _scraper = args.scraper
    print(f'[INFO] Using scraper: {_scraper}')

    run_pipeline(force=args.force, limit=args.limit)


if __name__ == '__main__':
    main()
