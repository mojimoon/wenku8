import requests
import os
import re
import time
import pandas as pd

API_URL = "https://api.github.com/repos/ixinzhi/{repo}/contents/"
REPOS = [
    "lightnovel-2014to2017",
    "lightnovel-2021",
    "lightnovel-2022",
    "lightnovel-2023",
    "lightnovel-2024",
    "lightnovel-2025"
]
ALL_REPOS = [
    "lightnovel-2009to2013",
    "lightnovel-2014to2017",
    "lightnovel-2018to2020",
    "lightnovel-2021",
    "lightnovel-2022",
    "lightnovel-2023",
    "lightnovel-2024",
    "lightnovel-2025"
]
HEADERS = {
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "python-requests/2.25.1",
}

TXT_DIR = 'txt'
TXT_LIST_FILE = os.path.join('out', 'txt_list.csv')

def scrape_repo(repo):
    url = API_URL.format(repo=repo)
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()

    data = resp.json()
    df = pd.DataFrame(data)
    df = df[["name", "download_url"]]

    parts_df = df["name"].str.split(" - ", expand=True)
    df["title"] = parts_df.iloc[:, :-2].apply(lambda x: " - ".join(x), axis=1)
    df["author"] = parts_df.iloc[:, -2]
    df["date"] = parts_df.iloc[:, -1].apply(lambda x: x.split(".")[0])
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")

    df = df[["title", "author", "date", "download_url"]]

    df.to_csv(os.path.join(TXT_DIR, f"{repo}.csv"), index=False, encoding="utf-8-sig")

def incremental_scrape():
    if not os.path.exists(TXT_DIR):
        os.makedirs(TXT_DIR)
    for repo in REPOS:
        csv_path = os.path.join(TXT_DIR, f"{repo}.csv")
        if not os.path.exists(csv_path):
            print(f"[INFO] Scraping repo: {repo}")
            scrape_repo(repo)
            time.sleep(1)

def merge_csv():
    all_dfs = []
    for repo in ALL_REPOS:
        df = pd.read_csv(os.path.join(TXT_DIR, f"{repo}.csv"))
        all_dfs.append(df)
    merged_df = pd.concat(all_dfs, ignore_index=True)
    merged_df = merged_df.drop_duplicates(subset=["title", "author"], keep="last")
    # TODO: additional duplicate check: if A.title contains B.title and A.author == B.author, remove B
    # however this is uncommon and inefficient to implement, so we will not do it for now
    merged_df = merged_df.sort_values(by=["date"], ascending=False)
    merged_df.to_csv(TXT_LIST_FILE, index=False, encoding="utf-8-sig")

def main():
    if not os.path.exists('out'):
        os.mkdir('out')
    if not os.path.exists(TXT_DIR):
        os.mkdir(TXT_DIR)
    
    incremental_scrape()
    merge_csv()

if __name__ == '__main__':
    main()