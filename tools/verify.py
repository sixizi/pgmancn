#!/usr/bin/env python3
"""检查翻译完整性：占位符残留、未翻译段落、CJK 覆盖率。"""
from __future__ import annotations

import json
import os
import re
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pgtrans as P  # noqa: E402
import run as R  # noqa: E402

SGM = R.SGM
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
CODE_UNIT_WORDS = {
    "year", "years", "mon", "mons", "month", "months", "day", "days",
    "hour", "hours", "min", "mins", "minute", "minutes",
    "sec", "secs", "second", "seconds", "ago",
}
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def has_ancestor(node, names):
    while node is not None:
        if node.tag in names:
            return True
        node = node.parent
    return False


def section_id(node):
    while node is not None:
        match = re.search(r'id="([^"]+)"', node.raw_open)
        if match:
            return match.group(1)
        node = node.parent
    return ""


def is_code_like(plain):
    compact = re.sub(r"\s+", " ", plain.strip().strip(";:,"))
    if URL_RE.fullmatch(compact):
        return True
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]*", compact)
    if not tokens:
        return True
    if all(token.lower() in CODE_UNIT_WORDS for token in tokens):
        return True
    return all(
        token.isupper() or "_" in token or "-" in token
        for token in tokens
    )


def is_author_section(node, text):
    while node is not None and node.tag != "refsect1":
        node = node.parent
    if node is None:
        return False
    title = next((child for child in node.children if child.tag == "title"), None)
    if title is None:
        return False
    title_text = re.sub(r"<[^>]+>", "", text[title.inner_start:title.inner_end]).strip()
    return title_text == "作者"


def should_report(unit, plain, text):
    if CJK_RE.search(plain):
        return False
    if section_id(unit.node).lower().endswith(("-author", "-authors")):
        return False
    if 'role="func_signature"' in unit.node.raw_open:
        return False
    if is_author_section(unit.node, text):
        return False
    if has_ancestor(unit.node, {"biblioentry"}):
        return False
    if is_code_like(plain):
        return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--files", nargs="*", default=None)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    if args.files:
        files = args.files
    elif args.all:
        files = sorted(
            os.path.relpath(os.path.join(root, name), SGM)
            for root, _, names in os.walk(SGM)
            for name in names
            if name.endswith(".sgml")
        )
    else:
        files = R.book_order()
    report = {"files": 0, "placeholder_leaks": [], "english": [], "cjk_ratio": 0.0}
    total_plain = total_cjk = 0
    for rel in files:
        p = os.path.join(SGM, rel)
        if not os.path.exists(p):
            continue
        t = open(p, encoding="utf-8").read()
        report["files"] += 1
        if "{{O" in t or "{{E" in t:
            report["placeholder_leaks"].append(rel)
        for u in P.make_units(t):
            plain = re.sub(r"\{\{[OE]\d+\}\}", "", re.sub(r"<[^>]+>", "", u.src))
            cjk = len(CJK_RE.findall(plain))
            en = len(re.findall(r"[A-Za-z]{3,}", plain))
            total_cjk += cjk
            total_plain += len(plain)
            if en >= 5 and should_report(u, plain, t):
                report["english"].append({"file": rel, "sec": u.section, "text": plain.strip()[:120]})
    report["cjk_ratio"] = round(total_cjk / max(total_plain, 1), 3)
    json.dump(report, open(os.path.join(R.ROOT, "tools", "verify.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"files={report['files']} placeholder_leaks={len(report['placeholder_leaks'])} "
          f"english_units={len(report['english'])} cjk_ratio={report['cjk_ratio']}")
    for x in report["english"][:15]:
        print(" -", x["file"], x["sec"], x["text"][:70])


if __name__ == "__main__":
    main()
