#!/usr/bin/env python3
"""
create_mr.py — 自动生成并提交 MR (描述从 git/trailer/测试证据自动拼)

设计目标: AI 提交 MR 时不再对着空模板手填。AI 完成任务时已经知道
"为什么改、改了什么、怎么测的", 这些信息开发时就产生了 —— 本脚本把它们
自动汇总成符合治理模板的 MR 描述并提交。AI 通常只需传 --why。

各段落数据来源:
  ## 背景        ← --why 参数 (只有用户/AI 知道任务背景)
  ## 变更内容    ← git diff 自动生成文件清单 (可 --what 覆盖)
  ## 自测确认    ← .governance/test-evidence.jsonl 测试记录 (可 --tested 覆盖)
  ## 风险与回滚  ← 自动判断大变更/敏感路径/schema (可 --risks 覆盖)
  治理元数据     ← 最新 commit 的 AI-Usage / Tested trailer, 放 <details> 折叠块

用法:
    # AI 最常用: 只传背景, 其余自动
    python create_mr.py --why "用户需求: 接入微信支付"

    # 仅生成描述不提交 (预览 / 测试)
    python create_mr.py --why "..." --dry-run

    # 人工补充更多段落
    python create_mr.py --why "..." --what "调整超时为30s" --tested "本地模拟延迟测试" \\
        --excludes "未改退款逻辑" --link "REQ-1234"

    # 生成草稿后打开编辑器让人改再提交
    python create_mr.py --why "..." --interactive

退出码: 0 成功 / 1 失败 / 2 用法错误
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

try:
    import yaml  # type: ignore
    _HAS_YAML = True
except Exception:  # pragma: no cover
    _HAS_YAML = False


EVIDENCE_PATH = ".governance/test-evidence.jsonl"

DEFAULT_CONFIG = {
    "large_change": {
        "line_threshold": 500,
        "excluded_paths": ["*.lock", "*.Designer.cs", "migrations/**", "**/*.generated.*"],
        "sensitive_paths": ["ci/", "CODEOWNERS", "charts*/", "*secret*", ".gitlab-ci.yml"],
        "schema_paths": ["*.sql", "migrations/**", "*.proto"],
    },
    "deliverhq_integration": {
        "enabled": False,
        "records_dirs": ["DeliverHQ/change-requests/", "docs/requirements/"],
        # 在 records_dirs/<需求ID>/ 下按这些文件名找需求文档 (按顺序取第一个存在的)
        "requirement_doc_patterns": ["requirement.md", "spec.md", "README.md", "index.md"],
        # 从需求文档里提取这些标题下的内容作为"背景"
        "background_headings": ["背景", "Background", "需求描述", "问题", "目标"],
    },
}

# diff 文件清单只展示源码/重要文件, 控制噪音
_NOISE_PATTERNS = ["*.lock", "**/*.generated.*", "*.Designer.cs"]


# ============================================================
# 配置
# ============================================================
def load_config(path: str | None) -> dict:
    if not path:
        for cand in ("governance.config.yml", "governance.config.yaml"):
            if os.path.isfile(cand):
                path = cand
                break
    if not path or not os.path.isfile(path) or not _HAS_YAML:
        return DEFAULT_CONFIG
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:  # pragma: no cover
        return DEFAULT_CONFIG
    merged = {**DEFAULT_CONFIG, **data}
    merged["large_change"] = {**DEFAULT_CONFIG["large_change"], **(data.get("large_change") or {})}
    merged["deliverhq_integration"] = {
        **DEFAULT_CONFIG["deliverhq_integration"],
        **(data.get("deliverhq_integration") or {}),
    }
    return merged


# ============================================================
# git
# ============================================================
def run_git(args: list[str], check: bool = True) -> str:
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True,
                           check=check)
        return r.stdout
    except FileNotFoundError:
        sys.stderr.write("[create-mr] 找不到 git\n")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"[create-mr] git 失败: git {' '.join(args)}\n{e.stderr}\n")
        sys.exit(1)


def current_branch() -> str:
    return run_git(["rev-parse", "--abbrev-ref", "HEAD"]).strip()


def numstat(base: str) -> list[tuple[int, int, str]]:
    """返回 [(added, removed, path), ...]。二进制文件行数记为 0。"""
    out = run_git(["diff", "--numstat", f"{base}...HEAD"])
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, d, p = parts
        try:
            rows.append((int(a), int(d), p))
        except ValueError:
            rows.append((0, 0, p))  # 二进制
    return rows


def commit_subjects(base: str) -> list[str]:
    out = run_git(["log", f"{base}..HEAD", "--format=%s"])
    return [l for l in out.splitlines() if l.strip()]


def latest_commit_body(base: str) -> str:
    out = run_git(["log", f"{base}..HEAD", "--format=%B", "-1"], check=False)
    return out


# ============================================================
# trailer 提取
# ============================================================
def extract_trailer(text: str, key: str) -> str | None:
    m = re.search(rf'(?im)^{re.escape(key)}:\s*(.+)$', text)
    return m.group(1).strip() if m else None


def collect_trailers(base: str) -> dict[str, str]:
    """从本次 MR 所有 commit 汇总治理 trailer (取最新出现值)。"""
    body = run_git(["log", f"{base}..HEAD", "--format=%B"], check=False)
    result = {}
    for key in ("AI-Usage", "AI-Tools", "AI-Models", "AI-Lines", "Tested",
                "Requirement-ID"):
        v = extract_trailer(body, key)
        if v:
            result[key] = v
    return result


# ============================================================
# 需求 ID 解析 + DeliverHQ 需求文档读取
# ============================================================
# 需求编号格式: CR-1234 / REQ-1234 (大小写不敏感)
_REQ_ID_RE = re.compile(r'\b((?:CR|REQ)-\d+)\b', re.IGNORECASE)


def resolve_requirement_id(explicit: str | None, base: str) -> str | None:
    """
    确定需求 ID, 优先级: 显式 --requirement-id > 分支名 > commit trailer。
    """
    if explicit:
        m = _REQ_ID_RE.search(explicit)
        return m.group(1).upper() if m else explicit.upper()
    # 从分支名解析, 如 feat/CR-1234-add-pay
    branch = current_branch()
    m = _REQ_ID_RE.search(branch)
    if m:
        return m.group(1).upper()
    # 退而从 commit trailer 找
    trailers = collect_trailers(base)
    rid = trailers.get("Requirement-ID")
    if rid:
        m = _REQ_ID_RE.search(rid)
        return m.group(1).upper() if m else rid.upper()
    return None


def _extract_heading_body(text: str, headings: list[str]) -> str | None:
    """从 markdown 提取某标题下、到下一个同级或更高级标题之前的内容。"""
    for h in headings:
        pat = re.compile(
            r'^#{1,6}\s*' + re.escape(h) + r'\s*$(?P<body>.*?)(?=^#{1,6}\s|\Z)',
            re.MULTILINE | re.DOTALL,
        )
        m = pat.search(text)
        if m:
            body = m.group("body")
            # 去掉 html 注释
            body = re.sub(r'<!--.*?-->', '', body, flags=re.DOTALL).strip()
            if body:
                return body
    return None


def read_why_from_requirement(req_id: str, cfg: dict) -> tuple[str | None, str]:
    """
    在 DeliverHQ/需求目录下找 <req_id> 的需求文档, 提取背景。
    返回 (背景文本 | None, 说明)。不依赖 DeliverHQ 内部格式: 路径/文件名/标题全来自 config。
    """
    dh = cfg.get("deliverhq_integration", {})
    records_dirs = dh.get("records_dirs", [])
    doc_patterns = dh.get("requirement_doc_patterns", ["requirement.md", "README.md"])
    headings = dh.get("background_headings", ["背景", "Background"])

    tried = []
    for rdir in records_dirs:
        cr_dir = os.path.join(rdir, req_id)
        if not os.path.isdir(cr_dir):
            # 也尝试小写目录名
            alt = os.path.join(rdir, req_id.lower())
            if os.path.isdir(alt):
                cr_dir = alt
            else:
                tried.append(cr_dir)
                continue
        for fname in doc_patterns:
            fpath = os.path.join(cr_dir, fname)
            if os.path.isfile(fpath):
                try:
                    text = open(fpath, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                body = _extract_heading_body(text, headings)
                if body:
                    # 附上来源, 便于追溯
                    return (f"{body}\n\n<!-- 背景自动取自 {fpath} -->",
                            f"从 {fpath} 提取")
                # 文档存在但没匹配到标题, 用整篇前几行兜底
                snippet = text.strip().splitlines()
                if snippet:
                    head = "\n".join(snippet[:10])
                    return (f"{head}\n\n<!-- 背景自动取自 {fpath} (未找到标题, 取开头) -->",
                            f"从 {fpath} 取开头 (未匹配背景标题)")
                tried.append(fpath)
    return (None, f"未找到需求 {req_id} 的文档 (查过: {', '.join(tried) or '无'})")


# ============================================================
# 段落生成
# ============================================================
def _fnmatch_any(path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/"):
            if path.startswith(pat) or fnmatch.fnmatch(path, pat + "**"):
                return True
        elif fnmatch.fnmatch(path, pat):
            return True
    return False


def gen_changes(rows: list[tuple[int, int, str]]) -> str:
    """从 numstat 生成 ## 变更内容 文件清单。"""
    if not rows:
        return "-"
    lines = []
    # 噪音文件 (lock/生成) 折叠成一行汇总
    shown = [(a, d, p) for a, d, p in rows if not _fnmatch_any(p, _NOISE_PATTERNS)]
    noise = [(a, d, p) for a, d, p in rows if _fnmatch_any(p, _NOISE_PATTERNS)]
    for a, d, p in sorted(shown, key=lambda x: -(x[0] + x[1])):
        lines.append(f"- `{p}` (+{a}/-{d})")
    if noise:
        na = sum(a for a, _, _ in noise)
        nd = sum(d for _, d, _ in noise)
        lines.append(f"- 其他 {len(noise)} 个生成/锁文件 (+{na}/-{nd})")
    return "\n".join(lines)


