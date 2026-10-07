"""
一次性迁移：把本仓库（mojimoon/wenku8）Release 中的重制版 EPUB 迁到存放仓库（EPUB_REPO），更新 out/epub_index.json，
全部核对无误后（--delete）删除本仓库的所有 Release 及其 tag。由 .github/workflows/migrate_releases.yml 运行。

- 按索引迁移：每本书的每个版本下载原附件、上传到存放仓库的 book-{aid}（文件名不变，显示名为新格式），
  核对文件名与大小一致后才改写索引；每本书保存一次，可中断后重跑（已迁移的跳过，已上传的附件不重复上传）
- 已在存放仓库的版本只刷新附件显示名
- 删除前确认索引中没有任何版本仍指向本仓库，否则拒绝删除
"""

import argparse
import json
import os
import subprocess
import tempfile

from build_batch import (REPO, STORE, asset_label, ensure_release, gh_store, load_index, release_tag, save_index,
                         update_notes)


def slots(entry: dict):
    yield '', entry
    yield from entry.get('variants', {}).items()


def asset_list(repo: str, tag: str) -> dict:
    """{附件名: (id, 大小)}"""
    cmd = ['release', 'view', tag, '--json', 'assets']
    if repo == STORE:
        out = gh_store(*cmd, capture_output=True, text=True, encoding='utf-8').stdout
    else:
        out = subprocess.run(['gh', *cmd, '--repo', repo], capture_output=True, text=True, encoding='utf-8',
                             check=True).stdout
    return {a['name']: (a.get('id'), a['size']) for a in json.loads(out)['assets']}


def store_api(*args) -> str:
    """用 EPUB_TOKEN 调用 GitHub API（gh api 不接受 --repo，不能用 gh_store）。"""
    env = {**os.environ, 'GH_TOKEN': os.environ['EPUB_TOKEN']}
    return subprocess.run(['gh', 'api', *args], env=env, capture_output=True, text=True, encoding='utf-8',
                          check=True).stdout


def relabel(tag: str, aid: int, entry: dict, slot: dict, variant: str):
    """刷新存放仓库中已有附件的显示名。"""
    ids = {a['name']: a['id'] for a in json.loads(store_api(f'repos/{STORE}/releases/tags/{tag}'))['assets']}
    for v in slot['volumes']:
        name = f'{aid}-{v["file"]}'
        if name in ids:
            label = asset_label(aid, v, entry.get('title', ''), entry.get('author', ''), variant)
            store_api('-X', 'PATCH', f'repos/{STORE}/releases/assets/{ids[name]}', '-f', f'label={label}')


def migrate(index: dict) -> int:
    failed = 0
    for aid in sorted(index, key=int):
        entry = index[aid]
        for variant, slot in slots(entry):
            if not slot.get('volumes'):
                continue
            new = release_tag(int(aid))
            if slot.get('repo', REPO) == STORE:
                relabel(slot['tag'], int(aid), entry, slot, variant)
                continue
            old = slot['tag']
            print(f'[migrate] aid={aid} {variant or "1000"}: {REPO}@{old} -> {STORE}@{new}（{len(slot["volumes"])} 卷）')
            ensure_release(new, f'{entry.get("title", aid)} (aid {aid})')
            src, have = asset_list(REPO, old), asset_list(STORE, new)
            with tempfile.TemporaryDirectory() as d:
                files = []
                for v in slot['volumes']:
                    name = f'{aid}-{v["file"]}'
                    if name in have and have[name][1] == src.get(name, (0, -1))[1]:
                        continue
                    subprocess.run(['gh', 'release', 'download', old, '--repo', REPO, '-p', name, '-D', d, '--clobber'],
                                   check=True)
                    label = asset_label(int(aid), v, entry.get('title', ''), entry.get('author', ''), variant)
                    files.append(f'{os.path.join(d, name)}#{label}')
                if files:
                    gh_store('release', 'upload', new, *files, '--clobber')
            have = asset_list(STORE, new)
            bad = [v['file'] for v in slot['volumes']
                   if have.get(f'{aid}-{v["file"]}', (0, -1))[1] != src.get(f'{aid}-{v["file"]}', (0, -2))[1]]
            if bad:
                print(f'    [ERROR] 核对失败，保留原记录: {bad}')
                failed += 1
                continue
            slot['tag'], slot['repo'] = new, STORE
            save_index(index)
            update_notes(new, index)
    return failed


def delete_all(index: dict):
    left = [(aid, variant or '1000') for aid, e in index.items() for variant, s in slots(e)
            if s.get('volumes') and s.get('repo', REPO) != STORE]
    if left:
        raise SystemExit(f'仍有 {len(left)} 个版本指向本仓库，拒绝删除: {left[:10]}')
    tags = json.loads(subprocess.run(['gh', 'release', 'list', '--repo', REPO, '--limit', '1000', '--json', 'tagName'],
                                     capture_output=True, text=True, encoding='utf-8', check=True).stdout)
    for t in tags:
        print(f'[delete] {REPO}@{t["tagName"]}')
        subprocess.run(['gh', 'release', 'delete', t['tagName'], '--repo', REPO, '--yes', '--cleanup-tag'], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--delete', action='store_true', help='迁移全部成功后删除本仓库的所有 Release')
    args = ap.parse_args()
    if STORE == REPO:
        raise SystemExit('未设置 EPUB_TOKEN，无法写入存放仓库')
    index = load_index()
    failed = migrate(index)
    save_index(index)
    print(f'[migrate] 完成，失败 {failed} 个版本')
    if args.delete:
        if failed:
            raise SystemExit('有迁移失败的版本，跳过删除')
        delete_all(index)


if __name__ == '__main__':
    main()
