"""PostgreSQL 文档 SGML 翻译引擎。

只替换文本节点，保留全部标签/属性；代码类元素整体替换为占位符，
翻译后还原，因此文档结构在字节级保持一致。
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

TAG_RE = re.compile(
    r"<!--.*?-->|<\?.*?\?>|<!\[CDATA\[.*?\]\]>|<![^>]*>|</?[A-Za-z][^>]*>|&[A-Za-z][A-Za-z0-9._-]*;|&#\d+;|&#x[0-9A-Fa-f]+;",
    re.S,
)

SKIP_TAGS = {
    "programlisting", "synopsis", "screen", "literallayout", "computeroutput",
    "userinput", "funcsynopsis", "funcsynopsisinfo", "sgml", "book", "article",
}

OPAQUE_TAGS = {
    "programlisting", "screen", "synopsis", "literallayout", "programlistingco",
    "literallayoutco", "screenco", "synopsisco",
    "command", "literal", "function", "varname", "type", "structname", "filename",
    "option", "envar", "systemitem", "application", "productname", "acronym",
    "database", "username", "hostname", "classname", "methodname", "interfacename",
    "oid", "symbol", "token", "sgmltag", "markuptype", "returnvalue", "prompt",
    "replaceable", "keycap", "keysym", "mousebutton", "errorname", "errortype",
    "parameter", "code", "constant", "structfield", "functionname", "typename",
    "envvar", "programlistingco",
}

BLOCK_TAGS = {
    "para", "simpara", "title", "subtitle", "term", "entry", "td", "th",
    "refpurpose", "glossterm", "bridgehead", "caption", "listitem", "question",
}

# 包含块级子元素时自身不再作为翻译单元
CONTAINER_BLOCKS = {"listitem", "answer", "question"}


class Node:
    __slots__ = ("tag", "raw_open", "start", "inner_start", "inner_end", "end", "children", "parent")

    def __init__(self, tag, raw_open, start, inner_start, parent):
        self.tag = tag
        self.raw_open = raw_open
        self.start = start
        self.inner_start = inner_start
        self.inner_end = None
        self.end = None
        self.children = []
        self.parent = parent

    def __repr__(self):
        return f"<{self.tag} {self.start}:{self.inner_end}>"


def parse_tags(text: str) -> Node:
    root = Node("#root", "", 0, 0, None)
    root.inner_start = 0
    root.inner_end = len(text)
    stack = [root]
    pos = 0
    for m in TAG_RE.finditer(text):
        tok = m.group()
        if tok.startswith("<!--") or tok.startswith("<?") or tok.startswith("<![CDATA[") \
                or tok.startswith("<!") or tok.startswith("&"):
            continue
        name_m = re.match(r"</?([A-Za-z][\w.-]*)", tok)
        if not name_m:
            continue
        name = name_m.group(1).lower()
        if tok.startswith("</"):
            # pop to matching
            for i in range(len(stack) - 1, 0, -1):
                if stack[i].tag == name:
                    stack[i].inner_end = m.start()
                    stack[i].end = m.end()
                    del stack[i:]
                    break
        elif tok.endswith("/>"):
            node = Node(name, tok, m.start(), m.end(), stack[-1])
            node.inner_end = m.end()
            node.end = m.end()
            stack[-1].children.append(node)
        else:
            node = Node(name, tok, m.start(), m.end(), stack[-1])
            stack[-1].children.append(node)
            stack.append(node)
    return root


def has_letters(s: str) -> bool:
    return bool(re.search(r"[A-Za-z]", s))


def text_of(text: str, node: Node) -> str:
    return text[node.inner_start:node.inner_end]


def contains_block(node: Node) -> bool:
    for ch in node.children:
        if ch.tag in BLOCK_TAGS and ch.tag not in ("listitem",):
            return True
        if contains_block(ch):
            return True
    return False


def collect_units(text: str):
    """返回翻译单元区间 (start, end, node)。

    含块级子元素的段落（如段后跟列表/表格）拆成多段文本单元，
    子元素本身另行处理，从而保证不重叠、不遗漏。
    """
    spans = []
    root = parse_tags(text)

    def walk(node: Node):
        if node.tag in SKIP_TAGS:
            return
        if node.tag in BLOCK_TAGS and has_letters(text_of(text, node)):
            block_kids = [
                c for c in node.children
                if (c.tag in BLOCK_TAGS and has_letters(text_of(text, c))) or contains_block(c)
            ]
            if block_kids:
                pos = node.inner_start
                for c in block_kids:
                    if c.start > pos:
                        spans.append((pos, c.start, node))
                    pos = c.end
                if node.inner_end > pos:
                    spans.append((pos, node.inner_end, node))
                for c in block_kids:
                    walk(c)
                return
            spans.append((node.inner_start, node.inner_end, node))
            return
        for ch in node.children:
            walk(ch)

    walk(root)
    spans.sort(key=lambda x: x[0])
    return spans


@dataclass
class Unit:
    uid: int
    start: int
    end: int
    src: str                     # 含占位符的待译文本
    placeholders: dict[str, str]  # {{O0}} -> 原始片段
    tags: list[str]
    node: object = None
    section: str = ""            # 所属小节标识


def render(node: Node, text: str, ph: dict[str, str]) -> str:
    """把节点内部渲染成待译字符串：代码元素->占位符，透明标签保留。"""
    out = []
    pos = node.inner_start
    for ch in node.children:
        seg = text[pos:ch.start]
        out.append(sub_placeholders(seg, ph))
        if ch.tag in OPAQUE_TAGS:
            key = "{{O%d}}" % len(ph)
            ph[key] = text[ch.start:ch.end]
            out.append(key)
        else:
            out.append(ch.raw_open)
            if ch.inner_end > ch.inner_start:
                out.append(render(ch, text, ph))
            out.append(closing_of(text, ch))
        pos = ch.end
    out.append(sub_placeholders(text[pos:node.inner_end], ph))
    return "".join(out)


def closing_of(text: str, node: Node) -> str:
    # inner_end 指向结束标签的 '<'
    m = re.match(r"</[A-Za-z][\w.-]*\s*>", text[node.inner_end:])
    return m.group() if m else ""


def sub_placeholders(seg: str, ph: dict[str, str]) -> str:
    def repl(m):
        key = "{{E%d}}" % len(ph)
        ph[key] = m.group()
        return key
    return TAG_RE.sub(repl, seg)


SECTION_TAGS = ("sect1", "chapter", "refentry", "preface", "appendix", "part")


def section_of(node: Node) -> str:
    cur = node.parent
    while cur is not None:
        if cur.tag in SECTION_TAGS:
            m = re.search(r'id="([^"]+)"', cur.raw_open)
            return (m.group(1) if m else cur.tag)
        cur = cur.parent
    return ""


def render_range(text: str, start: int, end: int, node: Node, ph: dict[str, str]) -> str:
    """渲染节点内 [start,end) 区间，区间内的内联子元素保留标签或转占位符。"""
    out = []
    pos = start
    for ch in node.children:
        if ch.start < start or ch.end > end:
            continue
        out.append(sub_placeholders(text[pos:ch.start], ph))
        if ch.tag in OPAQUE_TAGS:
            key = "{{O%d}}" % len(ph)
            ph[key] = text[ch.start:ch.end]
            out.append(key)
        else:
            out.append(ch.raw_open)
            if ch.inner_end > ch.inner_start:
                out.append(render(ch, text, ph))
            out.append(closing_of(text, ch))
        pos = ch.end
    out.append(sub_placeholders(text[pos:end], ph))
    return "".join(out)


def make_units(text: str) -> list[Unit]:
    units = []
    for i, (start, end, node) in enumerate(collect_units(text)):
        ph: dict[str, str] = {}
        src = render_range(text, start, end, node, ph)
        # 只保留含英文单词的单元
        raw_text = re.sub(r"<[^>]+>", "", TAG_RE.sub("", src))
        if len(re.findall(r"[A-Za-z]{2,}", raw_text)) < 1:
            continue
        tags = [m.group() for m in TAG_RE.finditer(src) if m.group().startswith("<")]
        units.append(Unit(i, start, end, src, ph, tags, node, section_of(node)))
    return units


# ---------------- LLM ----------------

API_BASE = os.environ.get("PGTRANS_API_BASE", "https://aigw.bitdeer.vip")
API_KEY_FILE = os.environ.get(
    "PGTRANS_KEY_FILE",
    "/Users/qirong.liu/Library/CloudStorage/OneDrive-Personal/vibe/Hermes/imagefact/.aigw_key",
)
MODEL = os.environ.get("PGTRANS_MODEL", "aicloud-glm")

SYSTEM_PROMPT = """你是 PostgreSQL 官方文档的资深译者，负责把英文文档翻译成简体中文。

