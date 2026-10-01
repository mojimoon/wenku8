"""
EPUB3 生成模块（直接用 zipfile 写入，无第三方依赖）。

支持: 封面、简介页、分卷嵌套目录（nav.xhtml + toc.ncx）、正文内嵌插图。

生成结构:
    ├── mimetype
    ├── META-INF/container.xml
    └── OEBPS/
        ├── content.opf
        ├── toc.ncx           (EPUB2 兼容目录)
        ├── nav.xhtml         (EPUB3 导航)
        ├── Styles/style.css
        ├── Images/cover.<ext>, img0001.<ext> ...
        └── Text/Cover.xhtml, Intro.xhtml, v1.xhtml, c0001.xhtml ...
"""

import datetime
import os
import re
import uuid
import zipfile
from dataclasses import dataclass, field

# ─── 数据模型 ───────────────────────────────────────────


@dataclass
class NovelMeta:
    title: str
    author: str
    source_url: str = ''
    description: str = ''
    publisher: str = ''
    subjects: list = field(default_factory=list)
    status: str = ''
    series: str = ''          # 系列名（分卷 EPUB 用）
    series_index: int = 0
    language: str = 'zh-CN'
    identifier: str = ''      # 为空则随机生成 UUID
    modified: str = ''        # YYYY-MM-DD，为空则取今天


@dataclass
class Chapter:
    title: str
    # 内容块: ('p', 文本) 段落 | ('img', 图片文件名)，图片文件名对应 images 字典的 key
    blocks: list = field(default_factory=list)


@dataclass
class Volume:
    title: str
    chapters: list = field(default_factory=list)


# ─── CSS ────────────────────────────────────────────────

CSS = """body {
  margin: 0 1%;
  padding: 0;
  line-height: 1.5;
}
h1 {
  line-height: 1.3;
  text-align: center;
  font-weight: bold;
  font-size: 1.4em;
  margin: 1em 0;
}
h1.volume {
  font-size: 1.8em;
  margin: 40% 0 0 0;
}
p {
  margin: 0;
  text-indent: 2em;
  text-align: justify;
}
p.center {
  text-indent: 0;
  text-align: center;
  padding: 1em 0;
}
p.numerals {
  text-indent: 0;
  text-align: center;
  font-weight: bold;
  padding: 1em 0;
}
p.meta {
  text-indent: 0;
  text-align: center;
}
div.cover {
  text-align: center;
  margin: 0;
  padding: 0;
}
div.cover img {
  max-width: 100%;
  max-height: 100%;
}
div.illust {
  text-align: center;
  margin: 0 0 1em 0;
  page-break-inside: avoid;
}
div.illust img {
  max-width: 100%;
  height: auto;
}
div.intro p {
  text-indent: 0;
  margin: 0.5em 0;
}
"""

# ─── XHTML 模板 ─────────────────────────────────────────

