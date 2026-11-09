#!/usr/bin/env python3
"""补译：只修复 status.json 中记录的失败单元，避免误译代码和表格。

用于主流程中因标签/占位符校验失败而保留英文的单元。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pgtrans as P  # noqa: E402
import run as R  # noqa: E402

ROOT = R.ROOT
SGM = R.SGM
SKIP_KEYS = {
    "datatype.sgml::datatype-datetime#401",
    "sslinfo.sgml::sslinfo#51",
}


def failed_units_by_file():
    status = R.load_status()
    grouped = OrderedDict()
    for key in status.get("failed", []):
        if "#" in key:
            file_part, uid_part = key.rsplit("#", 1)
            uid = int(uid_part)
        else:
            file_part, uid = key, None
        rel, _sec = file_part.split("::", 1)
        grouped.setdefault(rel, []).append((key, uid, _sec))
    return grouped


def requires_translation(unit):
    plain = re.sub(r"\{\{[OE]\d+\}\}", "", re.sub(r"<[^>]+>", "", unit.src))
    if re.search(r"[\u4e00-\u9fff]", plain):
        return False
    words = re.findall(r"[A-Za-z][A-Za-z0-9_.:/-]*", plain)
    if len(words) < 2:
        return False
    natural_words = [
        word for word in re.findall(r"[A-Za-z]{2,}", plain)
        if "_" not in word and "-" not in word and "." not in word and not word.isupper()
    ]
    return len(natural_words) >= 2


def main():
    grouped = failed_units_by_file()
    files = list(grouped)
    if len(sys.argv) > 1:
        files = [f for f in files if any(k in f for k in sys.argv[1:])]
    total_fixed = 0
    for rel in files:
        path = os.path.join(SGM, rel)
        if not os.path.exists(path):
            continue
        unit_keys = {uid: key for key, uid, _sec in grouped[rel] if uid is not None}
        section_keys = {sec: key for key, uid, sec in grouped[rel] if uid is None}
        text = open(path, encoding="utf-8").read()
        units = P.make_units(text)
        bad = [u for u in units if u.uid in unit_keys and requires_translation(u)
               and unit_keys[u.uid] not in SKIP_KEYS]
        bad.extend(u for u in units if (u.section or "_") in section_keys
                   and u.uid not in unit_keys and requires_translation(u))
        bad.sort(key=lambda u: u.uid)
        stale = [key for key, uid, sec in grouped[rel]
                 if (uid is not None and next((u for u in units if u.uid == uid), None) is None)
                 or (uid is not None and next((u for u in units if u.uid == uid and requires_translation(u)), None) is None)
                 or (uid is None and not any((u.section or "_") == sec and requires_translation(u) for u in units))]
        if stale:
            status = R.load_status()
            status["failed"] = [key for key in status["failed"] if key not in set(stale)]
            R.save_status(status)
            print(f"  cleaned {len(stale)} stale/no-prose keys", flush=True)
        if not bad:
            continue
        print(f"[{rel}] {len(bad)} failed units", flush=True)
        changed = False
        for i in range(0, len(bad), 12):
            grp = bad[i:i + 12]
            res = P.translate_units(grp, batch_chars=1200, batch_max=2, workers=8,
                                    log=lambda s: print("  " + s, flush=True))
            if not res:
                continue
            resolved = set()
            for unit in grp:
                if unit.uid not in res:
                    continue
                new_text = R.apply_translations(text, [unit], {unit.uid: res[unit.uid]})
                if sorted(R.tags_of(new_text)) != sorted(R.tags_of(text)) or not P.balanced(new_text.strip()):
                    print(f"  !! structure check failed in {rel} unit {unit.uid}", flush=True)
                    continue
                text = new_text
                changed = True
                total_fixed += 1
                if unit.uid in unit_keys:
                    resolved.add(unit_keys[unit.uid])
                else:
                    section = unit.section or "_"
                    if all(x.uid in res for x in bad if (x.section or "_") == section):
                        resolved.add(section_keys[section])
            if resolved:
                status = R.load_status()
                status["failed"] = [k for k in status["failed"] if k not in resolved]
                R.save_status(status)
        if changed:
            open(path, "w", encoding="utf-8").write(text)
            subprocess.run(["git", "-C", ROOT, "add", "-f", os.path.relpath(path, ROOT)], check=True)
            subprocess.run(["git", "-C", ROOT, "commit", "-m", f"zh: 补译 {rel}"], check=False,
                           capture_output=True)
            print(f"  committed {rel}", flush=True)
    print("fixed units:", total_fixed)


if __name__ == "__main__":
    main()
