import requests
from bs4 import BeautifulSoup
import csv
import time
import random
from urllib.parse import urljoin
import sys
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import re
import json
import os
import pandas as pd
import sys

BASE_URL = 'https://www.wenku8.net/modules/article/reviewslist.php'
params = { 'keyword': '8691', 'charset': 'utf-8', 'page': 1 }
# 'requests' | 'playwright'
SCRAPER = 'requests'
user_agents = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36'
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36'
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15'
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15'
]
HEADERS = { 
    # 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
    'User-Agent': random.choice(user_agents),
    'Referer': 'https://www.wenku8.net/',
    # 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    # 'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    # 'Accept-Encoding': 'gzip, deflate, br'
}
DOMAIN = 'https://www.wenku8.net'
OUT_DIR = 'out'
PUBLIC_DIR = 'docs'
COOKIE_FILE = os.path.join(os.path.dirname(__file__), 'COOKIE')
POST_LIST_FILE = os.path.join(OUT_DIR, 'post_list.csv')
TXT_LIST_FILE = os.path.join(OUT_DIR, 'txt_list.csv')
DL_FILE = os.path.join(OUT_DIR, 'dl.txt')
MERGED_CSV = os.path.join(OUT_DIR, 'merged.csv')
EPUB_HTML = os.path.join(PUBLIC_DIR, 'epub.html')
MERGED_HTML = os.path.join(PUBLIC_DIR, 'index.html')

retry_strategy = Retry(
    total=5,
    status_forcelist=[500, 502, 503, 504],
    backoff_factor=2
)
session = requests.Session()
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount('http://', adapter)
session.mount('https://', adapter)
session.headers.update(HEADERS)

def parse_cookie_line(line: str):
    line = line.strip()
    if not line:
        return {}
    cookie_dict = {}
    for part in line.split(';'):
        part = part.strip()
        if not part or '=' not in part:
            continue
        k, v = part.split('=', 1)
        cookie_dict[k.strip()] = v.strip()
    return cookie_dict

def load_cookie_from_file(sess, filepath):
    if not os.path.exists(filepath):
        return
    with open(filepath, 'r', encoding='utf-8') as f:
        # 只取第一行，整行都是 "k1=v1; k2=v2; ..."
        line = f.readline()
    cookie_dict = parse_cookie_line(line)
    if cookie_dict:
        jar = requests.utils.cookiejar_from_dict(cookie_dict)
        sess.cookies.update(jar)

load_cookie_from_file(session, COOKIE_FILE)

browser = None
playwright_ctx_cookie_dict = None  # 缓存解析后的 cookie，给 playwright 用

def get_browser():
    from playwright.sync_api import sync_playwright
    global browser, playwright_ctx_cookie_dict
    if browser is None:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        # 预解析 COOKIE_FILE，供后面 new_context 使用
        if os.path.exists(COOKIE_FILE):
            with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
                line = f.readline()
            playwright_ctx_cookie_dict = parse_cookie_line(line)
        else:
            playwright_ctx_cookie_dict = {}
    return browser

def scrape_page_playwright(url):
    global browser, playwright_ctx_cookie_dict
    if browser is None:
        browser = get_browser()
    # 每次新建 context，并注入 cookie
    with browser.new_context() as context:
        if playwright_ctx_cookie_dict:
            cookies = [
                {
                    "name": k,
                    "value": v,
                    "domain": "www.wenku8.net",
                    "path": "/",
                    # 可按需设置 "httpOnly" / "secure" / "sameSite"
                }
                for k, v in playwright_ctx_cookie_dict.items()
            ]
            context.add_cookies(cookies)
        page = context.new_page()
        page.goto(url, wait_until='networkidle')
        if "/login.php" in page.url:
            raise ValueError(f"[ERROR] Playwright 模式被重定向到登录页，可能需要更新 COOKIE 文件: {page.url}")
        html_content = page.content()
        page.close()
    return html_content

