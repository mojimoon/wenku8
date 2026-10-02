"""
批量为「仅 TXT 源」的小说生成分卷 EPUB，并上传到 GitHub Release（供网页下载）。

- 目标来自 out/merged.csv（有 aid、有 TXT 源、没有蓝奏源的条目，见 merge.py），状态记录在 out/epub_index.json
- 仅当 aid 尚未生成、只生成了部分卷、或其 TXT 源有更新（文件名中的日期变化）时才（重新）生成
- 每本生成后立即上传并保存状态，因此中断/超时后重跑即可继续
- wenku8 因版权下架的小说记为 {"blocked": "copyright"}，不再尝试

索引条目（每个 aid 一行）:
    {txt, title, author, built_at, src_update, tag, total, volumes: [{index, title, file, size, ...}],
     variants: {"1400"|"1600"|"orig": {txt, tag, total, built_at, volumes}}}   # 其他分辨率的缓存（按需生成）
Release 附件名为 {aid}-{file}：默认 1000px 在 epub-NN（aid//200），其他缓存分辨率在 epub-var-NN。

用法:
    python build_batch.py --max-novels 50 --time-budget 300   # 在 Actions 中
    python build_batch.py --aid 129 --no-upload                # 本地测试，不上传
    python build_batch.py --check-blocked --time-budget 60     # 只检查详情页，标记版权下架的小说
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
from gen_epub import EPUB_OUT_DIR, CopyrightBlocked, book_url, build_novel, parse_detail

MERGED_CSV = os.path.join('out', 'merged.csv')
INDEX_FILE = os.path.join('out', 'epub_index.json')
REPO = os.environ.get('GITHUB_REPOSITORY', 'mojimoon/wenku8')
TAG_SIZE = 200   # 每个 Release 容纳的 aid 范围（Release 最多 1000 个附件）
MAX_FAILS = 3


def release_tag(aid: int, variant: str = '') -> str:
    return f'epub-{"var-" if variant else ""}{aid // TAG_SIZE:02d}'


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


def txt_only_targets() -> dict[int, tuple[str, str]]:
    """需要生成重制版的小说：有 TXT 源、没有蓝奏 EPUB、且已对应到 aid。返回 {aid: (TXT 版本, 书名)}。"""
    df = pd.read_csv(MERGED_CSV, encoding='utf-8-sig', dtype=str).fillna('')
    df = df[(df['novel_link'] != '') & (df['download_url'] != '') & (df['dl_label'] == '')]
    return {int(re.search(r'/book/(\d+)', r.novel_link).group(1)): (txt_version(r.download_url), r.main)
            for r in df.itertuples()}


def is_complete(slot: dict, version: str) -> bool:
    vols = slot.get('volumes')
    return bool(vols) and slot.get('txt') == version and len(vols) >= slot.get('total', len(vols))


def pick_targets(index: dict) -> list[tuple[int, str]]:
    """[(aid, txt_version)]：尚未生成、只生成了部分卷、或 TXT 版本有更新的。"""
    targets = []
    for aid, (v, _) in sorted(txt_only_targets().items()):
        entry = index.get(str(aid), {})
        if entry.get('blocked') or is_complete(entry, v):
            continue
        if entry.get('fail_txt') == v and entry.get('fails', 0) >= MAX_FAILS:
            continue
        targets.append((aid, v))
    return targets


# ─── Release ──────────────────────────────────────────

def ensure_release(tag: str):
    if subprocess.run(['gh', 'release', 'view', tag], capture_output=True).returncode != 0:
        subprocess.run(['gh', 'release', 'create', tag, '--title', tag, '--notes', 'EPUB cache (auto-generated)'],
                       check=True)


def upload(aid: int, info: dict, variant: str = '') -> str:
    """上传 info['volumes'] 的分卷文件，附件名 {aid}-{file}，显示名带书名与 aid。返回 Release tag。"""
    tag = release_tag(aid, variant)
    ensure_release(tag)
    src = os.path.join(EPUB_OUT_DIR, str(aid))
    files = []
    for v in info['volumes']:
        dst = os.path.join(src, f'{aid}-{v["file"]}')
        shutil.copyfile(os.path.join(src, v['local']), dst)
        label = f'{info["title"]} {v["title"]}{f" [{variant}]" if variant else ""} (aid {aid})'.replace('#', '＃')
        files.append(f'{dst}#{label}')
    subprocess.run(['gh', 'release', 'upload', tag, *files, '--clobber'], check=True)
    return tag


def update_notes(tag: str, index: dict):
    """Release 说明：列出该 Release 中每本小说的 aid、书名、作者、卷数与生成时间。"""
    rows = []
    for aid in sorted(index, key=int):
        e = index[aid]
        slots = [('1000', e)] + list(e.get('variants', {}).items())
        for variant, s in slots:
            if s.get('tag') == tag and s.get('volumes'):
                rows.append(f'| [{aid}](https://www.wenku8.net/book/{aid}.htm) | {e.get("title", "")} | '
                            f'{e.get("author", "")} | {variant} | {len(s["volumes"])}/{s.get("total", len(s["volumes"]))} '
                            f'| {s.get("built_at", "")[:10]} |')
    kind = '其他分辨率（按需生成的缓存）' if '-var-' in tag else '插图长边 1000px（默认）'
    notes = '\n'.join([f'wenku8 重制版分卷 EPUB，{kind}。附件名为 `{{aid}}-[分辨率-]v{{卷序号}}.epub`。', '',
                       '| aid | 书名 | 作者 | 分辨率 | 卷数 | 生成时间 |', '|---|---|---|---|---|---|', *rows])
    subprocess.run(['gh', 'release', 'edit', tag, '--notes', notes[:120000]], check=True)


def merge_slot(slot: dict, info: dict, version: str, tag: str) -> dict:
    """把新生成的卷并入缓存条目（同一 TXT 版本下按卷累积，版本变化则重来）。"""
    have = {v['index']: v for v in slot.get('volumes', [])} if slot.get('txt') == version else {}
    for v in info['volumes']:
        have[v['index']] = {k: v[k] for k in ('index', 'title', 'file', 'size', 'chapters', 'images')}
    return {'txt': version, 'tag': tag, 'total': info['total_volumes'], 'built_at': info['built_at'],
            'volumes': [have[k] for k in sorted(have)]}


def name_files(info: dict, variant: str = ''):
    """本地文件 vNN.epub -> 索引中的 file 名（默认 vNN.epub；其他分辨率 {variant}-vNN.epub）。"""
    for v in info['volumes']:
        v['local'] = v['file']
        v['file'] = f'{variant}-{v["file"]}' if variant else v['file']


def record_default(index: dict, aid: int, info: dict, version: str, tag: str):
    entry = index.get(str(aid), {})
    slot = merge_slot(entry, info, version, tag)
    keep = {k: entry[k] for k in ('variants',) if k in entry}
    index[str(aid)] = {**keep, **slot, 'title': info['title'], 'author': info['author'],
                       'src_update': info['last_update']}


def record_blocked(index: dict, aid: int, title: str):
    index[str(aid)] = {'blocked': 'copyright', 'title': title, 'checked': datetime.date.today().isoformat()}


# ─── 主流程 ──────────────────────────────────────────

def check_blocked(fetcher: Fetcher, index: dict, budget: float):
    """只抓详情页，把版权下架的小说记入索引（页面据此隐藏“请求生成”）。"""
    start, found = time.time(), 0
    todo = [a for a in sorted(txt_only_targets()) if str(a) not in index]
    print(f'[check] 待检查 {len(todo)} 本')
    for i, aid in enumerate(todo, 1):
        if budget and (time.time() - start) / 60 > budget:
            print('[check] 达到时间预算，停止')
            break
        try:
            d = parse_detail(fetcher.get(book_url(aid), lambda h: 'id="content"' in h))
        except LoginExpired:
            raise
        except Exception as e:
            print(f'[check] aid={aid} 失败: {e}')
            continue
        if d.get('blocked'):
            record_blocked(index, aid, d.get('title', ''))
            found += 1
            print(f'[check] aid={aid} {d.get("title", "")} 版权下架')
            save_index(index)
        if i % 100 == 0:
            print(f'[check] {i}/{len(todo)}，下架 {found}')
    print(f'[check] 完成，新发现下架 {found} 本')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--aid', type=int, nargs='*', help='只处理指定 aid（忽略状态判断）')
    ap.add_argument('--max-novels', type=int, default=0)
    ap.add_argument('--time-budget', type=float, default=0, help='分钟；超时后不再开始新的小说')
    ap.add_argument('--scraper', default='auto')
    ap.add_argument('--no-upload', action='store_true')
    ap.add_argument('--check-blocked', action='store_true', help='只检查并标记版权下架的小说')
    args = ap.parse_args()

    index = load_index()
    fetcher = Fetcher(args.scraper)
    if args.check_blocked:
        try:
            check_blocked(fetcher, index, args.time_budget)
        finally:
            save_index(index)
            fetcher.close()
        return

    known = txt_only_targets()
    if args.aid:
        targets = [(a, known.get(a, ('', ''))[0]) for a in args.aid]
    else:
        targets = pick_targets(index)
    if args.max_novels:
        targets = targets[:args.max_novels]
    print(f'[batch] 待处理 {len(targets)} 本（已有 {sum("volumes" in e for e in index.values())} 本）')

    start = time.time()
    done, touched = 0, set()
    try:
        for aid, ver in targets:
            if args.time_budget and (time.time() - start) / 60 > args.time_budget:
                print('[batch] 达到时间预算，停止')
                break
            print(f'\n[batch] aid={aid} txt={ver}')
            entry = index.get(str(aid), {})
            # 只生成了部分卷（按需请求）且 TXT 未变：只补缺失的卷
            missing = None
            if entry.get('txt') == ver and entry.get('volumes') and entry.get('total'):
                missing = set(range(1, entry['total'] + 1)) - {v['index'] for v in entry['volumes']} or None
            try:
                info = build_novel(fetcher, aid, split=True, only_volumes=missing)
                name_files(info)
                tag = '' if args.no_upload else upload(aid, info)
                record_default(index, aid, info, ver, tag)
                touched.add(tag)
                done += 1
            except LoginExpired:
                raise
            except CopyrightBlocked as e:
                print(f'    {e}')
                record_blocked(index, aid, known.get(aid, ('', ''))[1])
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
        for tag in touched - {''}:
            update_notes(tag, index)
    print(f'\n[batch] 本次完成 {done} 本，用时 {(time.time() - start) / 60:.1f} 分钟')


if __name__ == '__main__':
    main()