def gen_tested(evidence_path: str) -> str:
    """从测试证据生成 ## 自测确认。"""
    if not os.path.isfile(evidence_path):
        return ("- [ ] 本地构建通过：`命令`\n"
                "- [ ] 单元测试通过：`命令`\n"
                "  <!-- 未找到 .governance/test-evidence.jsonl, "
                "建议用 record_test_run.py 跑测试留痕 -->")
    # 每条命令取最新结果
    latest: dict[str, dict] = {}
    with open(evidence_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            cmd = r.get("cmd", "")
            if cmd not in latest or str(r.get("ts", "")) >= str(latest[cmd].get("ts", "")):
                latest[cmd] = r
    if not latest:
        return "- [ ] 单元测试通过：`命令`"
    lines = []
    for r in latest.values():
        failed = r.get("failed")
        total, passed = r.get("total"), r.get("passed")
        ok = isinstance(failed, int) and failed == 0 and r.get("exit_code", 1) == 0
        mark = "x" if ok else " "
        detail = ""
        if isinstance(total, int) and isinstance(passed, int):
            detail = f" ({passed}/{total} passed)"
        elif ok:
            detail = " (通过)"
        lines.append(f"- [{mark}] `{r.get('cmd', '?')}`{detail}")
    return "\n".join(lines)


def assess_risk(rows: list[tuple[int, int, str]], cfg: dict) -> str:
    """自动判断大变更/敏感路径/schema, 生成 ## 风险与回滚。"""
    lc = cfg["large_change"]
    excluded = lc.get("excluded_paths", [])
    sensitive = lc.get("sensitive_paths", [])
    schema = lc.get("schema_paths", [])
    threshold = int(lc.get("line_threshold", 500))

    total = 0
    hit_sensitive, hit_schema = set(), set()
    for a, d, p in rows:
        if _fnmatch_any(p, sensitive):
            hit_sensitive.add(p)
        if _fnmatch_any(p, schema):
            hit_schema.add(p)
        if _fnmatch_any(p, excluded):
            continue
        total += a + d

    reasons = []
    if total >= threshold:
        reasons.append(f"净改动 {total} 行 ≥ {threshold}")
    if hit_sensitive:
        reasons.append(f"触及高敏路径: {', '.join(sorted(hit_sensitive))}")
    if hit_schema:
        reasons.append(f"含 schema 变更: {', '.join(sorted(hit_schema))}")

    if not reasons:
        return "- 低风险，无需特别说明（自动评估：未触发大变更/敏感路径/schema 条件）"
    body = "**自动评估为大变更**，请补充回滚方案：\n"
    for r in reasons:
        body += f"- ⚠ {r}\n"
    body += "- 风险点：<!-- 请补充 -->\n- 应对/回滚：<!-- 请补充 -->"
    return body


def gen_metadata_block(trailers: dict[str, str], style: str) -> str:
    """生成治理元数据块。style: details(折叠) / section(段落) / comment(注释)。"""
    if not trailers:
        items = ["（未检测到治理 trailer，建议安装 hook: "
                 "bash governance/scripts/install-hooks.sh）"]
    else:
        order = ["AI-Usage", "AI-Tools", "AI-Models", "AI-Lines", "Tested",
                 "Requirement-ID"]
        items = [f"- **{k}**: {trailers[k]}" for k in order if k in trailers]

    body = "\n".join(items)
    if style == "comment":
        return f"<!--\n治理元数据 (CI 自动采集):\n{body}\n-->"
    if style == "section":
        return f"## 治理元数据\n\n{body}"
    # 默认折叠块
    return (f"<details>\n<summary>📊 治理元数据（CI 自动采集）</summary>\n\n"
            f"{body}\n\n</details>")


def build_description(args, cfg: dict) -> str:
    base = args.target_branch
    rows = numstat(base)
    trailers = collect_trailers(base)

    background = args.why
    changes = args.what or gen_changes(rows)
    excludes = args.excludes or "无"
    tested = args.tested or gen_tested(args.evidence)
    risks = args.risks or assess_risk(rows, cfg)
    meta = gen_metadata_block(trailers, args.meta_style)

    links = ""
    if args.link:
        links = "\n".join(f"- {l}" for l in args.link)
    else:
        links = "-"

    return f"""## 背景

{background}

## 变更内容

{changes}

## 不包含的内容

{excludes}

## 自测确认

{tested}

## 风险与回滚

{risks}

## 关联

{links}

---

{meta}
"""


def infer_title(args, base: str) -> str:
    if args.title:
        return args.title
    subs = commit_subjects(base)
    if subs:
        # 用最后一个(最早)还是第一个(最新)? 单 commit 直接用; 多 commit 用最新
        return subs[0]
    return "chore: update"


# ============================================================
# 提交 MR
# ============================================================
def detect_cli() -> str | None:
    if shutil.which("glab"):
        return "glab"
    if shutil.which("gh"):
        return "gh"
    return None


def submit_mr(title: str, description: str, target: str, cli: str) -> int:
    # 用临时文件传 description, 避免 shell 转义问题
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(description)
        desc_file = tf.name
    try:
        if cli == "glab":
            cmd = ["glab", "mr", "create", "--title", title,
                   "--description", description,
                   "--target-branch", target, "--fill"]
        else:  # gh
            cmd = ["gh", "pr", "create", "--title", title,
                   "--body-file", desc_file, "--base", target]
        sys.stderr.write(f"[create-mr] 提交: {cli} ...\n")
        r = subprocess.run(cmd, text=True)
        return r.returncode
    finally:
        os.unlink(desc_file)


def main() -> int:
    ap = argparse.ArgumentParser(description="自动生成并提交 MR")
    ap.add_argument("--why", help="## 背景: 为什么做这个变更 (AI 从任务上下文提取)")
    ap.add_argument("--requirement-id", help="需求编号 CR-xxxx / REQ-xxxx (装了 DeliverHQ 时据此读需求文档当背景)")
    ap.add_argument("--what", help="## 变更内容 (默认从 diff 自动生成)")
    ap.add_argument("--tested", help="## 自测确认 (默认从测试证据自动生成)")
    ap.add_argument("--risks", help="## 风险与回滚 (默认自动评估)")
    ap.add_argument("--excludes", help="## 不包含的内容")
    ap.add_argument("--link", action="append", help="## 关联项, 可多次 (Issue/REQ-xxx)")
    ap.add_argument("--title", help="MR 标题 (默认取最新 commit subject)")
    ap.add_argument("--target-branch", default="master", help="目标分支")
    ap.add_argument("--config", help="governance.config.yml 路径")
    ap.add_argument("--evidence", default=EVIDENCE_PATH, help="测试证据文件")
    ap.add_argument("--meta-style", choices=["details", "section", "comment"],
                    default="details", help="治理元数据呈现方式")
    ap.add_argument("--dry-run", action="store_true", help="只打印描述, 不提交")
    ap.add_argument("--interactive", action="store_true",
                    help="生成草稿后打开编辑器, 保存后再提交")
    args = ap.parse_args()

    cfg = load_config(args.config)

    # --- 分层确定背景 (## 背景) ---
    # 优先级: 显式 --why > DeliverHQ 需求文档 > 报错要求 --why
    req_link = None
    if not args.why:
        dh = cfg.get("deliverhq_integration", {})
        if dh.get("enabled"):
            req_id = resolve_requirement_id(args.requirement_id, args.target_branch)
            if req_id:
                why, note = read_why_from_requirement(req_id, cfg)
                if why:
                    args.why = why
                    req_link = f"Requirement-ID: {req_id}"
                    sys.stderr.write(f"[create-mr] 背景自动读取: {note}\n")
                else:
                    sys.stderr.write(f"[create-mr] DeliverHQ 已启用但{note}\n")

    if not args.why and not args.dry_run:
        sys.stderr.write(
            "[create-mr] 错误: 无法确定背景。请提供 --why, 例:\n"
            '  --why "用户需求: 接入微信支付"\n'
            "(装了 DeliverHQ 时, 也可让分支名含 CR-xxxx, 或传 --requirement-id CR-xxxx 自动读需求文档)\n"
        )
        return 2
    if not args.why:
        args.why = "<!-- 背景待填 (--dry-run 预览) -->"

    # 若从需求文档解析出 ID, 自动补进关联项
    if req_link:
        args.link = (args.link or []) + [req_link]

    description = build_description(args, cfg)
    title = infer_title(args, args.target_branch)

    if args.interactive:
        editor = os.environ.get("EDITOR", "vi")
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                         encoding="utf-8") as tf:
            tf.write(description)
            path = tf.name
        subprocess.run([editor, path])
        with open(path, encoding="utf-8") as f:
            description = f.read()
        os.unlink(path)

    if args.dry_run:
        print(f"# [DRY-RUN] 标题: {title}\n# 目标分支: {args.target_branch}\n")
        print(description)
        return 0

    cli = detect_cli()
    if not cli:
        sys.stderr.write(
            "[create-mr] 未找到 glab 或 gh CLI。已生成描述如下, 请手动创建 MR:\n\n"
        )
        print(f"标题: {title}\n")
        print(description)
        return 1

    return submit_mr(title, description, args.target_branch, cli)


if __name__ == "__main__":
    sys.exit(main())
