# PostgreSQL 18.6 中文手册

PostgreSQL 18.6 官方文档的简体中文译本，直接翻译自官方 SGML 源码（`doc/src/sgml`），
保持原始文档结构与构建方式，HTML 版式与 postgresql.org 官方文档一致。

在线阅读：https://sixizi.github.io/pgmancn/

## 项目结构

```
doc/src/sgml/          PostgreSQL 18.6 官方文档源码（已翻译）
tools/pgtrans.py       翻译引擎：只替换文本节点，保留全部 SGML 标签
tools/run.py           按小节翻译并逐节 git commit
tools/build.sh         构建 HTML（xmllint + xsltproc + DocBook XSL）
tools/status.json      翻译进度（可断点续跑）
```

## 翻译约定

- 逐文件、逐小节翻译，每完成一个小节提交一次 commit（`zh: 翻译 <文件> — <小节>`）。
- 所有 SGML 标签、属性、内部交叉引用（`linkend`）保持不变。
- 代码块、SQL 关键字、命令名、函数名、配置参数、产品名 `PostgreSQL` 保持英文。
- 术语统一（表、模式、表空间、事务、元组、预写式日志……），详见 `tools/pgtrans.py` 中的术语表。

## 本地构建

macOS：

```sh
brew install docbook-xsl
./tools/build.sh
open doc/src/sgml/html/index.html
```

Linux（Debian/Ubuntu）：

```sh
sudo apt-get install xsltproc libxml2-utils docbook-xsl docbook-xml
XML_CATALOG_FILES=/etc/xml/catalog ./tools/build.sh
```

推送到 `main` 后由 GitHub Actions 自动构建并发布到 GitHub Pages。

## 版权

原文版权归 PostgreSQL Global Development Group 所有，按其许可证（PostgreSQL License）发布。
译文在相同许可证下发布。
