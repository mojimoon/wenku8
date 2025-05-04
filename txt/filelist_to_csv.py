import os
import pandas as pd

# dir_name = 'lightnovel-2009to2013'
dir_name = 'lightnovel-2018to2020'

download_url_base = 'https://raw.githubusercontent.com/ixinzhi/' + dir_name + '/master/'

_list = []

for file in os.listdir(dir_name):
    if file.endswith('.epub'):
        _list.append(file)

df = pd.DataFrame(_list, columns=['name'])

parts_df = df["name"].str.split(" - ", expand=True)
df["title"] = parts_df.iloc[:, :-2].apply(lambda x: " - ".join(x), axis=1)
df["author"] = parts_df.iloc[:, -2]
df["date"] = parts_df.iloc[:, -1].apply(lambda x: x.split(".")[0])
df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
df["date"] = df["date"].dt.strftime("%Y-%m-%d")
df["download_url"] = download_url_base + df["name"]

df = df[["title", "author", "date", "download_url"]]

df.to_csv(dir_name + '.csv', index=False, encoding="utf-8-sig")