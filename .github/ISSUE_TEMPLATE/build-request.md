---
name: 请求生成 EPUB
about: 为仅有 TXT 源的小说，从源站重新生成带封面、插图、分卷目录的 EPUB（通常由网页「提交请求」自动预填）
title: "[build] "
labels: build-request
---

aid: 
volumes: all
max_side: 1000
images: yes

<!--
请勿修改上面的字段名，只改冒号后的值（提交后由 GitHub Actions 自动解析，生成后在本 Issue 回复下载链接并关闭）：
- aid：轻小说文库的小说 ID，即 https://www.wenku8.net/book/【ID】.htm 中的数字，仅限没有蓝奏 EPUB 源的小说
- volumes：all（全部卷）或卷序号，如 1,3。卷序号是目录列表中的顺序（第几个卷），不一定等于卷名中的数字（如短篇集、外传也占一个序号）
- max_side：插图长边像素，可选 0（原图）/ 600 / 800 / 1000 / 1400 / 1600，越小越快、文件越小
- images：yes（含插图）/ no（不含插图，最快）
-->
