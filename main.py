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

BASE_URL = 'https://www.wenku8.net/modules/article/reviewslist.php'
params = { 'keyword': '8691', 'charset': 'gbk', 'page': 1 }
HEADERS = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3' }
DOMAIN = 'https://www.wenku8.net'
OUT_DIR = 'out'
PUBLIC_DIR = 'public'
POST_LIST_FILE = os.path.join(OUT_DIR, 'post_list.csv')
TXT_LIST_FILE = os.path.join(OUT_DIR, 'txt_list.csv')
DL_FILE = os.path.join(OUT_DIR, 'dl.txt')
MERGED_CSV = os.path.join(OUT_DIR, 'merged.csv')
EPUB_HTML = os.path.join(PUBLIC_DIR, 'index.html')
MERGED_HTML = os.path.join(PUBLIC_DIR, 'merged.html')

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

# ========== Scraping ==========
last_page = 1
def get_latest_url(post_link):
    resp = session.get(post_link, timeout=10)
    resp.raise_for_status()
    resp.encoding = 'gbk'

    # <a href="https://paste.gentoo.zip" target="_blank">https://paste.gentoo.zip</a>/EsX5Kx8V
    match = re.search(r'<a href="([^"]+)" target="_blank">([^<]+)</a>(/[^<]+)', resp.text)
    link = match.group(1) + match.group(3) if match else None
    if link is None:
        # <a href="https://0x0.st/8QWZ.txt" target="_blank">https://0x0.st/8QWZ.txt</a><br>
        match = re.search(r'https:\/\/[^"]+?\.txt(?=")', resp.text)
        if match:
            link = match.group(0)
        else:
            raise ValueError("[ERROR] Failed to find the latest URL")

    return link

def get_latest(url):
    resp = session.get(url, timeout=10)
    resp.raise_for_status()
    resp.encoding = 'utf-8'

    txt = resp.text
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

    with open(DL_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

def parse_page(page_num):
    params['page'] = page_num
    resp = session.get(BASE_URL, params=params, timeout=10)
    resp.raise_for_status()
    resp.encoding = 'gbk'
    soup = BeautifulSoup(resp.text, 'html.parser')
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
    return entries

def scrape():
    entries = parse_page(1)
    for page in range(2, last_page + 1):
        print(f'[INFO] scrape ({page}/{last_page})')
        entries.extend(parse_page(page))
        time.sleep(random.uniform(1, 3))
    with open(POST_LIST_FILE, 'w', encoding='utf-8', newline='') as f:
        f.write('post_title,post_link,novel_title,novel_link\n')
        for entry in entries:
            f.write(','.join(entry) + '\n')

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
            mask = df_post['post_pure'].str.match(purify(parts[3]))
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
            if _title.startswith('魔女之旅'):
                match = mask[mask].index[1]
            else:
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

def create_table_merged(df):
    rows = []
    for _, row in df.iterrows():
        _l, _m, _a, _txt, _dll, _u, _at, _v = row['novel_link'], row['main'], row['alt'], row['download_url'], row['dl_label'], row['update'], row['author'], row['volume']
        novel_link = None if pd.isna(_l) else _l
        title_html = f'<a href="{novel_link}" target="_blank">{_m}</a>' if novel_link else _m
        alt_html = '' if pd.isna(_a) else f"<span class='at'>{_a}</span>"
        txt_dl = '' if pd.isna(_txt) else f"<a href='{_txt}' target='_blank'>下载</a> <a href='https://ghproxy.com/{_txt}' target='_blank'>镜像</a>"
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
        '<link rel="stylesheet"href="style.css"></head><body>'
        '<h1 onclick="window.location.reload()">轻小说文库 EPUB 下载+</h1>'
        f'<h3>By <a href="https://github.com/mojimoon">mojimoon</a> | <a href="https://github.com/mojimoon/wenku8">Star me</a> | {today}</h3>'
        '<span>所有内容均收集于网络，仅供学习交流使用。'
        '特别感谢 <a href="https://www.wenku8.net/modules/article/reviewslist.php?keyword=8691&charset=gbk">酷儿加冰</a> 和 <a href="https://github.com/ixinzhi">布客新知</a> 整理。</span>'
        '<span class="at">蓝奏为 Calibre 生成 EPUB，合集为纯文本 EPUB。</span>'
        '<div class="right-controls"><a href="./index.html">'
        '<button class="btn"id="gotoButton">切换到仅 EPUB 源</button></a>'
        '<button class="btn"id="themeToggle">主题</button>'
        '<button class="btn"id="clearInput">清除</button></div>'
        '<div class="search-bar"><input type="text"id="searchInput"placeholder="搜索">'
        '<button class="btn"id="randomButton">随机</button></div>'
        '<table><thead><tr><th>标题</th><th>作者</th><th>蓝奏</th><th>密码</th><th>合集</th><th>更新</th></tr>'
        '</thead><tbody id="novelTableBody">'
        f'{table}</tbody></table><script src="script_merged.js"></script>'
        '</body></html>'
    )
    with open(MERGED_HTML, 'w', encoding='utf-8') as f:
        f.write(html)

def main():
    if not os.path.exists(OUT_DIR):
        os.mkdir(OUT_DIR)
    if not os.path.exists(PUBLIC_DIR):
        os.mkdir(PUBLIC_DIR)
    
    # scrape()
    merge()
    create_html_merged()

if __name__ == '__main__':
    main()