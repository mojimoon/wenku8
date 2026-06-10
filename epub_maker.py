"""
EPUB3 生成模块
基于 ebooklib，复刻 sample.epub 的 calibre 格式。

生成结构:
    ├── mimetype
    ├── META-INF/container.xml
    └── OEBPS/
        ├── content.opf       (元数据 + manifest + spine)
        ├── toc.ncx           (EPUB2 兼容目录)
        ├── nav.xhtml         (EPUB3 导航)
        ├── Styles/style.css
        ├── Images/cover.jpg
        └── Text/
            ├── Cover.xhtml
            ├── chapter0.xhtml ... chapterN.xhtml
            └── Credits.xhtml
"""

import uuid
import datetime
import os
import re
from dataclasses import dataclass, field
from typing import Optional
from ebooklib import epub

# ─── 数据模型 ───────────────────────────────────────────

@dataclass
class NovelMeta:
    """小说元数据"""
    title: str
    author: str
    source_url: str = ""
    description: str = ""
    publisher: str = ""
    subjects: list = field(default_factory=list)
    series: str = ""          # 系列名
    series_index: int = 0     # 卷号
    language: str = "zh"


@dataclass
class Chapter:
    """章节数据"""
    title: str
    content: str              # 纯文本，段落用 \n\n 分隔
    index: int = 0


# ─── 默认 CSS ───────────────────────────────────────────

DEFAULT_CSS = """body {
  padding: 0;
  margin-top: 0;
  margin-bottom: 0;
  margin-left: 1%;
  margin-right: 1%;
  line-height: 1.3;
}
h1 {
  line-height: 1.3;
  text-align: center;
  font-weight: bold;
  font-size: 1.5em;
  margin-top: 0;
  margin-bottom: 0;
}
div,
figure,
section {
  margin: 0;
  padding: 0;
  text-align: justify;
}
p {
  text-indent: 2em;
  line-height: 1.3;
  display: block;
}
p.asterisk,
p.numerals {
  text-indent: 5em;
  padding: 1em 0;
  display: block;
}
p.numerals {
  font-weight: bold;
}
.cover {
  margin: 0;
  padding: 0;
  text-indent: 0;
  text-align: center;
}
.cover svg,
.illust svg {
  border-radius: 1rem;
}
.illust {
  display: block;
}
.illust:not(:last-child) {
  margin-bottom: 1em;
}
.entry {
  padding: 3px;
  background: none repeat scroll 0 0 #eee;
  border-radius: 10px;
  margin-top: 0.5em;
  color: #000;
}
.credits,
p.last-update {
  background: none repeat scroll 0 0 #eee;
  text-indent: 0;
  text-align: center;
}
.credits b {
  background: none repeat scroll 0 0 #eee;
}
h1.credits {
  background: none repeat scroll 0 0 #eee;
  font-size: 1.5em;
  font-weight: bold;
  text-align: center;
  padding: 1em 0 1em 0;
  display: block;
}
p.author-signature,
p.reference {
  text-align: right;
  padding-right: 2em;
}
"""

# ─── XHTML 模板 ─────────────────────────────────────────

COVER_XHTML_TEMPLATE = """<html xml:lang="zh-CN" xmlns="http://www.w3.org/1999/xhtml">
    <head>
        <meta content="text/html; charset=utf-8" http-equiv="Content-Type"/>
        <link href="../Styles/style.css" rel="stylesheet" type="text/css"/>
        <title>封面</title>
    </head>
    <body>
        <figure class="cover">
            <svg xmlns="http://www.w3.org/2000/svg" version="1.1" xmlns:xlink="http://www.w3.org/1999/xlink"
                 width="100%" height="100%" viewBox="0 0 {img_w} {img_h}">
                <image width="{img_w}" height="{img_h}" xlink:href="../Images/cover.jpg"/>
            </svg>
        </figure>
    </body>
</html>"""

