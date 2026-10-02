"""
按需生成：处理页面生成的 GitHub Issue（标题以 "[build]" 开头），生成分卷 EPUB 并输出回复内容。

Issue 正文为 "key: value" 行（由页面预填，也可手写）:
    aid: 129
    volumes: all            # 或 1,3
    max_side: 1400          # 插图长边像素，0=原图
    images: yes             # yes / no

输入来自环境变量 ISSUE_BODY（不可信内容，仅做严格解析），输出:
    out/request_comment.md  回复到 Issue 的 Markdown（含下载链接）
默认参数（1400 / 含图 / 全卷）的结果记入 out/epub_index.json，与批量生成的结果一致；
其他参数的结果上传到 Release epub-custom，不入索引。
"""

import os
import re
import sys

from build_batch import load_index, save_index, txt_only_targets, upload
from gen_epub import MAX_SIDE, build_novel
from utils import Fetcher

GH_PROXY = 'https://gh-proxy.org/'
REPO = os.environ.get('GITHUB_REPOSITORY', 'mojimoon/wenku8')
COMMENT_FILE = os.path.join('out', 'request_comment.md')
CUSTOM_TAG = 'epub-custom'
MAX_SIDES = {0, 600, 800, 1000, 1400, 1600}


def parse_request(body: str) -> dict:
    kv = {}
    for line in (body or '').splitlines():
        m = re.match(r'^\s*([a-z_]+)\s*:\s*(.*?)\s*$', line)
        if m:
            kv[m.group(1)] = m.group(2)
    aid = int(kv.get('aid', ''))          # 非数字 -> ValueError
    vols = kv.get('volumes', 'all').lower()
    only = None if vols in ('', 'all') else {int(x) for x in vols.split(',') if x.strip()}
    if only is not None and (not only or len(only) > 60 or min(only) < 1):
        raise ValueError('volumes 无效')
    max_side = int(kv.get('max_side', MAX_SIDE))
    if max_side not in MAX_SIDES:
        raise ValueError(f'max_side 仅支持 {sorted(MAX_SIDES)}')
    images = kv.get('images', 'yes').lower() not in ('no', 'false', '0')
    return {'aid': aid, 'volumes': only, 'max_side': max_side, 'images': images}


def link(tag: str, name: str) -> str:
    return f'{GH_PROXY}https://github.com/{REPO}/releases/download/{tag}/{name}'


def comment(title: str, tag: str, files: list[tuple[str, str, int]], note: str = '') -> str:
    lines = [f'**{title}** 已生成：', '']
    for name, vtitle, size in files:
        lines.append(f'- [{vtitle}]({link(tag, name)}) ({size / 1048576:.1f} MB)')
    lines += ['', note, '', '链接经 gh-proxy.org 加速；本 Issue 将自动关闭。']
    return '\n'.join(lines)


def main():
    os.makedirs('out', exist_ok=True)
    req = parse_request(os.environ.get('ISSUE_BODY', ''))
    aid = req['aid']

    known = txt_only_targets()
    if aid not in known:
        raise SystemExit(f'aid {aid} 不在“仅 TXT 源”的列表中（可能已有蓝奏 EPUB 源，或不存在），已拒绝')
    version, title = known[aid]

    is_default = req['volumes'] is None and req['max_side'] == MAX_SIDE and req['images']
    index = load_index()
    entry = index.get(str(aid), {})
    if is_default and entry.get('txt') == version and entry.get('volumes'):
        files = [(f'{aid}-{v["file"]}', v['title'], v['size']) for v in entry['volumes']]
        open(COMMENT_FILE, 'w', encoding='utf-8').write(comment(title, entry['tag'], files, '该小说此前已生成，直接提供下载。'))
        return

    fetcher = Fetcher('curl_cffi', fallback=True)
    try:
        info = build_novel(fetcher, aid, split=True, max_side=req['max_side'], with_images=req['images'],
                           only_volumes=req['volumes'])
    finally:
        fetcher.close()

    out_dir = os.path.join('out', 'epub', str(aid))
    if is_default:
        tag = upload(aid, info)
        index[str(aid)] = {'txt': version, 'title': info['title'], 'author': info['author'],
                           'built_at': info['built_at'], 'src_update': info['last_update'],
                           'volumes': info['volumes'], 'tag': tag}
        save_index(index)
        files = [(f'{aid}-{v["file"]}', v['title'], v['size']) for v in info['volumes']]
        note = '已加入常规列表，页面下次更新后可直接下载。'
    else:
        import shutil
        import subprocess
        variant = f'{req["max_side"] or "orig"}' + ('' if req['images'] else '-noimg')
        subprocess.run(['gh', 'release', 'view', CUSTOM_TAG], capture_output=True).returncode == 0 or \
            subprocess.run(['gh', 'release', 'create', CUSTOM_TAG, '--title', CUSTOM_TAG,
                            '--notes', 'On-demand custom EPUBs'], check=True)
        paths, files = [], []
        for v in info['volumes']:
            name = f'{aid}-{variant}-{v["file"]}'
            dst = os.path.join(out_dir, name)
            shutil.copyfile(os.path.join(out_dir, v['file']), dst)
            paths.append(dst)
            files.append((name, v['title'], v['size']))
        subprocess.run(['gh', 'release', 'upload', CUSTOM_TAG, *paths, '--clobber'], check=True)
        tag = CUSTOM_TAG
        note = f'自定义参数（{variant}）的版本，仅保留一段时间。'
    open(COMMENT_FILE, 'w', encoding='utf-8').write(comment(info['title'], tag, files, note))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, SystemExit) as e:
        open(COMMENT_FILE, 'w', encoding='utf-8').write(f'无法处理该请求：{e}')
        print(e, file=sys.stderr)
        sys.exit(1)
