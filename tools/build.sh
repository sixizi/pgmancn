#!/bin/sh
# 构建中文 HTML 文档（与 postgresql.org 官方样式一致）
set -e
cd "$(dirname "$0")/../doc/src/sgml"
export XML_CATALOG_FILES="${XML_CATALOG_FILES:-/opt/homebrew/etc/xml/catalog}"
VER=$(sed -n 's/.*version "\(.*\)".*/\1/p' version.sgml)
xmllint --noent --valid --nonet --path . --output postgres-full.xml postgres.sgml
rm -rf html && mkdir -p html
xsltproc --nonet --path . --stringparam pg.version "$VER" \
  --param website.stylesheet 1 --stringparam l10n.gentext.language zh_cn \
  stylesheet.xsl postgres-full.xml
echo "built: $PWD/html/index.html"