CHAPTER_XHTML_TEMPLATE = """<html xml:lang="zh-CN" xmlns="http://www.w3.org/1999/xhtml">
    <head>
        <meta content="text/html; charset=utf-8" http-equiv="Content-Type"/>
        <link href="../Styles/style.css" rel="stylesheet" type="text/css"/>
        <title>{title}</title>
    </head>
    <body>
        <section>
            <h1>{title}</h1>
            <hr/>
{body}
        </section>
    </body>
</html>"""

CREDITS_XHTML_TEMPLATE = """<!DOCTYPE html>
<html xml:lang="zh-CN" xmlns="http://www.w3.org/1999/xhtml">
 <head>
  <meta content="text/html; charset=utf-8" http-equiv="Content-Type"/>
  <link href="../Styles/style.css" rel="stylesheet" type="text/css"/>
  <title>制作人员</title>
 </head>
 <body>
  <div class="entry">
   <h1 class="credits">制作人员</h1>
   <p class="credits">
    内容来源：轻小说文库 (www.wenku8.net)
    <br/>
    自动化 EPUB 生成
   </p>
   <p class="last-update">最后更新：{date}</p>
  </div>
 </body>
</html>"""

NAV_XHTML_TEMPLATE = """<?xml version='1.0' encoding='utf-8'?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh" xml:lang="zh">
  <head>
    <title>Navigation</title>
    <meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>
  </head>
  <body>
    <nav epub:type="toc">
  <ol>
    <li><a href="Text/Cover.xhtml">封面</a></li>
{nav_items}
    <li><a href="Text/Credits.xhtml">制作人员</a></li>
  </ol>
</nav>
</body>
</html>"""


# ─── 正文段落处理 ──────────────────────────────────────

def _is_section_number(text: str) -> bool:
    """检测是否为小节编号（如 '1', '2', '三', 'ⅰ' 等）"""
    stripped = text.strip()
    if not stripped:
        return False
    # 阿拉伯数字
    if re.match(r'^[0-9]+$', stripped) and len(stripped) <= 3:
        return True
    # 中文数字
    if re.match(r'^[零一二三四五六七八九十百千]+$', stripped) and len(stripped) <= 4:
        return True
    # 罗马数字
    if re.match(r'^[IVXivx]+$', stripped) and len(stripped) <= 5:
        return True
    return False


def _clean_content(raw_text: str) -> str:
    """清洗正文文本，跳过来源声明行"""
    lines = raw_text.strip().split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # 跳过网站来源声明
        if stripped.startswith('本文来自') or stripped.startswith('轻小说文库'):
            continue
        if 'wenku8' in stripped.lower():
            continue
        cleaned.append(stripped)
    return '\n'.join(cleaned)


def _text_to_xhtml(raw_text: str) -> str:
    """将纯文本内容转换为 EPUB 的 XHTML body 部分"""
    text = _clean_content(raw_text)
    # 按空行分割段落
    paragraphs = re.split(r'\n\s*\n', text)
    html_parts = []

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # 按换行分割（同一段内的换行用 <br/> 连接）
        sub_lines = [s.strip() for s in para.split('\n') if s.strip()]
        if not sub_lines:
            continue

        first_line = sub_lines[0]

        # 判断是否为小节编号
        if _is_section_number(first_line) and len(sub_lines) == 1:
            html_parts.append(f'            <p class="numerals">{_escape_xml(first_line)}</p>')
        else:
            # 检查以 ※ 或 ★ 等特殊符号开头的段落
            if first_line.startswith(('※', '★', '◆', '◇', '□', '■')):
                joined = '<br/>\n'.join(_escape_xml(l) for l in sub_lines)
                html_parts.append(f'            <p class="asterisk">{joined}</p>')
            else:
                joined = '<br/>\n'.join(_escape_xml(l) for l in sub_lines)
                html_parts.append(f'            <p>{joined}</p>')

    return '\n'.join(html_parts)


