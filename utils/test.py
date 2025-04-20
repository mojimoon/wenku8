
'''
https://www.wenku8.net/modules/article/reviewslist.php?t=1&keyword=8691&charset=gbk&page={}
'''

import os

os.environ['NLS_LANG'] = 'SIMPLIFIED CHINESE_CHINA.UTF8'

'''
first step: scrawl and save as index.html
'''

import requests
import time

request_headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
}

def get_html(url):
    try:
        response = requests.get(url, headers=request_headers, timeout=10)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        return response.text
    except requests.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return None

def main():
    get_url = 'https://www.wenku8.net/modules/article/reviewslist.php?t=1&keyword=8691&charset=gbk&page=1'
    html = get_html(get_url)
    if html:
        with open('index.html', 'w', encoding='utf-8') as f:
            f.write(html)
        print("HTML saved to index.html")
    else:
        print("Failed to fetch HTML")

if __name__ == "__main__":
    main()
