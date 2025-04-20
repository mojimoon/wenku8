# 轻小说文库 EPUB 下载

[![Deploy static content to Pages](https://github.com/mojimoon/wenku8/actions/workflows/static.yml/badge.svg)](https://github.com/mojimoon/wenku8/actions/workflows/static.yml)

自动化从 [轻小说文库](https://www.wenku8.net) 获取 EPUB 格式电子书，并将结果整合为网页呈现：

[mojimoon.github.io/wenku8/](https://mojimoon.github.io/wenku8/)

## 使用方法

运行 `main.py`，将自动进行以下工作：

- 获取最新的 EPUB 下载列表
- 生成 `index.html` 文件

此外，GitHub Actions 每天会自动运行 `main.py` 并部署到 GitHub Pages 上，确保网页始终是最新的。