def _escape_xml(text: str) -> str:
    """转义 XML 特殊字符"""
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    text = text.replace('"', '&quot;')
    text = text.replace("'", '&apos;')
    return text


# ─── EPUB 生成核心 ─────────────────────────────────────

def create_epub(
    meta: NovelMeta,
    chapters: list[Chapter],
    cover_image_data: Optional[bytes] = None,
    cover_mime: str = "image/jpeg",
    output_path: str = "output.epub",
    last_update_date: Optional[str] = None,
) -> str:
    """
    生成 EPUB3 文件。

    Args:
        meta: 小说元数据
        chapters: 章节列表
        cover_image_data: 封面图片二进制数据
        cover_mime: 封面图片 MIME 类型
        output_path: 输出文件路径
        last_update_date: 最后更新日期 (YYYY-MM-DD)

    Returns:
        生成的 EPUB 文件路径
    """
    book = epub.EpubBook()

    # ── 标识符 ──
    book_uid = f"urn:uuid:{uuid.uuid4()}"
    book.set_identifier(book_uid)
    book.set_title(meta.title)
    book.set_language(meta.language)
    book.add_author(meta.author)

    # ── 元数据 ──
    if meta.source_url:
        book.add_metadata('DC', 'source', meta.source_url)
    if meta.description:
        book.add_metadata('DC', 'description', meta.description)
    if meta.publisher:
        book.add_metadata('DC', 'publisher', meta.publisher)
    for subject in meta.subjects:
        if subject:
            book.add_metadata('DC', 'subject', subject)

    book.add_metadata('DC', 'contributor', 'wenku8-epub-gen', {'role': 'bkp'})

    # 系列信息
    if meta.series:
        book.add_metadata(None, 'meta', '', {
            'property': 'belongs-to-collection',
            'id': 'series-id',
        })
        book.add_metadata(None, 'meta', meta.series, {
            'refines': '#series-id',
            'property': 'collection-type',
        })
        book.add_metadata(None, 'meta', str(meta.series_index), {
            'refines': '#series-id',
            'property': 'group-position',
        })

    # 修改日期
    today = datetime.date.today().isoformat()
    book.add_metadata(None, 'meta', f'{today}T00:00:00Z', {
        'property': 'dcterms:modified',
    })

    # ── CSS ──
    css_item = epub.EpubItem(
        uid="style.css",
        file_name="Styles/style.css",
        media_type="text/css",
        content=DEFAULT_CSS.encode('utf-8'),
    )
    book.add_item(css_item)

    # ── 封面图片 ──
    cover_image_item = None
    if cover_image_data:
        cover_image_item = epub.EpubItem(
            uid="cover.jpg",
            file_name="Images/cover.jpg",
            media_type=cover_mime,
            content=cover_image_data,
        )
        book.add_item(cover_image_item)

    # ── 创建章节 EPUB items ──
    chapter_items = []
    spine_items = []
    toc_entries = []

    # 封面页
    if cover_image_item:
        cover_xhtml = COVER_XHTML_TEMPLATE.format(img_w=1034, img_h=1500)
    else:
        # 无封面时用简单文本
        cover_xhtml = (
            '<html xml:lang="zh-CN" xmlns="http://www.w3.org/1999/xhtml">'
            '<head><title>封面</title></head>'
            f'<body><h1 style="text-align:center;padding:2em;">{_escape_xml(meta.title)}</h1>'
            f'<p style="text-align:center;">{_escape_xml(meta.author)}</p></body></html>'
        )

    cover_item = epub.EpubItem(
        uid="Cover.xhtml",
        file_name="Text/Cover.xhtml",
        media_type="application/xhtml+xml",
        content=cover_xhtml.encode('utf-8'),
        properties="svg" if cover_image_item else "",
    )
    book.add_item(cover_item)
    spine_items.append(cover_item)
    toc_entries.append(epub.Link("Text/Cover.xhtml", "封面", "Cover"))

    # 各章节
    for ch in chapters:
        body_html = _text_to_xhtml(ch.content)
        xhtml = CHAPTER_XHTML_TEMPLATE.format(
            title=_escape_xml(ch.title),
            body=body_html,
        )
        item_id = f"chapter{ch.index}.xhtml"
        item = epub.EpubItem(
            uid=item_id,
            file_name=f"Text/chapter{ch.index}.xhtml",
            media_type="application/xhtml+xml",
            content=xhtml.encode('utf-8'),
        )
        book.add_item(item)
        chapter_items.append(item)
        spine_items.append(item)
        toc_entries.append(epub.Link(f"Text/chapter{ch.index}.xhtml", ch.title, item_id))

    # 制作人员页
    ld = last_update_date or today
    credits_xhtml = CREDITS_XHTML_TEMPLATE.format(date=ld)
    credits_item = epub.EpubItem(
        uid="Credits.xhtml",
        file_name="Text/Credits.xhtml",
        media_type="application/xhtml+xml",
        content=credits_xhtml.encode('utf-8'),
    )
    book.add_item(credits_item)
    spine_items.append(credits_item)
    toc_entries.append(epub.Link("Text/Credits.xhtml", "制作人员", "Credits"))

    # ── NAV (EPUB3) ──
    nav_items_str = '\n'.join(
        f'    <li><a href="Text/Cover.xhtml">封面</a></li>'
    )
    nav_items_str += '\n' + '\n'.join(
        f'    <li><a href="Text/chapter{ch.index}.xhtml">{_escape_xml(ch.title)}</a></li>'
        for ch in chapters
    )
    nav_items_str += '\n    <li><a href="Text/Credits.xhtml">制作人员</a></li>'

    nav_xhtml_str = NAV_XHTML_TEMPLATE.format(nav_items=nav_items_str)
    nav_item = epub.EpubItem(
        uid="nav",
        file_name="nav.xhtml",
        media_type="application/xhtml+xml",
        content=nav_xhtml_str.encode('utf-8'),
        properties="nav",
    )
    book.add_item(nav_item)

    # ── NCX (EPUB2 兼容) ──
    book.toc = toc_entries

    # ── SPINE ──
    book.spine = spine_items

    # ── 写入文件 ──
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    epub.write_epub(output_path, book)

    return output_path


