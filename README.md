# 轻小说文库 EPUB 下载

[![Daily and On-Commit Deploy](https://github.com/mojimoon/wenku8/actions/workflows/deploy.yml/badge.svg)](https://github.com/mojimoon/wenku8/actions/workflows/deploy.yml)

自动化从 [轻小说文库](https://www.wenku8.net) 获取 EPUB 格式电子书，并将结果整合为网页呈现：

- [mojimoon.github.io/wenku8/](https://mojimoon.github.io/wenku8/)：EPUB 源
    - 推荐使用，样式美观，更新较快
- [mojimoon.github.io/wenku8/txt.html](https://mojimoon.github.io/wenku8/txt.html)：TXT 源
    - 老书较全，每年更新一次，特别感谢 [布客新知](https://github.com/ixinzhi) 整理 

## 开始

```bash
git clone
cd wenku8
pip install -r requirements.txt
```

## 使用方法

运行 `main.py` 将进行以下工作：

- 获取最新的 EPUB 下载列表
    - 输出：`summary.csv`, `latest.txt`
- 整合数据并去重
    - 输出：`summary.json`
- 生成网页
    - 输出：`index.html`

此外，GitHub Actions 每天会自动运行 `main.py` 并部署到 GitHub Pages 上，确保网页始终是最新的。

运行 `txt.py` 将进行以下工作：

- 获取最新的 TXT 源下载列表
    - 输出：`txt/`
    - 由于 GitHub API 限制最多显示 1,000 条数据，请检查是否有遗漏。如有，可以手动下载后运行 `filelist_to_csv.py` 进行转换。
- 与 EPUB 数据匹配并去重
    - 输出：`txt.csv`
- 生成网页
    - 输出：`txt.html`
