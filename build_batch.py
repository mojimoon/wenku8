"""
批量为「仅 TXT 源」的小说生成分卷 EPUB，并上传到 GitHub Release（供网页下载）。

- 目标来自 out/txt_meta.csv（见 fill_meta.py），状态记录在 out/epub_index.json
- 仅当 aid 尚未生成、或其 TXT 源有更新（文件名中的日期变化）时才（重新）生成
- 每本生成后立即上传并保存状态，因此中断/超时后重跑即可继续

用法:
    python build_batch.py --max-novels 50 --time-budget 300   # 在 Actions 中
    python build_batch.py --aid 129 --no-upload                # 本地测试，不上传
"""

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import time

import pandas as pd

from utils import Fetcher, LoginExpired
from gen_epub import EPUB_OUT_DIR, build_novel

META_CSV = os.path.join('out', 'txt_meta.csv')
INDEX_FILE = os.path.join('out', 'epub_index.json')
TAG_SIZE = 200   # 每个 Release 容纳的 aid 范围（Release 最多 1000 个附件）
MAX_FAILS = 3


def release_tag(aid: int) -> str:
    return f'epub-{aid // TAG_SIZE:02d}'


def txt_version(url: str) -> str:
    m = re.search(r'(\d{8})\.epub$', url)
    return m.group(1) if m else url


def load_index() -> dict:
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_index(index: dict):
    """每个 aid 一行，便于 git 合并并发提交（批量生成与按需生成）。"""
    tmp = INDEX_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        rows = [f'{json.dumps(k)}:{json.dumps(index[k], ensure_ascii=False, separators=(",", ":"), sort_keys=True)}'
                for k in sorted(index, key=int)]
        f.write('{\n' + ',\n'.join(rows) + '\n}\n')
    os.replace(tmp, INDEX_FILE)


def pick_targets(index: dict) -> list[tuple[int, str]]:
    """[(aid, txt_version)]：每个 aid 取最新的 TXT 版本。"""
    df = pd.read_csv(META_CSV, encoding='utf-8-sig', dtype=str)
    latest = {}
    for r in df.itertuples():
        v = txt_version(r.download_url)
        aid = int(r.aid)
        if aid not in latest or v > latest[aid]:
            latest[aid] = v
    targets = []
    for aid, v in sorted(latest.items()):
        entry = index.get(str(aid))
        if entry and entry.get('txt') == v and 'volumes' in entry:
            continue
        if entry and entry.get('fail_txt') == v and entry.get('fails', 0) >= MAX_FAILS:
            continue
        targets.append((aid, v))
    return targets


def ensure_release(tag: str):
    if subprocess.run(['gh', 'release', 'view', tag], capture_output=True).returncode != 0:
        subprocess.run(['gh', 'release', 'create', tag, '--title', tag, '--notes', 'EPUB cache (auto-generated)'],
                       check=True)


def upload(aid: int, info: dict) -> str:
    tag = release_tag(aid)
    ensure_release(tag)
    src = os.path.join(EPUB_OUT_DIR, str(aid))
    files = []
    for v in info['volumes']:
        dst = os.path.join(src, f'{aid}-{v["file"]}')   # 附件名: {aid}-v01.epub
        shutil.copyfile(os.path.join(src, v['file']), dst)
        files.append(dst)
    subprocess.run(['gh', 'release', 'upload', tag, *files, '--clobber'], check=True)
    return tag


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--aid', type=int, nargs='*', help='只处理指定 aid（忽略状态判断）')
    ap.add_argument('--max-novels', type=int, default=0)
    ap.add_argument('--time-budget', type=float, default=0, help='分钟；超时后不再开始新的小说')
    ap.add_argument('--scraper', default='auto')
    ap.add_argument('--no-upload', action='store_true')
    args = ap.parse_args()

    index = load_index()
    if args.aid:
        versions = {}
        for r in pd.read_csv(META_CSV, encoding='utf-8-sig', dtype=str).itertuples():
            versions[int(r.aid)] = max(versions.get(int(r.aid), ''), txt_version(r.download_url))
        targets = [(a, versions.get(a, '')) for a in args.aid]
    else:
        targets = pick_targets(index)
    if args.max_novels:
        targets = targets[:args.max_novels]
    print(f'[batch] 待处理 {len(targets)} 本（已有 {sum("volumes" in e for e in index.values())} 本）')

    start = time.time()
    fetcher = Fetcher(args.scraper)
    done = 0
    try:
        for aid, ver in targets:
            if args.time_budget and (time.time() - start) / 60 > args.time_budget:
                print('[batch] 达到时间预算，停止')
                break
            print(f'\n[batch] aid={aid} txt={ver}')
            try:
                info = build_novel(fetcher, aid, split=True)
                entry = {'txt': ver, 'title': info['title'], 'author': info['author'],
                         'built_at': info['built_at'], 'src_update': info['last_update'],
                         'volumes': info['volumes']}
                if not args.no_upload:
                    entry['tag'] = upload(aid, info)
                index[str(aid)] = entry
                done += 1
            except LoginExpired:
                raise
            except Exception as e:
                import traceback
                traceback.print_exc()
                prev = index.get(str(aid), {})
                fails = prev.get('fails', 0) + 1 if prev.get('fail_txt') == ver else 1
                index[str(aid)] = {**prev, 'fails': fails, 'fail_txt': ver, 'error': str(e)[:200]}
            finally:
                if not args.no_upload:
                    shutil.rmtree(os.path.join(EPUB_OUT_DIR, str(aid)), ignore_errors=True)
                shutil.rmtree(os.path.join('out', 'cache', str(aid)), ignore_errors=True)
                save_index(index)
    except KeyboardInterrupt:
        print('\n[batch] 已中断，进度已保存')
    finally:
        fetcher.close()
    print(f'\n[batch] 本次完成 {done} 本，用时 {(time.time() - start) / 60:.1f} 分钟')


if __name__ == '__main__':
    main()
