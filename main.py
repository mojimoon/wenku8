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

BASE_URL = 'https://www.wenku8.net/modules/article/reviewslist.php'
PARAMS = {
    't': '1',
    'keyword': '8691',
    'charset': 'gbk',
    'page': 1
}
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/90.0.4430.93 Safari/537.36'
}
OUTPUT_CSV = 'summary.csv'
DOMAIN = 'https://www.wenku8.net'
LATEST_TXT = 'latest.txt'
OUTPUT_JSON = 'summary.json'
INDEX_HTML = 'index.html'

retry_strategy = Retry(
    total=5,
    status_forcelist=[500, 502, 503, 504],
    backoff_factor=2,
)
session = requests.Session()
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount('http://', adapter)
session.mount('https://', adapter)
session.headers.update(HEADERS)

# BEGIN web scraping
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
            raise ValueError("No valid link found in the response.")

    print(f"Latest link found: {link}")

    return link

def get_latest(url):
    resp = session.get(url, timeout=10)
    resp.raise_for_status()
    resp.encoding = 'utf-8'

    with open(LATEST_TXT, 'w', encoding='utf-8') as f:
        f.write(resp.text)

    print(f"Latest file saved to {LATEST_TXT}")

def get_last_page():
    """Fetch the first page and parse the total number of pages."""
    resp = session.get(BASE_URL, params=PARAMS)
    resp.encoding = 'gbk'
    soup = BeautifulSoup(resp.text, 'html.parser')
    last = soup.find('a', class_='last')
    return int(last.text) if last else 1

def parse_page(page_num):
    """Fetch and parse one page of reviews, returning a list of entries."""
    PARAMS['page'] = page_num
    resp = session.get(BASE_URL, params=PARAMS, timeout=10)
    resp.raise_for_status()
    resp.encoding = 'gbk'
    soup = BeautifulSoup(resp.text, 'html.parser')
    # table = soup.find('table', class_='grid')
    table = soup.find_all('table', class_='grid')[1]
    rows = table.find_all('tr')[1:]  # skip header row

    entries = []
    for (i, tr) in enumerate(rows):
        cols = tr.find_all('td')
        if len(cols) < 2:
            continue
        # Post title & link
        a_post = cols[0].find('a')
        raw_title = a_post.text.strip()
        if not raw_title.endswith(' epub'):
            continue
        post_title = raw_title[:-5] if raw_title.endswith(' epub') else raw_title
        post_link = a_post['href'] if a_post['href'].startswith('http') else urljoin(DOMAIN, a_post['href'])

        # Novel title & link
        a_novel = cols[1].find('a')
        novel_title = a_novel.text.strip()
        novel_link = urljoin(DOMAIN, a_novel['href'])

        # entries.append({
        #     'post_title': post_title,
        #     'post_link': post_link,
        #     'novel_title': novel_title,
        #     'novel_link': novel_link
        # })
        post_title = '"' + post_title + '"'
        novel_title = '"' + novel_title + '"'
        entries.append([post_title, post_link, novel_title, novel_link])

        if page_num == 1 and i == 0:
            get_latest(get_latest_url(post_link))
    return entries

def scrape():
    total_pages = get_last_page()
    print(f"Total pages found: {total_pages}")

    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as csvfile:
        # fieldnames = ['post_title', 'post_link', 'novel_title', 'novel_link']
        # writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        # writer.writeheader()
        csvfile.write('post_title,post_link,novel_title,novel_link\n')

        for page in range(1, total_pages + 1):
            print(f"Processing page {page}/{total_pages}...")
            entries = parse_page(page)
            for entry in entries:
                csvfile.write(','.join(entry) + '\n')
                # writer.writerow(entry)
            time.sleep(random.uniform(1, 3))

    print(f"Done. Data saved to {OUTPUT_CSV}")

def resume(last, tot):
    print(f"Resuming from page {last}/{tot}...")

    with open(OUTPUT_CSV, 'a', newline='', encoding='utf-8') as csvfile:
        for page in range(last, tot + 1):
            print(f"Processing page {page}/{tot}...")
            entries = parse_page(page)
            for entry in entries:
                csvfile.write(','.join(entry) + '\n')
            time.sleep(random.uniform(1, 3))

    print(f"Done. Data saved to {OUTPUT_CSV}")
# END web scraping

# BEGIN data processing
def purify(text):
    '''
    只保留汉字、数字和字母
    '''
    text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', text)
    return text

CN_NUM = {
    '零': 0, '一': 1, '二': 2, '三': 3, '四': 4,
    '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
    '十': 10
}

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
    pattern = r'[第]*([一二三四五六七八九十零]{1,3})[卷]*'
    match = re.search(pattern, s)
    if match:
        cn_num = match.group(1)
        arabic_num = chinese_to_arabic(cn_num)
        return s.replace(cn_num, f' {arabic_num} ')
    return s

