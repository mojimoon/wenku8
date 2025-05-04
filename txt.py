import requests
import json
import time
import pandas as pd
import os
import json
import re

# /repos/{owner}/{repo}/contents/

API_URL = "https://api.github.com/repos/ixinzhi/{repo}/contents/"

REPOS = [
    # "lightnovel-2009to2013",
    "lightnovel-2014to2017",
    # "lightnovel-2018to2020",
    "lightnovel-2021",
    "lightnovel-2022",
    "lightnovel-2023",
    "lightnovel-2024",
]

ALL_REPOS = [
    "lightnovel-2009to2013",
    "lightnovel-2014to2017",
    "lightnovel-2018to2020",
    "lightnovel-2021",
    "lightnovel-2022",
    "lightnovel-2023",
    "lightnovel-2024",
]

HEADERS = {
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "python-requests/2.25.1",
}

OUTPUT_DIR = "txt"
CSV_OUTPUT = "txt.csv"
HTML_OUTPUT = "txt.html"
SUMMARY_JSON = "summary.json"

def scrape_repo(repo):
    url = API_URL.format(repo=repo)
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        print(f"Failed to fetch data from {url}: {response.status_code}")
        return None

    data = response.json()
    df = pd.DataFrame(data)
    df = df[["name", "download_url"]]

    '''
    ! 惊叹号 - 二宫敦人 - 20140303.epub
    '''

    parts_df = df["name"].str.split(" - ", expand=True)
    df["title"] = parts_df.iloc[:, :-2].apply(lambda x: " - ".join(x), axis=1)
    df["author"] = parts_df.iloc[:, -2]
    df["date"] = parts_df.iloc[:, -1].apply(lambda x: x.split(".")[0])
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")

    df = df[["title", "author", "date", "download_url"]]

    df.to_csv(os.path.join(OUTPUT_DIR, f"{repo}.csv"), index=False, encoding="utf-8-sig")

def scrape_all():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)
    for repo in REPOS:
        scrape_repo(repo)
        time.sleep(1)

def purify(text):
    '''
    只保留汉字、数字和字母
    '''
    text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', text)
    return text

def merge_csv_files():
    all_data = []
    for repo in REPOS:
        df = pd.read_csv(os.path.join(OUTPUT_DIR, f"{repo}.csv"), encoding="utf-8-sig")
        all_data.append(df)

    merged_df = pd.concat(all_data, ignore_index=True)
    print(f"Total rows before deduplication: {len(merged_df)}")
    # merged_df = merged_df.sort_values(by=["title", "author", "date"], ascending=[True, True, False])
    # merged_df = merged_df.drop_duplicates(subset=["title", "author"], keep="first")
    merged_df = merged_df.drop_duplicates(subset=["title", "author"], keep="last")
    print(f"Total rows after deduplication: {len(merged_df)}")
    merged_df = merged_df.sort_values(by=["date"], ascending=False)

    summary_df = pd.read_json(SUMMARY_JSON, encoding="utf-8-sig")
    summary_df["purified_title"] = summary_df["novel_title"].apply(purify)
    summary_df["main_title"] = summary_df["novel_title"].apply(lambda x: x[:x.rfind('(')] if x[-1] == ')' else x)
    summary_df["alternate_title"] = summary_df["novel_title"].apply(lambda x: x[x.rfind('(')+1:-1] if x[-1] == ')' else "")
    summary_df["purified_main_title"] = summary_df["main_title"].apply(purify)
    summary_df["purified_alternate_title"] = summary_df["alternate_title"].apply(purify)
    merged_df["novel_link"] = ""
    for i, row in merged_df.iterrows():
        title = purify(row["title"])
        if summary_df["purified_title"].str.match(title).any():
            matched_row = summary_df[summary_df["purified_title"].str.match(title)]
            merged_df.at[i, "novel_link"] = matched_row["novel_link"].values[0]
            if len(matched_row) > 1:
                print(f"{len(matched_row)} matches found for title: {title}")
                for k, v in matched_row.iterrows():
                    print(f"row {k}: {v['purified_title']} - {v['novel_link']}")
        elif summary_df["purified_alternate_title"].str.match(title).any():
            matched_row = summary_df[summary_df["purified_alternate_title"].str.match(title)]
            merged_df.at[i, "novel_link"] = matched_row["novel_link"].values[0]
            merged_df.at[i, "title"] = f"{matched_row['main_title'].values[0]}({matched_row['alternate_title'].values[0]})"
            # print(f"alternate_title match: {matched_row['main_title'].values[0]} - {matched_row['alternate_title'].values[0]}")

    merged_df.to_csv(CSV_OUTPUT, index=False, encoding="utf-8-sig")