def scrape_page_requests(url):
    # 使用带 COOKIE 的 session 访问，自动跟随重定向
    resp = session.get(url, timeout=10, allow_redirects=True)
    final_url = resp.url
    if '/login.php' in final_url:
        raise ValueError(f"[ERROR] Requests 模式被重定向到登录页，可能需要更新 COOKIE 文件: {final_url}")
    resp.raise_for_status()
    resp.encoding = 'utf-8'
    # with open('debug.html', 'w', encoding='utf-8') as f:
    #     f.write(resp.text)
    return resp.text

def scrape_page(url):
    if SCRAPER == 'playwright':
        return scrape_page_playwright(url)
    elif SCRAPER == 'requests':
        return scrape_page_requests(url)
    else:
        raise ValueError(f"Unknown scraper: {SCRAPER}")

def build_url_with_params(base_url, params):
    if not params:
        return base_url
    query_string = '&'.join(f"{key}={value}" for key, value in params.items())
    # print(f'[DEBUG] Built URL: {base_url}?{query_string}')
    return f"{base_url}?{query_string}"

# ========== Scraping ==========
last_page = 1
def get_latest_url(post_link):
    # resp = session.get(post_link, timeout=10)
    # resp.raise_for_status()
    # resp.encoding = 'utf-8'
    txt = scrape_page(post_link)

    # <a href="https://paste.gentoo.zip" target="_blank">https://paste.gentoo.zip</a>/EsX5Kx8V
    match = re.search(r'<a href="([^"]+)" target="_blank">([^<]+)</a>(/[^<]+)', txt)
    link = match.group(1) + match.group(3) if match else None
    if link is None:
        # <a href="https://0x0.st/8QWZ.txt" target="_blank">https://0x0.st/8QWZ.txt</a><br>
        match = re.search(r'https:\/\/[^"]+?\.txt(?=")', txt)
        if match:
            link = match.group(0)
        else:
            raise ValueError("[ERROR] Failed to find the latest URL")

    return link

def get_latest(url):
    # resp = session.get(url, timeout=10)
    # resp.raise_for_status()
    # resp.encoding = 'utf-8'

    txt = scrape_page(url)
    lines = txt.split('\n')
    flg = [False] * 4
    for i in range(len(lines)):
        if not flg[0] and lines[i].endswith('_杂志连载版'):
            lines[i] = lines[i].replace('_杂志连载版', '')
            flg[0] = True
        elif not flg[1] and lines[i].endswith('_SS'):
            lines[i] = lines[i].replace('_SS', '')
            flg[1] = True
        elif not flg[2] and lines[i].endswith('-Ordinary_days-'):
            lines[i] = lines[i].replace('-Ordinary_days-', ' 莉可丽丝 Ordinary days')
            flg[2] = True
        elif not flg[3] and lines[i].endswith('君若星辰'):
            lines[i] = lines[i].replace('君若星辰', '宛如星辰的你')
            flg[3] = True
    
    txt = '\n'.join(lines)
    # if the content has not changed, exit
    if os.path.exists(DL_FILE):
        with open(DL_FILE, 'r', encoding='utf-8') as f:
            old_txt = f.read()
        if old_txt == txt:
            print('[INFO] Exiting, no update found.')
            sys.exit(0)

    with open(DL_FILE, 'w', encoding='utf-8') as f:
        f.write(txt)

