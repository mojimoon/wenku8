"""
把重制版 EPUB（默认 1000px 版本）从 Release 归档到存放仓库本身（EPUB_REPO 的默认分支），目录结构：

    {年份}/{aid}-{书名}-{作者}/{书名} {卷序号:02} - {卷名} - {作者}.epub

年份取 wenku8 目录的最后更新日期（没有时用生成时记录的 src_update）。Release 保留不动，页面仍从 Release 下载。

无状态、可重复运行：每次对比仓库现有文件（路径 + 大小）与 out/epub_index.json，只补缺失 / 大小变化的卷，
并删除同一 aid 下已过期的文件（重新生成后卷变少、书名或年份变化导致目录改名）。
为避免把 10GB+ 的仓库拉到本地：每批用 blob:none + sparse 的浅克隆，只写入本批文件，提交推送后整个删掉；
每批不超过 --batch-mb（GitHub 单次推送上限 2GB）。

    python archive.py --dry-run                 # 只打印要做什么
    python archive.py --time-budget 300         # 在 Actions 中（EPUB_TOKEN 需对存放仓库有 contents 写权限）
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

EPUB_REPO = os.environ.get('EPUB_REPO', 'mojimoon/wenku8-epub')
INDEX_FILE = os.path.join('out', 'epub_index.json')
CATALOG_FILE = os.path.join('out', 'wenku_catalog.json')


def safe(name: str, limit: int = 60) -> str:
    """路径段：去掉文件系统不允许的字符，限制长度（Linux 单段 255 字节，中文 3 字节/字）。"""
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).strip().rstrip('.')
    return s[:limit].strip() or '_'


def vol_name(idx: int, title: str, vol: str, author: str) -> str:
    """书名 卷序号 - 卷名 - 作者.epub（如 文学少女 01 - 第一卷 渴望死亡的小丑 - 野村美月.epub）。
    整个文件名限制在 240 字节内（Linux 单段 255 字节），超出时截短卷名。"""
    head, a = f'{safe(title, 40)} {idx:02d}'.strip(), safe(author, 20)
    room = 240 - len(f'{head} -  - {a}.epub'.encode())
    v = safe(vol, 80).encode()[:max(room, 0)].decode('utf-8', 'ignore').strip()
    return ' - '.join(x for x in (head, v, a) if x) + '.epub'


def plan_files(index: dict, catalog: dict) -> dict[str, tuple[int, int, str]]:
    """{仓库内路径: (aid, 大小, Release 下载地址)}，只含默认版本。"""
    years = {it['aid']: it.get('update', '')[:4] for p in catalog['pages'].values() for it in p}
    files = {}
    for aid, e in index.items():
        if not e.get('volumes') or not e.get('tag'):
            continue
        year = years.get(int(aid)) or (e.get('src_update') or e.get('built_at', ''))[:4] or 'unknown'
        d = f'{year}/{safe(f"{aid}-{e.get("title", "")}-{e.get("author", "")}", 80)}'
        repo = e.get('repo', 'mojimoon/wenku8')
        for v in e['volumes']:
            path = f'{d}/{vol_name(v["index"], e.get("title", ""), v["title"], e.get("author", ""))}'
            url = f'https://github.com/{repo}/releases/download/{e["tag"]}/{aid}-{v["file"]}'
            files[path] = (int(aid), v['size'], url)
    return files


def git(*args, cwd=None, capture=False) -> str:
    r = subprocess.run(['git', *args], cwd=cwd, check=True, text=True, encoding='utf-8',
                       stdout=subprocess.PIPE if capture else None)
    return r.stdout if capture else ''


def clone(url: str, d: str):
    """浅、无 blob、空 sparse 的克隆：只有目录树，不下载任何文件内容。"""
    git('clone', '--quiet', '--depth', '1', '--filter=blob:none', '--sparse', url, d)
    git('sparse-checkout', 'set', '--no-cone', '/README.md', cwd=d)
    git('config', 'core.quotepath', 'false', cwd=d)


def remote_files(d: str) -> dict[str, int]:
    """{路径: 大小}（ls-tree -l 读树对象里的大小，不需要 blob）。"""
    out = git('ls-tree', '-r', '-l', '-z', 'HEAD', cwd=d, capture=True)
    files = {}
    for rec in out.split('\0'):
        if rec:
            meta, path = rec.split('\t', 1)
            files[path] = int(meta.split()[3]) if meta.split()[3].isdigit() else -1
    return files


def diff(want: dict, have: dict) -> tuple[list[str], list[str]]:
    """(需要上传的路径, 需要删除的路径)。删除只针对 {年份}/{aid}-... 目录下、本次不再需要的文件。
    ponytail: 只按大小判断是否变化，重新生成后各卷大小都不变的情况视为未变。"""
    add = sorted(p for p, (_, size, _) in want.items() if have.get(p) != size)
    aids = {a for a, _, _ in want.values()}
    rm = sorted(p for p in have if p not in want and re.match(r'[^/]+/(\d+)-', p)
                and int(re.match(r'[^/]+/(\d+)-', p).group(1)) in aids)
    return add, rm


def download(url: str, dst: str, size: int):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=120) as r, open(dst, 'wb') as f:
                shutil.copyfileobj(r, f)
            if os.path.getsize(dst) == size:
                return
            err = f'大小不符 {os.path.getsize(dst)} != {size}'
        except OSError as e:
            err = str(e)
        print(f'[archive] 下载失败（第 {attempt + 1} 次）{url}: {err}')
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f'下载失败: {url}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--batch-mb', type=int, default=1000, help='每次推送的大小上限（MB）')
    ap.add_argument('--time-budget', type=float, default=0, help='分钟；超时后不再开始新的一批')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    token = os.environ.get('EPUB_TOKEN')
    url = f'https://x-access-token:{token}@github.com/{EPUB_REPO}.git' if token else f'https://github.com/{EPUB_REPO}.git'
    with open(INDEX_FILE, encoding='utf-8') as f:
        index = json.load(f)
    with open(CATALOG_FILE, encoding='utf-8') as f:
        catalog = json.load(f)
    want = plan_files(index, catalog)
    start, batch_no = time.time(), 0

    while True:
        with tempfile.TemporaryDirectory() as d:
            clone(url, d)
            add, rm = diff(want, remote_files(d))
            total = sum(want[p][1] for p in add)
            print(f'[archive] 需上传 {len(add)} 个文件（{total / 1e9:.2f} GB），删除 {len(rm)} 个；共 {len(want)} 个')
            if args.dry_run:
                for p in (add[:20] + rm[:20]):
                    print('   ', p)
                return
            if not add and not rm:
                return
            if args.time_budget and (time.time() - start) / 60 > args.time_budget:
                print('[archive] 达到时间预算，剩余的下次继续')
                return
            # 一批：按路径顺序（即按年份、aid）取到大小上限为止，至少一个文件
            batch, size = [], 0
            for p in add:
                if batch and size + want[p][1] > args.batch_mb * 1e6:
                    break
                batch.append(p)
                size += want[p][1]
            with ThreadPoolExecutor(8) as ex:
                list(ex.map(lambda p: download(want[p][2], os.path.join(d, p), want[p][1]), batch))
            if rm:
                git('rm', '--quiet', '--cached', '--sparse', '--', *rm, cwd=d)
            for i in range(0, len(batch), 200):   # 避免命令行过长
                git('add', '--sparse', '--', *batch[i:i + 200], cwd=d)
            batch_no += 1
            first, last = batch[0].split('/')[1].split('-')[0], batch[-1].split('/')[1].split('-')[0]
            git('-c', 'user.name=mojimoon', '-c', 'user.email=109803649+mojimoon@users.noreply.github.com',
                'commit', '--quiet', '-m', f'archive: {len(batch)} volumes (aid {first}..{last}), remove {len(rm)}', cwd=d)
            for attempt in range(3):
                try:
                    git('push', '--quiet', 'origin', 'HEAD', cwd=d)
                    break
                except subprocess.CalledProcessError:
                    if attempt == 2:
                        raise
                    time.sleep(30)
            print(f'[archive] 第 {batch_no} 批已推送：上传 {len(batch)} 个文件（{size / 1e6:.0f} MB），删除 {len(rm)} 个')


if __name__ == '__main__':
    main()