XHTML = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN" lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>{title}</title>
<link href="../Styles/style.css" rel="stylesheet" type="text/css"/>
</head>
<body>
{body}
</body>
</html>
"""

MIME = {'jpg': 'image/jpeg', 'png': 'image/png', 'gif': 'image/gif', 'webp': 'image/webp'}


# ─── 工具 ───────────────────────────────────────────────


def escape_xml(text: str) -> str:
    return (text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            .replace('"', '&quot;'))


def sniff_ext(data: bytes) -> str:
    """根据文件头判断图片扩展名，未知按 jpg 处理。"""
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    return 'jpg'


def is_section_number(text: str) -> bool:
    """小节编号（如 '1', '三', 'Ⅱ'）"""
    t = text.strip()
    if not t or len(t) > 4:
        return False
    return bool(re.fullmatch(r'[0-9０-９零一二三四五六七八九十百千IVXivxⅠ-Ⅻ]+', t))


def block_to_xhtml(block: tuple) -> str:
    kind, value = block
    if kind == 'img':
        return f'<div class="illust"><img src="../Images/{value}" alt=""/></div>'
    if is_section_number(value):
        return f'<p class="numerals">{escape_xml(value)}</p>'
    if value.startswith(('※', '★', '◆', '◇', '□', '■', '＊', '*')) and len(value) <= 40:
        return f'<p class="center">{escape_xml(value)}</p>'
    return f'<p>{escape_xml(value)}</p>'


def page(title: str, body: str) -> bytes:
    return XHTML.format(title=escape_xml(title), body=body).encode('utf-8')


# ─── EPUB 生成 ──────────────────────────────────────────


def create_epub(meta: NovelMeta, volumes: list, images: dict = None,
                cover_data: bytes = None, output_path: str = 'output.epub', prefix: str = '') -> str:
    """
    生成 EPUB3 文件。

    Args:
        meta: 小说元数据
        volumes: [Volume, ...]
        images: {文件名: 二进制}，章节内容块通过文件名引用
        cover_data: 封面图片二进制，可为空
    """
    images = images or {}
    modified = meta.modified or datetime.date.today().isoformat()
    uid = meta.identifier or f'urn:uuid:{uuid.uuid4()}'

    files = {}      # 路径(相对 OEBPS) -> bytes
    manifest = []   # (id, href, media-type, properties)
    spine = []      # idref
    nav_tree = []   # [(title, href, [(title, href), ...])]

    files['Styles/style.css'] = CSS.encode('utf-8')
    manifest.append(('css', 'Styles/style.css', 'text/css', ''))

    # 封面
    cover_name = ''
    if cover_data:
        cover_name = prefix + 'cover.' + sniff_ext(cover_data)
        files['Images/' + cover_name] = cover_data
        manifest.append(('cover-image', 'Images/' + cover_name, MIME[sniff_ext(cover_data)], 'cover-image'))
        files[f'Text/{prefix}Cover.xhtml'] = page(
            '封面', f'<div class="cover"><img src="../Images/{cover_name}" alt="cover"/></div>')
        manifest.append(('cover', f'Text/{prefix}Cover.xhtml', 'application/xhtml+xml', ''))
        spine.append('cover')
        nav_tree.append(('封面', f'Text/{prefix}Cover.xhtml', []))

    # 简介页
    info = [f'<h1>{escape_xml(meta.title)}</h1>', f'<p class="meta">{escape_xml(meta.author)}</p>']
    extra = ' / '.join(x for x in (meta.publisher, meta.status) if x)
    if extra:
        info.append(f'<p class="meta">{escape_xml(extra)}</p>')
    if meta.subjects:
        info.append(f'<p class="meta">{escape_xml(" ".join(meta.subjects))}</p>')
    if meta.description:
        paras = ''.join(f'<p>{escape_xml(s.strip())}</p>' for s in meta.description.split('\n') if s.strip())
        info.append(f'<div class="intro">{paras}</div>')
    if meta.source_url:
        info.append(f'<p class="meta">来源：{escape_xml(meta.source_url)}</p>')
    files[f'Text/{prefix}Intro.xhtml'] = page('简介', '\n'.join(info))
    manifest.append(('intro', f'Text/{prefix}Intro.xhtml', 'application/xhtml+xml', ''))
    spine.append('intro')
    nav_tree.append(('简介', f'Text/{prefix}Intro.xhtml', []))

    # 图片
    for name, data in images.items():
        ext = name.rsplit('.', 1)[-1].lower()
        files['Images/' + name] = data
        manifest.append((f'img-{name}', 'Images/' + name, MIME.get(ext, 'image/jpeg'), ''))

    # 分卷与章节
    ci = 0
    for vi, vol in enumerate(volumes, 1):
        vhref = f'Text/{prefix}v{vi}.xhtml'
        files[vhref] = page(vol.title, f'<h1 class="volume">{escape_xml(vol.title)}</h1>')
        manifest.append((f'v{vi}', vhref, 'application/xhtml+xml', ''))
        spine.append(f'v{vi}')
        children = []
        for ch in vol.chapters:
            ci += 1
            href = f'Text/{prefix}c{ci:04d}.xhtml'
            body = f'<h1>{escape_xml(ch.title)}</h1>\n' + '\n'.join(block_to_xhtml(b) for b in ch.blocks)
            files[href] = page(ch.title, body)
            manifest.append((f'c{ci:04d}', href, 'application/xhtml+xml', ''))
            spine.append(f'c{ci:04d}')
            children.append((ch.title, href))
        nav_tree.append((vol.title, vhref, children))

    # nav.xhtml
    def nav_li(title, href, children):
        inner = ''
        if children:
            inner = '\n<ol>\n' + '\n'.join(nav_li(t, h, []) for t, h in children) + '\n</ol>'
        return f'<li><a href="{href}">{escape_xml(title)}</a>{inner}</li>'

    nav_body = ('<nav epub:type="toc" id="toc"><h1>目录</h1>\n<ol>\n'
                + '\n'.join(nav_li(*n) for n in nav_tree) + '\n</ol></nav>')
    files['nav.xhtml'] = XHTML.format(title='目录', body=nav_body).replace(
        '../Styles/style.css', 'Styles/style.css').encode('utf-8')
    manifest.append(('nav', 'nav.xhtml', 'application/xhtml+xml', 'nav'))

    # toc.ncx
    order = 0

    def ncx_point(title, href, children):
        nonlocal order
        order += 1
        pid = order
        kids = ''.join(ncx_point(t, h, []) for t, h in children)
        return (f'<navPoint id="np{pid}" playOrder="{pid}"><navLabel><text>{escape_xml(title)}</text></navLabel>'
                f'<content src="{href}"/>{kids}</navPoint>')

    points = ''.join(ncx_point(*n) for n in nav_tree)
    depth = 2 if any(n[2] for n in nav_tree) else 1
    files['toc.ncx'] = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        f'<head><meta name="dtb:uid" content="{escape_xml(uid)}"/><meta name="dtb:depth" content="{depth}"/>'
        '<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>'
        f'<docTitle><text>{escape_xml(meta.title)}</text></docTitle><navMap>{points}</navMap></ncx>'
    ).encode('utf-8')
    manifest.append(('ncx', 'toc.ncx', 'application/x-dtbncx+xml', ''))

    # content.opf
    md = [f'<dc:identifier id="BookId">{escape_xml(uid)}</dc:identifier>',
          f'<dc:title>{escape_xml(meta.title)}</dc:title>',
          f'<dc:creator>{escape_xml(meta.author)}</dc:creator>',
          f'<dc:language>{meta.language}</dc:language>']
    if meta.publisher:
        md.append(f'<dc:publisher>{escape_xml(meta.publisher)}</dc:publisher>')
    if meta.description:
        md.append(f'<dc:description>{escape_xml(meta.description)}</dc:description>')
    for s in meta.subjects:
        if s:
            md.append(f'<dc:subject>{escape_xml(s)}</dc:subject>')
    if meta.source_url:
        md.append(f'<dc:source>{escape_xml(meta.source_url)}</dc:source>')
    md.append(f'<meta property="dcterms:modified">{modified}T00:00:00Z</meta>')
    if cover_name:
        md.append('<meta name="cover" content="cover-image"/>')
    if meta.series:
        sx = escape_xml(meta.series)
        md.append(f'<meta property="belongs-to-collection" id="series1">{sx}</meta>')
        md.append('<meta refines="#series1" property="collection-type">series</meta>')
        md.append(f'<meta refines="#series1" property="group-position">{meta.series_index}</meta>')
        md.append(f'<meta name="calibre:series" content="{sx}"/>')
        md.append(f'<meta name="calibre:series_index" content="{meta.series_index}"/>')

    items = '\n'.join(
        f'<item id="{escape_xml(i)}" href="{escape_xml(h)}" media-type="{m}"' + (f' properties="{p}"' if p else '') + '/>'
        for i, h, m, p in manifest)
    refs = '\n'.join(f'<itemref idref="{escape_xml(i)}"/>' for i in spine)
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="BookId">\n'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n' + '\n'.join(md) + '\n</metadata>\n'
           f'<manifest>\n{items}\n</manifest>\n<spine toc="ncx">\n{refs}\n</spine>\n</package>\n')

    container = ('<?xml version="1.0" encoding="utf-8"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 '</rootfiles></container>')

    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    tmp = output_path + '.tmp'
    with zipfile.ZipFile(tmp, 'w') as z:
        z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', container, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/content.opf', opf, compress_type=zipfile.ZIP_DEFLATED)
        for path, data in files.items():
            # 图片本身已压缩，直接存储
            comp = zipfile.ZIP_STORED if path.startswith('Images/') else zipfile.ZIP_DEFLATED
            z.writestr('OEBPS/' + path, data, compress_type=comp)
    os.replace(tmp, output_path)
    return output_path