def parse_page(page_num, latest_post_link=None):
    params['page'] = page_num
    url = build_url_with_params(BASE_URL, params)
    txt = scrape_page(url)
    # print(txt)
    soup = BeautifulSoup(txt, 'html.parser')
    table = soup.find_all('table', class_='grid')[1]
    rows = table.find_all('tr')[1:]  # skip header row

    flg = [False] * 2
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

        # 检查是否解析到已存在的最新帖子
        if latest_post_link is not None and post_link == latest_post_link:
            return entries, True  # 返回当前已收集的entries，并标记停止

        a_novel = cols[1].find('a')
        novel_title = a_novel.text.strip()
        novel_link = urljoin(DOMAIN, a_novel['href'])
        if not flg[0] and novel_link.endswith('/2751.htm'):
            novel_title = '我们不可能成为恋人！绝对不行。（※似乎可行？）(我怎么可能成为你的恋人，不行不行！)'
            flg[0] = True
        if not flg[1] and novel_link.endswith('/3828.htm'):
            novel_title = 'Tier1姐妹 有名四姐妹没我就活不下去'
            flg[1] = True

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
    # 获取POST_LIST_FILE中第一个post_link
    latest_post_link = None
    try:
        with open(POST_LIST_FILE, 'r', encoding='utf-8') as f:
            next(f)  # skip header
            first_line = next(f, '').strip()
            if first_line:
                latest_post_link = first_line.split(',')[1]
            file_exists = True
    except FileNotFoundError:
        file_exists = False

    all_entries = []
    stop = False

    # 先爬第一页
    print('[INFO] scrape (1)')
    entries, found = parse_page(1, latest_post_link)
    all_entries.extend(entries)
    stop = found

    # 继续爬剩余页数，直到遇到已存在帖子
    page = 2
    while not stop and page <= last_page:
        print(f'[INFO] scrape ({page}/{last_page})')
        entries, found = parse_page(page, latest_post_link)
        all_entries.extend(entries)
        stop = found
        if stop:
            break
        page += 1
        time.sleep(random.uniform(1, 3))

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

# ========== Data Processing ==========
def purify(text): # 只保留中文、英文和数字
    text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', text)
    return text