def match_summary():
    csv_records = []
    with open(OUTPUT_CSV, 'r', encoding='utf-8') as csvfile:
        lines = csvfile.readlines()
        lines = lines[1:-1]
        reader = csv.reader(lines)
        for row in reader:
            if row[3].endswith('2751.htm'):
                patched_name = '我们不可能成为恋人！绝对不行。（※似乎可行？）(我怎么可能成为你的恋人，不行不行！)'
                csv_records.append([replace_chinese_numerals(row[0]), purify(patched_name), patched_name, row[3]])
            else:
                csv_records.append([replace_chinese_numerals(row[0]), purify(row[2]), row[2], row[3]])
        # print(f"CSV records: {len(csv_records)}")
    
    latest_records = []
    with open(LATEST_TXT, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        for line in lines[2:]:
            parts = line.strip().split()
            if parts[1] == 'b04e6koyd':
                continue
            elif parts[1] == 'b04e85ohi':
                _name = '密室中的霍尔顿'
            elif parts[1] == 'b00g38fzcb':
                _name = '某科学的超电磁炮'
            elif parts[1] == 'b04esk38d':
                _name = 'Lycoris Recoil 莉可丽丝 Ordinary days'
            elif parts[1] == 'b00g2u013e':
                _name = 'Tier1姐妹'
            # elif '_' in parts[3]:
            #     _name = parts[3].replace('_', ' ')
            else:
                _name = parts[3]
            latest_records.append([parts[0], parts[1], parts[2], _name])
    
    output = []
    for latest in latest_records:
        p_latest = purify(latest[3])
        matched = None
        for record in csv_records:
            if p_latest in record[1]:
                matched = record
                break
        if matched:
            output.append({
                'post_title': matched[0],
                'novel_title': matched[2],
                'novel_link': matched[3],
                'updated': latest[0],
                'url': latest[1],
                'pwd': latest[2],
            })
        else:
            output.append({
                'post_title': '',
                'novel_title': latest[3],
                'novel_link': '',
                'updated': latest[0],
                'url': latest[1],
                'pwd': latest[2],
            })

    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        # minimal JSON output
        json.dump(output, f, ensure_ascii=False, separators=(',', ':'))
    print(f"Matched summary saved to {OUTPUT_JSON}")
# END data processing

# BEGIN HTML generation
def create_html_table(data):
    rows_html = ""
    for item in data:
        novel_title = item.get("novel_title", "N/A")
        novel_link = item.get("novel_link", "https://www.wenku8.net/")
        post_title = item.get("post_title", "N/A")
        updated = item.get("updated", "N/A")
        url = item.get("url", "")
        pwd = item.get("pwd", "N/A")
        lanzou_link = f"https://wwyt.lanzov.com/{url}" if url else "#"
        lanzou_text = url if url else "N/A"
        novel_alternate_title = None
        if novel_title[-1] == ')': # 半角括号
            last_bracket = novel_title.rfind('(')
            novel_alternate_title = novel_title[last_bracket+1:-1]
            novel_title = novel_title[:last_bracket]
        # if len(post_title) > 2 and post_title[0] == '第' and post_title[-1] == '卷':
        #     post_title = post_title[1:-1]
        if re.search(r'第 \S+ 卷', post_title):
            post_title = re.sub(r'第 (\S+) 卷', r'\1', post_title)
        # https://www.wenku8.net/book/2751.htm -> https://www.wenku8.net/novel/2/2751/index.htm
        # online_read_link = re.sub(r'book/(\d+).htm', r'novel/2/\1/index.htm', novel_link)

        rows_html += f"""
        <tr>
            <td class="novel-title"><a href="{novel_link}" target="_blank">{novel_title}</a>{novel_alternate_title and "<span class='alternate-title'>" + novel_alternate_title + "</span>" or ""}</td>
            <td><a href="{lanzou_link}" target="_blank">{lanzou_text}</a></td>
            <td>{pwd}</td>
            <td>{post_title}</td>
            <td>{updated}</td>
        </tr>
        """
    return rows_html

def generate_html_file(data, output_filename="index.html"):
    table_content = create_html_table(data)

    today = time.strftime("%Y-%m-%d", time.localtime())

    html_template = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="keywords" content="轻小说,sf轻小说,dmzj轻小说,日本轻小说,动漫小说,轻小说电子书,轻小说EPUB下载">
    <meta name="description" content="轻小说文库 EPUB 下载，支持搜索关键字、跳转至源站和蓝奏云下载，已进行移动端适配。">
    <meta name="author" content="mojimoon">
    <title>轻小说文库 EPUB 下载</title>
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <h1>轻小说文库 EPUB 下载</h1>
    <h3>By <a href="https://github.com/mojimoon">mojimoon</a> | <a href="https://github.com/mojimoon/wenku8">Star me</a> | {today}</h3>
    <span>所有内容均收集于网络、仅供学习交流使用，本站仅作整理工作。特别感谢 EPUB 制作者 @<a href="https://www.wenku8.net/modules/article/reviewslist.php?t=1&keyword=8691&charset=gbk">酷儿加冰</a>。</span>
    <span class="alternate-title">蓝奏链接前缀均为 https://wwyt.lanzov.com/</span>

    <div class="controls">
        <button id="themeToggle">日夜</button>
        <input type="text" id="searchInput" placeholder="搜索">
        <button id="randomButton">随机</button>
    </div>

    <table>
        <thead>
            <tr>
                <th>小说</th>
                <th>蓝奏链接</th>
                <th>密码</th>
                <th><span class="mobile-hidden">最新</span>卷</th>
                <th>更新日期</th>
            </tr>
        </thead>
        <tbody id="novelTableBody">
            {table_content}
        </tbody>
    </table>

    <script src="script.js"></script>
</body>
</html>
"""
    try:
        with open(output_filename, 'w', encoding='utf-8') as f:
            f.write(html_template)
        print(f"HTML file generated: {output_filename}")
    except IOError as e:
        print(f"Error writing HTML file: {e}")

def html():
    with open(OUTPUT_JSON, 'r', encoding='utf-8') as f:
        data = json.load(f)
    generate_html_file(data, INDEX_HTML)
# END HTML generation

if __name__ == '__main__':
    if len(sys.argv) == 3:
        last_page = int(sys.argv[1])
        total_pages = int(sys.argv[2])
        resume(last_page, total_pages)
    else:
        scrape()

    match_summary()
    html()
