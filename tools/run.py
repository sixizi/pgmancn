#!/usr/bin/env python3
"""按小节翻译 PostgreSQL SGML 文档，每完成一节提交一次 git commit。

用法：
  python3 tools/run.py --files intro.sgml
  python3 tools/run.py --limit 5
  python3 tools/run.py --all
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pgtrans as P  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SGM = os.path.join(ROOT, "doc", "src", "sgml")
STATUS = os.path.join(ROOT, "tools", "status.json")


def load_status():
    if os.path.exists(STATUS):
        return json.load(open(STATUS, encoding="utf-8"))
    return {"done": [], "failed": []}


def save_status(st):
    json.dump(st, open(STATUS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def git(*args, check=True):
    return subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True, check=check)


def book_order() -> list[str]:
    """按 postgres.sgml 实体展开顺序返回文件列表。"""
    mapping = {}
    for line in open(os.path.join(SGM, "filelist.sgml"), encoding="utf-8"):
        m = re.match(r'<!ENTITY\s+([\w.-]+)\s+SYSTEM\s+"([^"]+)">', line.strip())
        if m:
            mapping[m.group(1)] = m.group(2)
    order: list[str] = []
    seen = set()

    def expand(fname: str):
        path = os.path.join(SGM, fname)
        if not os.path.exists(path) or fname in seen:
            return
        seen.add(fname)
        order.append(fname)
        text = open(path, encoding="utf-8").read()
        for m in re.finditer(r"&([\w.-]+);", text):
            ref = m.group(1)
            if ref in mapping:
                expand(mapping[ref])

    expand("postgres.sgml")
    return order


def apply_translations(text: str, units, results: dict[int, str]) -> str:
    out = text
    for u in sorted(units, key=lambda x: -x.start):
        tr = results.get(u.uid)
        if not tr:
            continue
        lead = re.match(r"\s*", u.src).group()
        trail = re.search(r"\s*$", u.src).group()
        body = tr.strip()
        out = out[: u.start] + lead + body + trail + out[u.end:]
    return out


def tags_of(s: str):
    return [m.group() for m in P.TAG_RE.finditer(s) if m.group().startswith("<")]


def translate_file(path: str, model: str, workers: int, st: dict, force=False,
                   commit=True, dry=False, verbose=True) -> tuple[int, int]:
    rel = os.path.relpath(path, SGM)
    text = open(path, encoding="utf-8").read()
    units = P.make_units(text)
    if not units:
        return 0, 0
    groups: "OrderedDict[str, list]" = OrderedDict()
    for u in units:
        groups.setdefault(u.section or "_", []).append(u)

    done_keys = set(st["done"])
    n_done = 0
    for sec, grp in groups.items():
        key = f"{rel}::{sec}"
        if key in done_keys and not force:
            continue
        # 重新读取：前面小节写入后原文偏移会变化
        text = open(path, encoding="utf-8").read()
        units_now = P.make_units(text)
        grp = [u for u in units_now if (u.section or "_") == sec]
        total = len(grp)
        if not total:
            continue
        if verbose:
            print(f"[{rel} §{sec}] {total} units, {sum(len(u.src) for u in grp)} chars", flush=True)
        if dry:
            continue
        t0 = time.time()
        res = P.translate_units(grp, workers=workers, model=model,
                                log=(lambda s: print(s, flush=True)) if verbose else (lambda s: None))
        missing = [u.uid for u in grp if u.uid not in res]
        new_text = apply_translations(text, grp, res)
        # 结构校验：标签序列必须完全一致
        if tags_of(new_text) != tags_of(text):
            print(f"  !! tag sequence changed in {rel}, section {sec}; skipped", flush=True)
            st.setdefault("failed", []).append(key)
            save_status(st)
            continue
        text = new_text
        if commit:
            open(path, "w", encoding="utf-8").write(text)
            title = ""
            for u in grp:
                if re.match(r"<title[ >]", u.src.strip()[:7]):
                    title = res.get(u.uid, "").strip()
                    break
            git("add", os.path.relpath(path, ROOT))
            msg = f"zh: 翻译 {rel} — {sec}" + (f"（{title}）" if title else "")
            r = git("commit", "-m", msg, check=False)
            if r.returncode != 0:
                print(f"  !! commit failed: {r.stderr.strip()[:200]}", flush=True)
        if missing:
            st.setdefault("failed", []).extend(f"{rel}::{sec}#{u}" for u in missing)
        st["done"].append(key)
        n_done += 1
        save_status(st)
        if verbose:
            print(f"  ok {total - len(missing)}/{total} in {time.time() - t0:.1f}s", flush=True)
    return len(units), n_done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--model", default=os.environ.get("PGTRANS_MODEL", "aicloud-deepseek-flash"))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-commit", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-file", default=None)
    args = ap.parse_args()

    st = load_status()
    files = args.files or book_order()
    if args.from_file:
        start = next((i for i, f in enumerate(files) if f == args.from_file), 0)
        files = files[start:]
    if args.limit:
        files = files[: args.limit]

    tot_u = tot_s = 0
    t0 = time.time()
    for f in files:
        p = os.path.join(SGM, f)
        if not os.path.exists(p):
            continue
        u, s = translate_file(p, args.model, args.workers, st, force=args.force,
                              commit=not args.no_commit, dry=args.dry_run)
        tot_u += u
        tot_s += s
    print(f"files={len(files)} units={tot_u} sections_committed={tot_s} elapsed={time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
