import requests
from bs4 import BeautifulSoup
import csv
import time
import random
from urllib.parse import urljoin
import sys
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

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
    for tr in rows:
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

if __name__ == '__main__':
    if len(sys.argv) == 3:
        last_page = int(sys.argv[1])
        total_pages = int(sys.argv[2])
        resume(last_page, total_pages)
    else:
        main()