必须遵守：
1. 忠实准确、行文通顺，保持技术文档的正式语气；不增删内容，不加解释。
2. 原样保留所有 SGML 标签（<> 及其属性），标签数量、顺序、属性一个字都不能改；只翻译标签之间的文字。
3. 形如 {{O0}}、{{E0}} 的占位符代表不可翻译的代码/实体，必须原样保留，数量与顺序不变。
4. 不翻译：SQL 关键字（保持大写）、命令名、函数名、类型名、变量名、文件名、配置参数、URL、代码片段；PostgreSQL 保持原文。
5. 术语统一：table 表；schema 模式；tablespace 表空间；role 角色；transaction 事务；index 索引；constraint 约束；trigger 触发器；view 视图；sequence 序列；extension 扩展；operator 运算符；expression 表达式；subquery 子查询；cursor 游标；prepared statement 预备语句；aggregate function 聚合函数；window function 窗口函数；replication 复制；WAL 预写式日志；checkpoint 检查点；autovacuum 自动清理；collation 排序规则；foreign data wrapper 外部数据包装器；full text search 全文检索；backup 备份；restore 恢复；cluster 数据库集簇；connection 连接；session 会话；tuple 元组；relation 关系；catalog 系统目录；privilege 权限；lock 锁；deadlock 死锁；partition 分区；index scan 索引扫描；query planner 查询规划器；executor 执行器。
6. 中文与英文/数字之间加一个半角空格；使用中文标点；保留原文的换行结构。
7. 只输出译文，不要任何说明或 Markdown 代码块。
"""


def api_chat(messages, model=MODEL, temperature=0.25, timeout=240, retries=4):
    key = open(API_KEY_FILE).read().strip()
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }).encode()
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                API_BASE.rstrip("/") + "/v1/chat/completions",
                data=payload,
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            return d["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(min(2 ** attempt, 20) + random.random())
    raise RuntimeError(f"API failed: {last}")


def build_prompt(units: list[Unit]) -> str:
    parts = ["请逐条翻译下列文档片段。每条译文以 ###编号### 单独一行开头，然后换行输出译文。\n"]
    for u in units:
        parts.append(f"###{u.uid}###\n{u.src}\n")
    return "\n".join(parts)


def parse_response(text: str, ids: list[int]) -> dict[int, str]:
    res = {}
    pattern = re.compile(r"^\s*###(\d+)###\s*$", re.M)
    marks = list(pattern.finditer(text))
    for i, m in enumerate(marks):
        uid = int(m.group(1))
        body = text[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(text)]
        res[uid] = body.strip("\n")
    return {k: v for k, v in res.items() if k in ids}


def validate(src: Unit, out: str) -> tuple[bool, str]:
    if not out.strip():
        return False, "empty"
    tags_out = [m.group() for m in TAG_RE.finditer(out) if m.group().startswith("<")]
    if sorted(tags_out) != sorted(src.tags):
        return False, "tag-mismatch"
    if not balanced(out):
        return False, "tag-unbalanced"
    ph_src = re.findall(r"\{\{[OE]\d+\}\}", src.src)
    ph_out = re.findall(r"\{\{[OE]\d+\}\}", out)
    if sorted(ph_src) != sorted(ph_out):
        return False, "placeholder-mismatch"
    if ph_src != ph_out:
        # 中文语序可能调整占位符位置；只要集合一致且可完整还原即可接受
        pass
    letters = re.sub(r"\{\{[OE]\d+\}\}|<[^>]+>", "", out)
    if re.search(r"[A-Za-z]{3,}", letters):
        # 仍可接受（含大量专有名词），仅记录
        pass
    cjk = len(re.findall(r"[\u4e00-\u9fff]", out))
    if cjk == 0 and len(re.findall(r"[A-Za-z]{3,}", letters)) > 2:
        return False, "no-chinese"
    return True, "ok"


def balanced(s: str) -> bool:
    depth = 0
    for m in TAG_RE.finditer(s):
        t = m.group()
        if not t.startswith("<") or t.startswith("<!") or t.startswith("<?"):
            continue
        nm = re.match(r"</?([A-Za-z][\w.-]*)", t)
        if not nm:
            continue
        if t.startswith("</"):
            depth -= 1
            if depth < 0:
                return False
        elif not t.endswith("/>"):
            depth += 1
    return depth == 0


def restore(out: str, ph: dict[str, str]) -> str:
    for k, v in ph.items():
        out = out.replace(k, v)
    return out


def translate_units(units: list[Unit], batch_chars=2600, batch_max=6, workers=6,
                    log=print, model=MODEL, done_cb=None):
    """并发翻译，返回 uid -> 译文。"""
    from concurrent.futures import ThreadPoolExecutor

    batches, cur, cur_len = [], [], 0
    for u in units:
        if cur and (cur_len + len(u.src) > batch_chars or len(cur) >= batch_max):
            batches.append(cur)
            cur, cur_len = [], 0
        cur.append(u)
        cur_len += len(u.src)
    if cur:
        batches.append(cur)

    results: dict[int, str] = {}

    def work(batch):
        ids = [u.uid for u in batch]
        prompt = build_prompt(batch)
        local = {}
        for attempt in range(3):
            try:
                resp = api_chat([
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt + ("\n\n注意：上次输出没有通过校验，请确保标签与占位符完全保持不变。" if attempt else "")},
                ], model=model, temperature=0.25 if attempt == 0 else 0.1)
                parsed = parse_response(resp, ids)
                for u in batch:
                    out = parsed.get(u.uid)
                    ok, why = validate(u, out or "")
                    if ok:
                        local[u.uid] = restore(out, u.placeholders)
                    else:
                        log(f"  ! unit {u.uid} {why}")
                break
            except Exception as e:  # noqa: BLE001
                log(f"  ! batch retry {attempt}: {e}")
                time.sleep(2 * (attempt + 1))
        if done_cb:
            done_cb(batch, local)
        return local

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(work, batches):
            results.update(res)
    return results
