# 轻小说文库 EPUB 下载 - [wenku.mojimoon.top](https://wenku.mojimoon.top)：单页展示全部条目，支持搜索、筛选与移动端浏览
    - **蓝奏 EPUB**：Calibre 生成，来自论坛整理
    - **重制 EPUB**：对仅有 TXT 源的小说，由 GitHub Actions 从 wenku8 重新抓取生成（含封面、插图、分卷目录），按卷下载
    - **TXT 源**：纯文本 EPUB（无样式、无插图），特别感谢 [布客新知](https://github.com/ixinzhi) 整理
    - 所有 GitHub 文件均通过 [gh-proxy.org](https://gh-proxy.org/) 下载

## Star History

**如果您觉得这个项目有用，点个 Star 支持一下吧！Thanks! 😊**

[![Star History Chart](https://api.star-history.com/chart?repos=mojimoon/wenku8&type=date&legend=top-left)](https://www.star-history.com/?repos=mojimoon%2Fwenku8&type=date&legend=top-left)

## Usage

克隆仓库并安装依赖：

```bash
git clone https://github.com/mojimoon/wenku8
cd wenku8
pip install -r requirements.txt
```

有 3 种爬虫方式可选：

- `requests`：在使用境内 IP 时推荐使用
- `playwright`：在使用境外 IP 时必须使用，能绕过 Cloudflare 验证
- `steel`：在使用风控 IP（如 GitHub Actions 的服务器）时必须使用 [Steel](https://steel.dev) 平台提供的无头浏览器服务，需注册账号并获取 API Key

如需使用 `playwright` 或 `steel`，还需安装 Playwright 及其浏览器：

```bash
pip install pytest-playwright
playwright install # 或 python -m playwright install
```

如需使用 `steel`，还需在项目根目录创建 `.env` 文件，内容如下：

```
STEEL_API_KEY=...
```

并填入从 [Steel 控制台](https://app.steel.dev/quickstart) 获取的 API Key。

---

此外，在 wenku8 某次更新后，还需要登录网站来访问论坛内容。为此，你需要在浏览器中登录后，将 `COOKIE` 文件保存到项目根目录。`COOKIE` 的开头如下所示：

```
jieqiUserCharset=utf-8; jieqiVisitId=...; ...
```

## Workflow

运行 `txt.py`：

- `incremental_scrape()` 获取最新的 TXT 源下载列表
    - 输出：`txt/*.csv`
    - 由于 GitHub API 限制最多显示 1,000 条数据，请检查是否有遗漏。如有，可以手动下载后运行 `filelist_to_csv.py` 进行转换。
- `merge_csv()` 合并、去重
    - 输出：`out/txt_list.csv`

运行 `main.py`：

- `scrape()` 获取最新的 EPUB 源下载列表
    - 输出：`out/dl.txt`, `out/post_list.csv`
- `merge()` 合并、去重并与 TXT 源进行匹配
    - 输出：`out/merged.csv`
- `create_html_merged(), create_html_epub()` 生成 HTML 文件
    - 输出：`public/index.html`, `public/epub.html`

`fill_meta.py`（低频，一次性）：为仅有 TXT 源的小说补全 wenku8 的 aid 与元数据，输出 `out/txt_meta.csv`。

`build_batch.py` / `gen_epub.py`（见 `.github/workflows/build_epub.yml`）：为这些小说按卷生成带插图的 EPUB 并上传到 Release（`epub-NN`），状态记录在 `out/epub_index.json`；仅当 TXT 源更新时才会重新生成。
本地单本测试：`python gen_epub.py --aid 129`（整本）或 `--split`（按卷）。

抓取层在 `utils/fetcher.py`，可切换方式：`requests` / `curl_cffi` / `playwright` / `steel`（`python main.py [scraper]`，默认 `curl_cffi`，失败自动升级）。
GitHub Actions 的 IP 会被 Cloudflare 拦截，实测 `curl_cffi`、`patchright`、`camoufox`、Steel、WARP 可通过，普通 `requests` 与有头 Chromium 不行。

此外，GitHub Actions 会每天自动运行 `main.py`，将 `public/` 目录提交到 `gh-pages` 分支并部署到 GitHub Pages。

## Remarks

为加快访问速度，HTML、CSS、JS 文件均已压缩（源代码在 `source` 目录下），且使用 jsDeliver CDN 加速。  

> 可参考本人博客中 [加快 GitHub Pages 国内访问速度](https://mojimoon.github.io/blog/2025/speedup-github-page/) 一文。

## License

[MIT License](LICENSE)