def create_html_table(data):
    rows_html = []
    for _, row in data.iterrows():
        title = row["title"]
        author = row["author"]
        date = row["date"]
        date = "" if pd.isna(date) else date
        download_url = row["download_url"]
        ghproxy_url = f"https://gh-proxy.com/{download_url}"
        if title[-1] == ')':
            last_bracket = title.rfind('(')
            alternate_title = title[last_bracket+1:-1]
            title = title[:last_bracket]
        else:
            alternate_title = ""
        novel_link = row["novel_link"]
        novel_link = novel_link if not pd.isna(novel_link) else ""

        title_html = f'<a href="{novel_link}" target="_blank">{title}</a>' if novel_link else title
    
        row_html = f"""
        <tr>
            <td class="novel-title">{title_html}{alternate_title and "<span class='alternate-title'>" + alternate_title + "</span>"}</td>
            <td class="dl"><a href="{download_url}" target="_blank">下载</a> <a href="{ghproxy_url}" target="_blank">镜像</a></td>
            <td>{author}</td>
            <td>{date}</td>
        </tr>
        """

        rows_html.append(row_html)
    return "\n".join(rows_html)

def generate_html_file(data):
    table_content = create_html_table(data)

    # today = time.strftime("%Y-%m-%d", time.localtime())
    this_year = time.strftime("%Y", time.localtime())

    html_template = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="keywords" content="轻小说,sf轻小说,dmzj轻小说,日本轻小说,动漫小说,轻小说电子书,轻小说EPUB下载">
    <meta name="description" content="轻小说文库 EPUB 下载，支持搜索关键字、跳转至源站和蓝奏云下载，已进行移动端适配。">
    <meta name="author" content="mojimoon">
    <title>轻小说文库 EPUB 下载 (TXT 源)</title>
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <h1>轻小说文库 EPUB 下载 (TXT 源)</h1>
    <h3>By <a href="https://github.com/mojimoon">mojimoon</a> | <a href="https://github.com/mojimoon/wenku8">Star me</a> | {this_year}年 | {len(data)}部</h3>
    <span>所有内容均收集于网络、仅供学习交流使用，本站仅作整理工作。特别感谢 <a href="https://github.com/ixinzhi">布客新知</a> 整理。</span>

    <div class="right-controls">
        <a href="./index.html"><button class="btn" id="gotoButton">切换到 EPUB 源 (最新最快)</button></a>
        <button class="btn" id="themeToggle">主题</button>
        <button class="btn" id="clearInput">清除</button>
    </div>
    <div class="search-bar">
        <input type="text" id="searchInput" placeholder="搜索">
        <button class="btn" id="randomButton">随机</button>
    </div>

    <table>
        <thead>
            <tr>
                <th>标题</th>
                <th>下载</th>
                <th>作者</th>
                <th>更新</th>
            </tr>
        </thead>
        <tbody id="novelTableBody">
            {table_content}
        </tbody>
    </table>

    <script src="script_txt.js"></script>
</body>
</html>
"""
    with open(HTML_OUTPUT, "w", encoding="utf-8") as f:
        f.write(html_template)

def html():
    with open(CSV_OUTPUT, "r", encoding="utf-8-sig") as f:
        data = pd.read_csv(f)
    generate_html_file(data)

if __name__ == "__main__":
    # scrape_all()
    # merge_csv_files()
    html()
