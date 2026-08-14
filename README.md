# 轻小说文库 EPUB 下载

An automated crawler and static site generator for light novel ebooks from [轻小说文库](https://www.wenku8.net), featuring multiple download sources, daily updates, and GitHub Actions deployment with [Steel](https://steel.dev).

---

[![Scrape and Deploy](https://github.com/mojimoon/wenku8/actions/workflows/deploy.yml/badge.svg)](https://github.com/mojimoon/wenku8/actions/workflows/deploy.yml)

自动化从 [轻小说文库](https://www.wenku8.net) 获取 EPUB 格式电子书，并将结果整合为网页呈现：

- [mojimoon.github.io/wenku8](https://mojimoon.github.io/wenku8/index.html)：EPUB 源 + TXT 源
    - 内容全面，但条目数多，可能加载较慢
    - 特别感谢 [布客新知](https://github.com/ixinzhi) 整理 
- [mojimoon.github.io/wenku8/epub.html](https://mojimoon.github.io/wenku8/epub.html)：EPUB 源
    - 仅包含 EPUB 源，适合移动端浏览

## Star History

**如果您觉得这个项目有用，点个 Star 支持一下吧！Thanks! 😊**

<a href="https://www.star-history.com/?repos=mojimoon%2Fwenku8&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=mojimoon/wenku8&type=date&theme=dark&legend=top-left&sealed_token=645APGfetPVmFND5Ebj4ovOk1jxf-x7-73GapGzveUHVEhD-oenGojbx_g7ovL37eepDvMjCeKI2-DwVmzLW2ERhDOGDaBNt7RYA429XZ3akAIr1Xds6ctNi7of0jfTbduf1FNoNI2wk1b7uAIA2qTmcU_4e0pPx7UK4bhXBVjOXV-Du5BcQEI3O_hWc" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=mojimoon/wenku8&type=date&legend=top-left&sealed_token=645APGfetPVmFND5Ebj4ovOk1jxf-x7-73GapGzveUHVEhD-oenGojbx_g7ovL37eepDvMjCeKI2-DwVmzLW2ERhDOGDaBNt7RYA429XZ3akAIr1Xds6ctNi7of0jfTbduf1FNoNI2wk1b7uAIA2qTmcU_4e0pPx7UK4bhXBVjOXV-Du5BcQEI3O_hWc" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=mojimoon/wenku8&type=date&legend=top-left&sealed_token=645APGfetPVmFND5Ebj4ovOk1jxf-x7-73GapGzveUHVEhD-oenGojbx_g7ovL37eepDvMjCeKI2-DwVmzLW2ERhDOGDaBNt7RYA429XZ3akAIr1Xds6ctNi7of0jfTbduf1FNoNI2wk1b7uAIA2qTmcU_4e0pPx7UK4bhXBVjOXV-Du5BcQEI3O_hWc" />
 </picture>
</a>

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

此外，GitHub Actions 会每天自动运行 `main.py`，将 `public/` 目录提交到 `gh-pages` 分支并部署到 GitHub Pages。

## Remarks

为加快访问速度，HTML、CSS、JS 文件均已压缩（源代码在 `source` 目录下），且使用 jsDeliver CDN 加速。  

> 可参考本人博客中 [加快 GitHub Pages 国内访问速度](https://mojimoon.github.io/blog/2025/speedup-github-page/) 一文。

## License

[MIT License](LICENSE)