# ─── 便捷函数 ──────────────────────────────────────────

def make_epub_from_raw(
    title: str,
    author: str,
    chapters_raw: list[tuple[str, str]],  # [(chapter_title, chapter_text), ...]
    description: str = "",
    source_url: str = "",
    publisher: str = "",
    subjects: list = None,
    cover_image_data: bytes = None,
    output_path: str = "",
    series: str = "",
    series_index: int = 0,
    last_update: str = "",
) -> str:
    """
    便捷函数：从原始数据直接生成 EPUB。

    Args:
        title: 书名
        author: 作者
        chapters_raw: 章节列表 [(标题, 正文文本), ...]
        description: 简介
        source_url: 源 URL
        publisher: 出版社/文库
        subjects: 标签列表
        cover_image_data: 封面图片数据
        output_path: 输出路径 (为空则自动生成)
        series: 系列名
        series_index: 卷序号
        last_update: 最后更新日期

    Returns:
        生成的 EPUB 文件路径
    """
    meta = NovelMeta(
        title=title,
        author=author,
        source_url=source_url,
        description=description,
        publisher=publisher,
        subjects=subjects or [],
        series=series,
        series_index=series_index,
    )

    chapters = [
        Chapter(title=t, content=c, index=i)
        for i, (t, c) in enumerate(chapters_raw)
    ]

    if not output_path:
        safe_title = re.sub(r'[\\/*?:"<>|]', '', title)[:80]
        output_path = f"{safe_title}.epub"

    return create_epub(
        meta=meta,
        chapters=chapters,
        cover_image_data=cover_image_data,
        output_path=output_path,
        last_update_date=last_update,
    )
