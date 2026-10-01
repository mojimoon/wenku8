from bs4 import BeautifulSoup
import csv
import time
import random
from urllib.parse import urljoin
import sys
import re
import json
import os
import pandas as pd

from utils import LEVELS, Fetcher

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
def get_latest_url(post_link: str):
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
    # if the content has not changed, exit
    if os.path.exists(DL_FILE):
        with open(DL_FILE, 'r', encoding='utf-8') as f:
            old_txt = f.read()
        if old_txt == txt:
            print('[INFO] Exiting, no update found.')
            sys.exit(0)

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
        # get_latest 中 sys.exit(0) 或异常退出时也要释放 Steel 会话
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

# ========== Data Processing ==========
def purify(text: str) -> str: # 只保留中文、英文和数字
    text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', text)
    return text

CN_NUM = { '零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10 }

def chinese_to_arabic(cn: str) -> int:
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

def replace_chinese_numerals(s: str) -> str:
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

_prefix = ''
IGNORED_TITLES = ['时间', '少女', '再见宣言', '强袭魔女', '秋之回忆', '秋之回忆2', '魔王', '青梅竹马', '弹珠汽水']

def merge():
    global _prefix
    if _scraper == 'none':
        _prefix = 'wenku8.lanzov.com'
        print('[INFO] Skipping merge.')
        return
    
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
    df_post['dl_remark'] = ""
    df_post['txt_matched'] = False

    # merge dl to post
    with open(DL_FILE, 'r', encoding='utf-8') as f:
        _ = f.readlines()
        # <html><head><meta name="color-scheme" content="light dark"></head><body><pre style="word-wrap: break-word; white-space: pre-wrap;"> 网址前缀：wenku8.lanzov.com/
        _prefix = _[0].split('：')[-1].strip() # ends with '/'
        # print(f"[DEBUG] DL prefix: {_prefix}")
        lines = _[2:]
        for line in lines:
            parts = line.strip().split()
            if len(parts) < 4:
                continue
            mask = df_post['post_pure'].str.match(purify(parts[-1]))
            if mask.any():
                df_post.loc[mask, 'dl_update'] = parts[0]
                df_post.loc[mask, 'dl_label'] = parts[1]
                df_post.loc[mask, 'dl_pwd'] = parts[2]
                if len(parts) > 4:
                    if parts[3][:2] == '更新' or parts[3][:2] == '补全':
                        df_post.loc[mask, 'dl_remark'] = parts[3][2:]
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
    df_txt['dl_remark'] = '' # 9
    df_txt['novel_title'] = '' # 10
    df_txt['novel_link'] = '' # 11
    for i in range(len(df_txt)):
        _title = df_txt.iloc[i, 0]
        if _title in IGNORED_TITLES:
            continue
        mask = df_post['post_pure'].str.match(df_txt.iloc[i, 4]) & (df_post['txt_matched'] == False)
        match = None
        if mask.any():
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
            df_txt.iloc[i, 9] = df_post.iloc[match]['dl_remark']
            df_txt.iloc[i, 10] = df_post.iloc[match]['novel_title']
            df_txt.iloc[i, 11] = df_post.iloc[match]['novel_link']
            df_post.iloc[match, -1] = True
    
    _mask = df_post['txt_matched'] == False
    for y in df_post[_mask].itertuples():
        if y.dl_label == "":
            continue
        df_txt.loc[len(df_txt)] = ["", "", None, "", "", y.volume, y.dl_label, y.dl_pwd, y.dl_update, y.dl_remark, y.novel_title, y.novel_link]
    
    df_txt['title'] = df_txt.apply(lambda x: x['novel_title'] if x['novel_title'] else x['title'], axis=1)
    df_txt['update'] = df_txt.apply(lambda x: x['dl_update'] if x['dl_update'] else x['date'], axis=1)
    df_txt['main'] = df_txt['title'].apply(lambda x: x[:x.rfind('(')] if x[-1] == ')' else x)
    df_txt['alt'] = df_txt['title'].apply(lambda x: x[x.rfind('(')+1:-1] if x[-1] == ')' else "")
    df_txt.drop(columns=['title', 'date', 'txt_pure', 'novel_title'], inplace=True)
    df_txt.sort_values(by=['update'], ascending=False, inplace=True)
    df_txt.to_csv(MERGED_CSV, index=False, encoding='utf-8-sig')

# ========== HTML Generation ==========
GH_PROXY = 'https://gh-proxy.org/'   # 所有 GitHub 下载统一走该代理（页面内拼接）
RAW_PREFIX = 'https://raw.githubusercontent.com/'
TEMPLATE_FILE = os.path.join('source', 'template.html')
TXT_META_CSV = os.path.join(OUT_DIR, 'txt_meta.csv')
EPUB_INDEX_FILE = os.path.join(OUT_DIR, 'epub_index.json')
CATALOG_FILE = os.path.join(OUT_DIR, 'wenku_catalog.json')

def _s(v):
    """NaN/None -> ''，其余转 str 并去空白"""
    return '' if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()

def create_data():
    """合并后的条目 + 重制版 EPUB 索引 -> 页面内嵌的 JSON 数据。"""
    df = pd.read_csv(MERGED_CSV, encoding='utf-8-sig', dtype=str)

    # 仅 TXT 源条目通过 txt_meta.csv 补全 wenku8 的 aid
    txt_aid = {}
    if os.path.exists(TXT_META_CSV):
        meta = pd.read_csv(TXT_META_CSV, encoding='utf-8-sig', dtype=str)
        txt_aid = dict(zip(meta['download_url'], meta['aid']))

    # 蓝奏条目常缺作者：用 wenku8 全站目录（fill_meta.py 生成）按 aid 补全
    cat_author = {}
    if os.path.exists(CATALOG_FILE):
        with open(CATALOG_FILE, 'r', encoding='utf-8') as f:
            for page in json.load(f)['pages'].values():
                for it in page:
                    cat_author[str(it['aid'])] = it.get('author', '')

    built = {}
    if os.path.exists(EPUB_INDEX_FILE):
        with open(EPUB_INDEX_FILE, 'r', encoding='utf-8') as f:
            for aid, e in json.load(f).items():
                if e.get('volumes') and e.get('tag'):
                    built[aid] = {'t': e['tag'], 'v': [[v['file'], v['title'], v['size']] for v in e['volumes']]}

    items = []
    for row in df.to_dict('records'):
        link, txt = _s(row['novel_link']), _s(row['download_url'])
        m = re.search(r'/book/(\d+)', link)
        aid = m.group(1) if m else txt_aid.get(txt, '')
        item = {'t': _s(row['main']), 'a': _s(row['alt']), 'au': _s(row['author']) or cat_author.get(aid, ''), 'u': _s(row['update']),
                'n': aid, 'l': _s(row['dl_label']), 'p': _s(row['dl_pwd']), 'v': _s(row['volume']),
                'r': _s(row['dl_remark']),
                'x': txt[len(RAW_PREFIX):] if txt.startswith(RAW_PREFIX) else txt}
        if aid in built and not item['l']:   # 重制版仅用于没有蓝奏 EPUB 源的条目
            item['b'] = 1
        items.append({k: v for k, v in item.items() if v != ''})
    only_built = {it['n']: built[it['n']] for it in items if it.get('b')}
    prefix = _prefix
    if not prefix and os.path.exists(DL_FILE):   # 未运行 merge() 时（如仅重新生成页面）从 dl.txt 读取
        with open(DL_FILE, 'r', encoding='utf-8') as f:
            prefix = f.readline().split('：')[-1].strip()
    lz = 'https://' + prefix.rstrip('/') + '/'
    return {'items': items, 'built': only_built, 'lz': lz, 'gh': GH_PROXY}

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
                '<meta http-equiv="refresh" content="0;url=./?f=epub"><title>轻小说文库 EPUB 下载</title></head>'
                '<body><a href="./?f=epub">前往新版页面</a></body></html>')

def main():
    if not os.path.exists(OUT_DIR):
        os.mkdir(OUT_DIR)
    if not os.path.exists(PUBLIC_DIR):
        os.mkdir(PUBLIC_DIR)
    
    scrape()
    merge()
    create_html()

if __name__ == '__main__':
    if len(sys.argv) > 1:
        _scraper = sys.argv[1]
        if _scraper not in ('none', 'auto', *LEVELS):
            sys.exit(f"Unknown scraper: {_scraper} (可选: none, auto, {', '.join(LEVELS)})")
    main()
