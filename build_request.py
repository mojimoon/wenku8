"""
按需生成：处理页面生成的 GitHub Issue（标题以 "[build]" 开头），生成分卷 EPUB 并输出回复内容。

Issue 正文为 "key: value" 行（由页面预填，也可手写）:
    aid: 129
    volumes: all            # 或 1,3
    max_side: 1000          # 插图长边像素，0=原图
    images: yes             # yes / no

输入来自环境变量 ISSUE_BODY（不可信内容，仅做严格解析），输出:
    out/request_comment.md  回复到 Issue 的 Markdown（含下载链接）
所有版本都按卷缓存并记入 out/epub_index.json：1000 为页面上的默认版本（Release epub-NN，与批量生成共用），
其他（原图/1600/1400/800/600/无图）在 epub-var-NN；再次请求同一版本、同样的卷时直接回复。
"""

import os
import re
import sys

from build_batch import (load_index, merge_slot, name_files, record_blocked, record_default, save_index,
                         txt_only_targets, update_notes, upload)
from gen_epub import MAX_SIDE, CopyrightBlocked, build_novel
from utils import Fetcher

GH_PROXY = 'https://gh-proxy.org/'
REPO = os.environ.get('GITHUB_REPOSITORY', 'mojimoon/wenku8')
COMMENT_FILE = os.path.join('out', 'request_comment.md')
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


def variant_of(req: dict) -> str:
    """版本名：1000 为默认版本（空串），其他为 orig / 1600 / 1400 / 800 / 600 / noimg。"""
    if not req['images']:
        return 'noimg'
    return '' if req['max_side'] == MAX_SIDE else ('orig' if req['max_side'] == 0 else str(req['max_side']))


def link(tag: str, name: str) -> str:
    return f'{GH_PROXY}https://github.com/{REPO}/releases/download/{tag}/{name}'


def fmt_size(n: int) -> str:
    return f'{n / 1048576:.1f} MB' if n >= 1048576 else f'{max(1, round(n / 1024))} KB'


def comment(title: str, aid: int, tag: str, vols: list[dict], note: str = '') -> str:
    lines = [f'**{title}**（aid {aid}）已生成：', '']
    for v in vols:
        lines.append(f'- [{v["title"]}]({link(tag, f"{aid}-{v["file"]}")}) ({fmt_size(v["size"])})')
    lines += ['', note, '', '链接经 gh-proxy.org 加速；本 Issue 将自动关闭。']
    return '\n'.join(lines)


def write(text: str):
    with open(COMMENT_FILE, 'w', encoding='utf-8') as f:
        f.write(text)


def main():
    os.makedirs('out', exist_ok=True)
    req = parse_request(os.environ.get('ISSUE_BODY', ''))
    aid = req['aid']

    known = txt_only_targets()
    if aid not in known:
        raise SystemExit(f'aid {aid} 不在“仅 TXT 源”的列表中（可能已有蓝奏 EPUB 源，或不存在），已拒绝')
    version, title = known[aid]
    index = load_index()
    entry = index.get(str(aid), {})
    if entry.get('blocked'):
        raise SystemExit(f'《{title}》已因版权问题被轻小说文库下架（章节内容为空），无法生成')

    variant = variant_of(req)
    slot = entry if not variant else entry.get('variants', {}).get(variant, {})
    have = {v['index']: v for v in slot.get('volumes', [])} if slot.get('txt') == version else {}
    total = slot.get('total', len(have)) if have else 0
    wanted = req['volumes'] or (set(range(1, total + 1)) if total else None)
    if wanted is not None and wanted <= have.keys():
        write(comment(title, aid, slot['tag'], [have[i] for i in sorted(wanted)], '该版本此前已生成，直接提供下载。'))
        return
    todo = wanted - have.keys() if wanted is not None else None

    fetcher = Fetcher('curl_cffi', fallback=True)
    try:
        info = build_novel(fetcher, aid, split=True, max_side=req['max_side'], with_images=req['images'],
                           only_volumes=todo)
    except CopyrightBlocked as e:
        record_blocked(index, aid, title)
        save_index(index)
        raise SystemExit(str(e))
    finally:
        fetcher.close()

    name_files(info, variant)
    tag = upload(aid, info, variant)
    if not variant:
        record_default(index, aid, info, version, tag)
        slot = index[str(aid)]
        note = '已加入常规列表，页面下次更新后可直接下载。'
    else:
        entry = index.setdefault(str(aid), {'title': info['title'], 'author': info['author']})
        slot = entry.setdefault('variants', {})[variant] = merge_slot(slot, info, version, tag)
        note = f'{variant} 版本已缓存，再次请求时直接提供下载。'
    save_index(index)
    update_notes(tag, index)
    vols = {v['index']: v for v in slot['volumes']}
    show = sorted(wanted) if wanted is not None else sorted(vols)
    write(comment(info['title'], aid, tag, [vols[i] for i in show if i in vols], note))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, SystemExit) as e:
        write(f'无法处理该请求：{e}')
        print(e, file=sys.stderr)
        sys.exit(1)
