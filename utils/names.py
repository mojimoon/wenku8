"""
书名 / 作者的规范化与匹配：把不同来源（论坛帖、TXT 源、wenku8 目录）中的条目对应到同一个 aid。

匹配分层（越靠前越可信，前一层有结果就不再往下）：
  0. 人工别名表（alias）
  1. 书名精确匹配：完整书名 > 主书名 > 别名（括号内），同层多个候选时用作者、再用相似度裁决
  2. 同作者模糊匹配：作者相同的书里，相似度最高且明显领先于第二名
  3. 全局模糊匹配：相似度很高（阈值严格）
书名里的数字必须一致（避免 “X” 匹配到 “X2”）。
"""

import re
from collections import defaultdict

# 过于通用的书名：必须作者一致才允许匹配
GENERIC_TITLES = {'时间', '少女', '再见宣言', '强袭魔女', '秋之回忆', '秋之回忆2', '魔王', '青梅竹马', '弹珠汽水'}

# 衍生作品标记：书名一方带有而另一方没有时，不能仅凭包含关系判定为同一本书
SPINOFF_MARKS = ('外传', '番外', '官方', '短篇', '特别篇', '外典', '同人', '画集', '设定集')

_NOT_WORD = re.compile(r'[^一-龥a-zA-Z0-9぀-ヿ]')


def purify(text) -> str:
    """只保留中文、英文、数字与日文假名，并转小写。"""
    return _NOT_WORD.sub('', text).lower() if isinstance(text, str) else ''


def split_alt(title: str) -> tuple[str, str]:
    """'A(B)' -> ('A', 'B')；兼容全角括号。"""
    title = (title or '').strip().replace('（', '(').replace('）', ')')
    if title.endswith(')') and '(' in title:
        i = title.rfind('(')
        return title[:i].strip(), title[i + 1:-1].strip()
    return title, ''


def title_keys(title: str) -> tuple[str, str, str]:
    """(完整, 主书名, 别名) 的规范化形式；缺失为空串。"""
    main, alt = split_alt(title)
    return purify(f'{main}{alt}'), purify(main), purify(alt)


def author_tokens(author) -> set[str]:
    if not isinstance(author, str):
        return set()
    return {t for t in (purify(x) for x in re.split(r'[/、,，&＆\s]+', author)) if t}


def same_author(a, b) -> bool:
    x, y = author_tokens(a), author_tokens(b)
    return bool(x and y and (x & y))


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def _digits(s: str) -> tuple:
    return tuple(re.findall(r'\d+', s))


def similarity(a_keys, b_keys) -> float:
    """两组书名（各取 完整/主/别名）的最大相似度；包含关系 0.95，数字不一致则无效。"""
    best = 0.0
    for x in {k for k in a_keys if k}:
        for y in {k for k in b_keys if k}:
            # 数字必须一致；一方带有“外传/官方/短篇”等而另一方没有，说明是衍生作品而不是同一本书
            if _digits(x) != _digits(y) or any((m in x) != (m in y) for m in SPINOFF_MARKS):
                continue
            s, l = (x, y) if len(x) <= len(y) else (y, x)
            if len(s) >= 4 and s in l:
                best = max(best, 0.95)
                continue
            A, B = _bigrams(x), _bigrams(y)
            best = max(best, 2 * len(A & B) / (len(A) + len(B)))
    return best


class Resolver:
    """titles: {aid: [书名, ...]}（同一本书的多个写法）；authors: {aid: 作者}。"""

    def __init__(self, titles: dict, authors: dict, alias: dict | None = None):
        self.titles, self.authors = titles, authors
        self.alias = alias or {}                       # 规范化书名 -> aid
        self.full, self.main, self.alt = defaultdict(set), defaultdict(set), defaultdict(set)
        self.keys = {}                                 # aid -> [(完整, 主, 别名), ...]
        self.by_author = defaultdict(set)
        for aid, ts in titles.items():
            self.keys[aid] = [title_keys(t) for t in ts]
            for f, m, a in self.keys[aid]:
                if f:
                    self.full[f].add(aid)
                if m:
                    self.main[m].add(aid)
                if a:
                    self.alt[a].add(aid)
            for tok in author_tokens(authors.get(aid)):
                self.by_author[tok].add(aid)

    def _exact(self, qf, qm, qa) -> list[set]:
        """按可信度分层返回候选集合。"""
        tiers = [
            self.full.get(qf, set()) if qa else set(),                      # 完整书名（含别名）一致
            (self.main.get(qm, set()) | self.full.get(qm, set())) if qm else set(),   # 主书名一致
            (self.alt.get(qm, set()) | (self.main.get(qa, set()) | self.alt.get(qa, set()) if qa else set())),  # 经别名对应
        ]
        return [t for t in tiers if t]

    def _score(self, aid, q) -> float:
        return max((similarity(q, k) for k in self.keys[aid]), default=0.0)

    def resolve(self, title: str, author=None, strict_generic: bool = True, global_min: float = 0.85,
                prefer: dict | None = None) -> int | None:
        qf, qm, qa = q = title_keys(title)
        for k in (purify(title), qf, qm):
            if k in self.alias:
                return self.alias[k]
        generic = qm in {purify(g) for g in GENERIC_TITLES} or len(qm) <= 3

        for cands in self._exact(qf, qm, qa):
            if len(cands) > 1:   # 同层多个：作者 -> 相似度
                by_author = [a for a in cands if same_author(self.authors.get(a), author)]
                if len(by_author) == 1:
                    return by_author[0]
                pool = by_author or list(cands)
                ranked = sorted(((self._score(a, q), a) for a in pool), reverse=True)
                if ranked[0][0] >= 0.6 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.15):
                    return ranked[0][1]
                if prefer:       # 仍有歧义：取优先级最高的（如最新帖子所属的 aid）
                    return max(pool, key=lambda a: prefer.get(a, 0))
                return None      # 歧义，交给人工别名表
            aid = next(iter(cands))
            if strict_generic and generic and not same_author(self.authors.get(aid), author):
                continue
            return aid

        # 前缀：名称被截断（如论坛列表只保留前 20 字）时，按前缀对应
        if len(qm) >= 8 or len(qf) >= 8:
            def is_prefix(k, x):
                if len(k) < 8 or len(x) < 8 or not (k.startswith(x) or x.startswith(k)):
                    return False
                extra = k[len(x):] if k.startswith(x) else x[len(k):]
                return not any(m in extra for m in SPINOFF_MARKS)
            hits = {aid for aid, ks in self.keys.items() for f, m, a in ks
                    if any(is_prefix(k, x) for k in (f, m) for x in (qf, qm))}
            if len(hits) == 1:
                return next(iter(hits))
        # 同作者模糊
        pool = set()
        for tok in author_tokens(author):
            pool |= self.by_author.get(tok, set())
        if pool:
            ranked = sorted(((self._score(a, q), a) for a in pool), reverse=True)
            if ranked[0][0] >= 0.3 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.12):
                return ranked[0][1]
        # 全局模糊（严格）
        ranked = sorted(((self._score(a, q), a) for a in self.keys), reverse=True)[:2]
        if ranked and ranked[0][0] >= global_min and (
                ranked[0][0] >= 0.85 or len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.15):
            return ranked[0][1]
        return None
