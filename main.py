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

BASE_URL = 'https://www.wenku8.net/modules/article/reviewslist.php'
params = { 'keyword': '8691', 'charset': 'gbk', 'page': 1 }
HEADERS = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3' }
DOMAIN = 'https://www.wenku8.net'
POST_LIST_FILE = os.path.join('out', 'post_list.csv')
DL_FILE = os.path.join('out', 'dl.txt')

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
    with open(DL_FILE, 'w', encoding='utf-8') as f:
        f.write(resp.text)

def parse_page(page_num):
    params['page'] = page_num
    resp = session.get(BASE_URL, params=params, timeout=10)
    resp.raise_for_status()
    resp.encoding = 'gbk'
    soup = BeautifulSoup(resp.text, 'html.parser')
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

def main():
    if not os.path.exists('out'):
        os.mkdir('out')
    if not os.path.exists('public'):
        os.mkdir('public')
    
    scrape()

if __name__ == '__main__':
    main()