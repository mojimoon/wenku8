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

# Configuration
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

def get_latest_url(post_link):
    resp = session.get(post_link, timeout=10)
    resp.raise_for_status()
    resp.encoding = 'gbk'

    # <a href="https://paste.gentoo.zip" target="_blank">https://paste.gentoo.zip</a>/EsX5Kx8V
    match = re.search(r'<a href="([^"]+)" target="_blank">([^<]+)</a>(/[^<]+)', resp.text)
    link = match.group(1) + match.group(3) if match else None

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

def main():
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

def purify(text):
    '''
    只保留汉字、数字和字母
    '''
    text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', text)
    return text

def match_summary():
    csv_records = []
    with open(OUTPUT_CSV, 'r', encoding='utf-8') as csvfile:
        lines = csvfile.readlines()
        lines = lines[1:-1]
        reader = csv.reader(lines)
        for row in reader:
            if row[3].endswith('2751.htm'):
                patched_name = '我们不可能成为恋人！绝对不行。（※似乎可行？)(我怎么可能成为你的恋人，不行不行！)'
                csv_records.append([row[0], purify(patched_name), patched_name, row[3]])
            else:
                csv_records.append([row[0], purify(row[2]), row[2], row[3]])
        # print(f"CSV records: {len(csv_records)}")
    
    latest_records = []
    with open(LATEST_TXT, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        for line in lines[2:]:
            parts = line.strip().split()
            if parts[1] == 'b04e6koyd':
                patched_name = '吹响吧！上低音号：欢迎加入北宇治高中管乐社 短篇'
                latest_records.append([parts[0], parts[1], parts[2], patched_name])
            elif parts[1] == 'b04e85ohi':
                patched_name = '密室中的霍尔顿'
                latest_records.append([parts[0], parts[1], parts[2], patched_name])
            else:
                latest_records.append(parts)
    
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

if __name__ == '__main__':
    if len(sys.argv) == 3:
        last_page = int(sys.argv[1])
        total_pages = int(sys.argv[2])
        resume(last_page, total_pages)
    else:
        main()

    match_summary()
    
# get_latest(get_latest_url('https://www.wenku8.net/modules/article/reviewshow.php?rid=295996'))