CN_NUM = { '零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10 }

def chinese_to_arabic(cn):
    if cn == '十':
        return 10
    elif cn.startswith('十'):
        return 10 + CN_NUM.get(cn[1], 0)
    elif cn.endswith('十'):
        return CN_NUM.get(cn[0], 0) * 10
    elif '十' in cn:
        parts = cn.split('十')
        return CN_NUM.get(parts[0], 0) * 10 + CN_NUM.get(parts[1], 0)
    else:
        return CN_NUM.get(cn, 0)

def replace_chinese_numerals(s):
    match = re.search(r'第([一二三四五六七八九十零]{1,3})卷', s)
    if match:
        cn_num = match.group(1)
        arabic_num = chinese_to_arabic(cn_num)
        s = s.replace(cn_num, f' {arabic_num} ')
    match = re.search(r'第 (\S+) 卷', s)
    if match:
        s = s.replace('第 ', '')
        s = s.replace(' 卷', '')
    return s

UNMATCH = ['时间', '少女', '再见宣言', '强袭魔女', '秋之回忆', '秋之回忆2', '魔王', '青梅竹马', '弹珠汽水']

def merge():
    df_post = pd.read_csv(POST_LIST_FILE, encoding='utf-8')
    df_post.drop_duplicates(subset=['novel_title'], keep='first', inplace=True)
    df_post.reset_index(drop=True, inplace=True)
    df_post['volume'] = df_post['post_title'].apply(replace_chinese_numerals)
    # df_post['post_main'] = df_post['novel_title'].apply(lambda x: x[:x.rfind('(')] if x[-1] == ')' else x)
    df_post['post_alt'] = df_post['novel_title'].apply(lambda x: x[x.rfind('(')+1:-1] if x[-1] == ')' else "")
    df_post['post_pure'] = df_post['novel_title'].apply(purify)
    df_post['post_alt_pure'] = df_post['post_alt'].apply(purify)
    df_post.drop(columns=['post_title'], inplace=True)

    df_post['dl_label'] = ""
    df_post['dl_pwd'] = ""
    df_post['dl_update'] = ""
    df_post['txt_matched'] = False

    # merge dl to post
    with open(DL_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()[2:]
        for line in lines:
            parts = line.strip().split()
            if len(parts) < 4:
                continue
            mask = df_post['post_pure'].str.match(purify(parts[-1]))
            if mask.any():
                df_post.loc[mask, 'dl_update'] = parts[0]
                df_post.loc[mask, 'dl_label'] = parts[1]
                df_post.loc[mask, 'dl_pwd'] = parts[2]
            #     if mask.sum() > 1:
            #         print(f'[WARN] {mask.sum()} entries matched for {parts[3]}')
            # else:
            #     print(f'[WARN] Failed to match {parts[3]}')
    
    # merge post to txt
    df_txt = pd.read_csv(TXT_LIST_FILE, encoding='utf-8')
    df_txt['txt_pure'] = df_txt['title'].apply(purify) # 4
    df_txt['volume'] = '' # 5
    df_txt['dl_label'] = '' # 6
    df_txt['dl_pwd'] = '' # 7
    df_txt['dl_update'] = None # 8
    df_txt['novel_title'] = '' # 9
    df_txt['novel_link'] = '' # 10
    for i in range(len(df_txt)):
        _title = df_txt.iloc[i, 0]
        if _title in UNMATCH:
            continue
        mask = df_post['post_pure'].str.match(df_txt.iloc[i, 4]) & (df_post['txt_matched'] == False)
        match = None
        if mask.any():
            # if _title.startswith('魔女之旅'):
            #     match = mask[mask].index[1]
            # else:
            match = mask[mask].index[0]
            # if mask.sum() > 1:
            #     print(f'[WARN] {mask.sum()} entries matched for {_title}')
            #     for j in range(len(df_post)):
            #         if mask[j]:
            #             print(f'    {df_post.iloc[j]["novel_title"]}')
        else:
            mask = df_post['post_alt_pure'].str.match(df_txt.iloc[i, 4]) & (df_post['txt_matched'] == False)
            if mask.any():
                match = mask[mask].index[0]
                # if mask.sum() > 1:
                #     print(f'[WARN] {mask.sum()} entries matched for {_title}')
                #     for j in range(len(df_post)):
                #         if mask[j]:
                #             print(f'    {df_post.iloc[j]["novel_title"]}')
        if match is not None:
            df_txt.iloc[i, 5] = df_post.iloc[match]['volume']
            df_txt.iloc[i, 6] = df_post.iloc[match]['dl_label']
            df_txt.iloc[i, 7] = df_post.iloc[match]['dl_pwd']
            df_txt.iloc[i, 8] = df_post.iloc[match]['dl_update']
            df_txt.iloc[i, 9] = df_post.iloc[match]['novel_title']
            df_txt.iloc[i, 10] = df_post.iloc[match]['novel_link']
            df_post.iloc[match, -1] = True
    
    _mask = df_post['txt_matched'] == False
    for y in df_post[_mask].itertuples():
        if y.dl_label == "":
            continue
        df_txt.loc[len(df_txt)] = ["", "", None, "", "", y.volume, y.dl_label, y.dl_pwd, y.dl_update, y.novel_title, y.novel_link]
    
    df_txt['title'] = df_txt.apply(lambda x: x['novel_title'] if x['novel_title'] else x['title'], axis=1)
    df_txt['update'] = df_txt.apply(lambda x: x['dl_update'] if x['dl_update'] else x['date'], axis=1)
    df_txt['main'] = df_txt['title'].apply(lambda x: x[:x.rfind('(')] if x[-1] == ')' else x)
    df_txt['alt'] = df_txt['title'].apply(lambda x: x[x.rfind('(')+1:-1] if x[-1] == ')' else "")
    df_txt.drop(columns=['title', 'date', 'txt_pure', 'novel_title'], inplace=True)
    df_txt.sort_values(by=['update'], ascending=False, inplace=True)
    df_txt.to_csv(MERGED_CSV, index=False, encoding='utf-8-sig')

# ========== HTML Generation ==========
starme = '<iframe style="margin-left: 2px; margin-bottom:-5px;" frameborder="0" scrolling="0" width="81px" height="20px" src="https://ghbtns.com/github-btn.html?user=mojimoon&repo=wenku8&type=star&count=true" ></iframe>'
def create_table_merged(df):
    rows = []
    for _, row in df.iterrows():
        _l, _m, _a, _txt, _dll, _u, _at, _v = row['novel_link'], row['main'], row['alt'], row['download_url'], row['dl_label'], row['update'], row['author'], row['volume']
        novel_link = None if pd.isna(_l) else _l
        title_html = f'<a href="{novel_link}" target="_blank">{_m}</a>' if novel_link else _m
        alt_html = '' if pd.isna(_a) else f"<span class='at'>{_a}</span>"
        txt_dl = '' if pd.isna(_txt) else f"<a href='{_txt}' target='_blank'>下载</a> <a href='https://ghfast.top/{_txt}' target='_blank'>镜像</a>"
        volume = '' if pd.isna(_v) else _v
        # volume = volume[:3].strip() if len(volume) > 3 else volume
        lz_dl = '' if pd.isna(_dll) else f"<a href='https://wwyt.lanzov.com/{_dll}' target='_blank'>({volume})</a>"
        date = '' if pd.isna(_u) else _u
        author = '' if pd.isna(_at) else _at
        lz_pwd = '' if pd.isna(_dll) else row['dl_pwd']
        rows.append(
            f"<tr><td>{title_html}{alt_html}</td>"
            f"<td class='au'>{author}</td><td>{lz_dl}</td><td>{lz_pwd}</td>"
            f"<td class='dl'>{txt_dl}</td><td class='yd'>{date}</td></tr>"
        )
    return ''.join(rows)

def create_html_merged():
    df = pd.read_csv(MERGED_CSV, encoding='utf-8-sig')
    table = create_table_merged(df)
    today = time.strftime('%Y-%m-%d', time.localtime())
    html = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
        '<meta name="viewport"content="width=device-width,initial-scale=1.0">'
        '<meta name="keywords"content="轻小说,sf轻小说,dmzj轻小说,日本轻小说,动漫小说,轻小说电子书,轻小说EPUB下载">'
        '<meta name="description"content="轻小说文库 EPUB 下载，支持搜索关键字、跳转至源站和蓝奏云下载，已进行移动端适配。">'
        '<meta name="author"content="mojimoon"><title>轻小说文库 EPUB 下载+</title>'
        '<link rel="stylesheet"href="https://gcore.jsdelivr.net/gh/mojimoon/wenku8@gh-pages/style.css"></head><body>'
        '<h1 onclick="window.location.reload()">轻小说文库 EPUB 下载+</h1>'
        f'<h4>({today}) <a href="https://github.com/mojimoon">mojimoon</a>/<a href="https://github.com/mojimoon/wenku8">wenku8</a> {starme}</h4>'
        '<span>所有内容均收集于网络，仅供学习交流使用。'
        '特别感谢 <a href="https://www.wenku8.net/modules/article/reviewslist.php?keyword=8691&charset=utf-8">酷儿加冰</a> 和 <a href="https://github.com/ixinzhi">布客新知</a> 整理。</span>'
        '<span class="at">最新为 Calibre 生成 EPUB，括号内为最新卷数；年更为纯文本 EPUB。</span>'
        '<div class="right-controls"><a href="./epub.html">'
        '<button class="btn"id="gotoButton">切换到仅 EPUB 源，加载更快</button></a>'
        '<button class="btn"id="themeToggle">主题</button>'
        '<button class="btn"id="clearInput">清除</button></div>'
        '<div class="search-bar"><input type="text"id="searchInput"placeholder="搜索标题或作者">'
        '<button class="btn"id="randomButton">随机</button></div>'
        '<table><thead><tr><th>标题</th><th>作者</th><th>最新</th><th>密码</th><th>年更</th><th>更新</th></tr>'
        '</thead><tbody id="novelTableBody">'
        f'{table}</tbody></table><script src="https://gcore.jsdelivr.net/gh/mojimoon/wenku8@gh-pages/script_merged.js"></script>'
        '</body></html>'
    )
    with open(MERGED_HTML, 'w', encoding='utf-8') as f:
        f.write(html)

def create_table_epub(df):
    rows = []
    for _, row in df.iterrows():
        _l, _m, _a, _dll, _at = row['novel_link'], row['main'], row['alt'], row['dl_label'], row['author']
        novel_link = None if pd.isna(_l) else _l
        title_html = f'<a href="{novel_link}" target="_blank">{_m}</a>' if novel_link else _m
        alt_html = '' if pd.isna(_a) else f"<span class='at'>{_a}</span>"
        lz_dl = f"<a href='https://wwyt.lanzov.com/{_dll}' target='_blank'>({row['volume']})</a>"
        author = '' if pd.isna(_at) else _at
        rows.append(
            f"<tr><td>{title_html}{alt_html}</td>"
            f"<td class='au'>{author}</td><td>{lz_dl}</td><td>{row['dl_pwd']}</td>"
            f"<td class='yd'>{row['update']}</td></tr>"
        )
    return ''.join(rows)

def create_html_epub():
    df = pd.read_csv(MERGED_CSV, encoding='utf-8-sig')
    df = df[df['dl_label'].notna()]
    table = create_table_epub(df)
    today = time.strftime('%Y-%m-%d', time.localtime())
    html = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
        '<meta name="viewport"content="width=device-width,initial-scale=1.0">'
        '<meta name="keywords"content="轻小说,sf轻小说,dmzj轻小说,日本轻小说,动漫小说,轻小说电子书,轻小说EPUB下载">'
        '<meta name="description"content="轻小说文库 EPUB 下载，支持搜索关键字、跳转至源站和蓝奏云下载，已进行移动端适配。">'
        '<meta name="author"content="mojimoon"><title>轻小说文库 EPUB 下载</title>'
        '<link rel="stylesheet"href="https://gcore.jsdelivr.net/gh/mojimoon/wenku8@gh-pages/style.css"></head><body>'
        '<h1 onclick="window.location.reload()">轻小说文库 EPUB 下载</h1>'
        f'<h4>({today}) <a href="https://github.com/mojimoon">mojimoon</a>/<a href="https://github.com/mojimoon/wenku8">wenku8</a> {starme}</h4>'
        '<span>所有内容均收集于网络，仅供学习交流使用。'
        '特别感谢 <a href="https://www.wenku8.net/modules/article/reviewslist.php?keyword=8691&charset=utf-8">酷儿加冰</a> 整理。括号内为最新卷数。</span>'
        '<div class="right-controls"><a href="./index.html">'
        '<button class="btn"id="gotoButton">切换到 EPUB/TXT 源，内容更全</button></a>'
        '<button class="btn"id="themeToggle">主题</button>'
        '<button class="btn"id="clearInput">清除</button></div>'
        '<div class="search-bar"><input type="text"id="searchInput"placeholder="搜索标题或作者">'
        '<button class="btn"id="randomButton">随机</button></div>'
        '<table><thead><tr><th>标题</th><th>作者</th><th>蓝奏</th><th>密码</th><th>更新</th></tr>'
        '</thead><tbody id="novelTableBody">'
        f'{table}</tbody></table><script src="https://gcore.jsdelivr.net/gh/mojimoon/wenku8@gh-pages/script_merged.js"></script>'
        '</body></html>'
    )
    with open(EPUB_HTML, 'w', encoding='utf-8') as f:
        f.write(html)

def main():
    if not os.path.exists(OUT_DIR):
        os.mkdir(OUT_DIR)
    if not os.path.exists(PUBLIC_DIR):
        os.mkdir(PUBLIC_DIR)
    
    scrape()
    merge()
    create_html_merged()
    create_html_epub()

if __name__ == '__main__':
    main()
