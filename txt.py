import requests
import json
import time
import pandas as pd
import os

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

def merge_csv_files():
    all_data = []
    for repo in REPOS:
        df = pd.read_csv(os.path.join(OUTPUT_DIR, f"{repo}.csv"), encoding="utf-8-sig")
        all_data.append(df)

    merged_df = pd.concat(all_data, ignore_index=True)
    print(f"Total rows before deduplication: {len(merged_df)}")
    merged_df = merged_df.sort_values(by=["title", "author", "date"], ascending=[True, True, False])
    merged_df = merged_df.drop_duplicates(subset=["title", "author"], keep="first")
    print(f"Total rows after deduplication: {len(merged_df)}")
    merged_df = merged_df.sort_values(by=["date"], ascending=False)
    merged_df.to_csv(CSV_OUTPUT, index=False, encoding="utf-8-sig")

if __name__ == "__main__":
    # scrape_all()
    merge_csv_files()
