# 轻小说文库 EPUB 下载

[![pages-build-deployment](https://github.com/mojimoon/wenku8/actions/workflows/pages/pages-build-deployment/badge.svg)](https://github.com/mojimoon/wenku8/actions/workflows/pages/pages-build-deployment)

**Happy New Year 2026! 🎉** 2025 年的 TXT 源数据现已更新！

自动化从 [轻小说文库](https://www.wenku8.net) 获取 EPUB 格式电子书，并将结果整合为网页呈现：

- [mojimoon.github.io/wenku8](https://mojimoon.github.io/wenku8/index.html)：EPUB 源 + TXT 源
    - 内容全面，但条目数多，可能加载较慢
    - 特别感谢 [布客新知](https://github.com/ixinzhi) 整理 
- [mojimoon.github.io/wenku8/epub.html](https://mojimoon.github.io/wenku8/epub.html)：EPUB 源
    - 仅包含 EPUB 源，适合移动端浏览

## Star History

**如果您觉得这个项目有用，点个 Star 支持一下吧！**

[![Star History Chart](https://api.star-history.com/svg?repos=mojimoon/wenku8&type=Date)](https://www.star-history.com/#mojimoon/wenku8&Date)

## 使用方法

克隆仓库并安装依赖：

```bash
git clone https://github.com/mojimoon/wenku8
cd wenku8
pip install -r requirements.txt
```

代码提供了两种爬虫方式：`requests` 和 `playwright`。正常情况下使用 `requests` 即可，若遇到无法绕过 Cloudflare 验证的情况，可切换到 `playwright`。

若需使用 `playwright`，需额外安装：

```bash
pip install pytest-playwright
playwright install
```

---

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

## 注意事项

为加快访问速度，HTML、CSS、JS 文件均已压缩（源代码在 `source` 目录下），且使用 jsDeliver CDN 加速。  

> 可参考本人博客中 [加快 GitHub Pages 国内访问速度](https://mojimoon.github.io/blog/2025/speedup-github-page/) 一文。

## 许可证

[MIT License](LICENSE)
