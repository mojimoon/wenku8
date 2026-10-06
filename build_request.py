"""
按需生成：按创建顺序逐个处理所有打开的 build-request Issue（队列），生成分卷 EPUB，回复下载链接并关闭 Issue。

Issue 正文为 "key: value" 行（由页面预填，也可手写）:
    aid: 129
    volumes: all            # 或 1,3（目录中的第几个卷）
    max_side: 1000          # 插图长边像素，0=原图
    images: yes             # yes / no

Issue 正文是不可信内容：通过 GitHub API 读取，仅做严格解析，不进入 shell。
所有版本都按卷缓存并记入 out/epub_index.json：1000 为页面上的默认版本（与批量生成共用），
其他为原图/1600/1400/800/600/无图；每本书一个 Release（book-{aid}）。再次请求同一版本、同样的卷时直接回复。

防滥用：
  - 每个 GitHub 用户 1 小时内最多 5 条、24 小时内最多 10 条请求（按 Issue 创建时间计，含缓存命中与被拒绝的；
    仓库所有者 / 成员 / 协作者不限），超出则回复并关闭
  - 全站每 24 小时最多新抓取 50 本（只计需要抓取 wenku8 的请求，不含缓存命中与批量预生成，记录在
    out/request_builds.txt），超出的请求加上 queued 标签留在队列里，名额恢复后由之后的运行自动处理
  - 每次运行超过 --time-budget 分钟后不再开始新的请求（剩余的由下次运行继续）
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import time

from build_batch import (load_index, merge_slot, release_tag, name_files, record_blocked, record_default, save_index,
                         txt_only_targets, update_notes, upload)
from gen_epub import MAX_SIDE, CopyrightBlocked, build_novel
from utils import Fetcher

GH_PROXY = 'https://gh-proxy.org/'
REPO = os.environ.get('GITHUB_REPOSITORY', 'mojimoon/wenku8')
MAX_SIDES = {0, 600, 800, 1000, 1400, 1600}
BUILD_LOG = os.path.join('out', 'request_builds.txt')   # 每行: ISO 时间 aid #issue（仅新抓取的）
USER_LIMITS = ((datetime.timedelta(hours=1), 5), (datetime.timedelta(hours=24), 10))
GLOBAL_LIMIT = 50          # 每 24 小时新抓取的上限
EXEMPT = {'OWNER', 'MEMBER', 'COLLABORATOR'}
LABEL, QUEUED = 'build-request', 'queued'


class Deferred(Exception):
    """全站名额已满，留在队列中稍后处理。"""


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


# ─── GitHub ──────────────────────────────────────────

def gh(*args, input_text=None) -> str:
    return subprocess.run(['gh', *args], capture_output=True, text=True, encoding='utf-8', check=True,
                          input=input_text).stdout


def gh_api(path: str):
    return json.loads(gh('api', path))


def ts(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s.replace('Z', '+00:00'))


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def open_requests() -> list[dict]:
    issues = gh_api(f'repos/{REPO}/issues?labels={LABEL}&state=open&sort=created&direction=asc&per_page=100')
    return [i for i in issues if 'pull_request' not in i]


def reply(number: int, text: str, close: str | None):
    gh('issue', 'comment', str(number), '--body-file', '-', input_text=text)
    if close:
        gh('issue', 'close', str(number), '--reason', close)


# ─── 限额 ────────────────────────────────────────────

def user_over_limit(issue: dict) -> str:
    """该用户在这条 Issue 创建前的窗口内（含本条）的请求数超限时，返回说明。"""
    if issue.get('author_association') in EXEMPT:
        return ''
    login, created = issue['user']['login'], ts(issue['created_at'])
    since = (created - USER_LIMITS[-1][0]).strftime('%Y-%m-%dT%H:%M:%SZ')
    mine = gh_api(f'repos/{REPO}/issues?creator={login}&labels={LABEL}&state=all&since={since}&per_page=100')
    times = [ts(i['created_at']) for i in mine if 'pull_request' not in i]
    for window, limit in USER_LIMITS:
        n = sum(created - window < t <= created for t in times)
        if n > limit:
            hours = int(window.total_seconds() // 3600)
            return f'请求过于频繁：每个账号 {hours} 小时内最多 {limit} 条（本条为第 {n} 条），请稍后再提交。'
    return ''


def fresh_builds_24h() -> int:
    if not os.path.exists(BUILD_LOG):
        return 0
    cutoff = now() - datetime.timedelta(hours=24)
    with open(BUILD_LOG, encoding='utf-8') as f:
        return sum(1 for line in f if line.strip() and ts(line.split()[0]) > cutoff)


def log_build(aid: int, number: int):
    with open(BUILD_LOG, 'a', encoding='utf-8') as f:
        f.write(f'{now().strftime("%Y-%m-%dT%H:%M:%SZ")} {aid} #{number}\n')


# ─── 处理 ────────────────────────────────────────────

def process(req: dict, allow_fresh: bool) -> tuple[str, bool]:
    """处理一条请求，返回 (回复内容, 是否新抓取)。拒绝时抛 SystemExit，名额已满时抛 Deferred。"""
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
        return comment(title, aid, slot['tag'], [have[i] for i in sorted(wanted)], '该版本此前已生成，直接提供下载。'), False
    if not allow_fresh:
        raise Deferred()
    # 只补缺失的卷；旧 Release（epub-00 等）中的卷不与新 Release 混用，需要全部重新生成
    todo = (wanted - have.keys() if slot.get('tag') == release_tag(aid) else wanted) if wanted is not None else None

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
    return comment(info['title'], aid, tag, [vols[i] for i in show if i in vols], note), True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--time-budget', type=float, default=40, help='分钟；超过后不再开始新的请求')
    args = ap.parse_args()
    os.makedirs('out', exist_ok=True)
    start = time.time()
    queue = open_requests()
    print(f'[request] 队列中 {len(queue)} 条')
    for issue in queue:
        if (time.time() - start) / 60 > args.time_budget:
            print('[request] 达到时间预算，剩余请求留给下次运行')
            break
        number = issue['number']
        labels = {l['name'] for l in issue.get('labels', [])}
        print(f'\n[request] #{number} {issue["title"]}（{issue["user"]["login"]}）')
        try:
            why = user_over_limit(issue)
            if why:
                print('    ' + why)
                reply(number, why, 'not planned')
                continue
            req = parse_request(issue.get('body') or '')
            text, fresh = process(req, allow_fresh=fresh_builds_24h() < GLOBAL_LIMIT)
            if fresh:
                log_build(req['aid'], number)
            reply(number, text, 'completed')
        except Deferred:
            print('    全站名额已满，留在队列')
            if QUEUED not in labels:
                gh('issue', 'edit', str(number), '--add-label', QUEUED)
                reply(number, f'今日生成名额已满（全站每 24 小时最多新生成 {GLOBAL_LIMIT} 本），请求已排队，'
                              '名额恢复后会自动处理并在此回复，无需重新提交。', None)
        except (ValueError, SystemExit) as e:
            print(f'    拒绝: {e}')
            reply(number, f'无法处理该请求：{e}', 'not planned')
        except Exception as e:
            import traceback
            traceback.print_exc()
            reply(number, f'生成失败（{type(e).__name__}），请稍后重新提交，或查看 Actions 日志。', 'not planned')


if __name__ == '__main__':
    main()
