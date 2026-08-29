#!/usr/bin/env python3
# PDF 分类/提取脚本（leow3bot pdf-route skill）。
# 用法:
#   pdf.py classify <file.pdf>   分类：文字型/扫描型 + 建议下一步
#   pdf.py extract  <file.pdf>   文字型 → markdown 落盘（hash 缓存，命中零解析）
#
# 依赖自举：优先 pdf-inspector（pip 包，结构化 markdown）；未安装时脚本输出
# 安装命令，装不上自动降级 pdftotext（纯文本）。渲染扫描页用 render.sh（poppler）。
# 输出面向模型：末尾必带「建议下一步」，模型照做即可，无需自行推理路由。
#
# 输出布局: ${TMPDIR}/leow3bot-pdf/<工作目录scope>/<内容hash>.md——scope 为工作目录
# basename + 完整路径 6 位 hash（与 render.sh 规则一致）：不同项目互不混放、可整目录
# 手动清理；7 天未动的陈旧产物在每次运行时自动 GC。

from __future__ import annotations  # 兼容 Python ≤3.9：延迟求值注解（str | None 等 3.10+ 语法）

import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get('TMPDIR', '/tmp')) / 'leow3bot-pdf'
SCRIPT_DIR = Path(__file__).resolve().parent
GC_MAX_AGE_DAYS = 7


def fmt_mb(n: float) -> str:
    return f'{n / 1024 / 1024:.1f}MB'


def scope_dir() -> Path:
    """工作目录派生的 scope：basename 可辨认 + 完整路径 6 位 hash 防同名目录冲突。
    os.getcwd() 是物理路径，与 render.sh 的 pwd -P 对齐（经符号链接进入也一致）。"""
    cwd = os.getcwd()
    base = Path(cwd).name[:32] or 'root'
    tag = hashlib.sha256(cwd.encode()).hexdigest()[:6]
    return ROOT / f'{base}-{tag}'


def gc_stale() -> None:
    """老化 GC：删 7 天未动的陈旧文件，移除清空后的 scope 目录。幂等，谁先跑谁清。"""
    now = time.time()
    try:
        entries = list(ROOT.rglob('*'))
    except OSError:
        return
    for p in entries:
        try:
            if p.is_file() and now - p.stat().st_mtime > GC_MAX_AGE_DAYS * 86400:
                p.unlink()
        except OSError:
            pass
    # 深度优先删空目录（rmdir 仅对空目录成功，非空抛 OSError 被吞）
    for d in sorted((p for p in entries if p.is_dir()), key=lambda x: len(x.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass


def ensure_dirs() -> Path:
    """建 scope 目录（含根目录，700 权限），返回输出目录。"""
    d = scope_dir()
    for p in (ROOT, d):
        try:
            p.mkdir(parents=True, exist_ok=True)
            os.chmod(p, 0o700)
        except OSError:
            pass
    return d


def file_digest(path: str) -> str:
    """流式内容 hash（12 位）——分块读取，几百 MB 的扫描件也不整载入内存。"""
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()[:12]


def run(cmd: list) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def pdf_pages(file: str) -> int | None:
    out = run(['pdfinfo', file])
    if out:
        m = re.search(r'^Pages:\s+(\d+)', out, re.M)
        if m:
            return int(m.group(1))
    return None


def load_inspector():
    try:
        import pdf_inspector
        return pdf_inspector
    except ImportError:
        return None


INSTALL_HINT = ('依赖缺失: pdf-inspector 未安装且 pdftotext 不可用。安装其一:\n'
                '  python3 -m pip install --user pdf-inspector   （推荐，结构化 markdown）\n'
                '  sudo apt install poppler-utils               （降级，纯文本）\n'
                '  若 pip 报 externally-managed-environment，加 --break-system-packages')


def fallback_classify(file: str) -> str | None:
    """pdftotext 前 3 页字符量判定。poppler 缺失返回 None。"""
    txt = run(['pdftotext', '-l', '3', file, '-'])
    if txt is None:
        return None
    return 'text' if len(re.sub(r'\s', '', txt)) > 150 else 'scanned'


def fmt_pages(pages: list, total: int) -> str:
    if not pages:
        return '无'
    if len(pages) == total:
        return f'全部 {total} 页'
    if len(pages) > 8:
        return f'第 {pages[0]}-{pages[-1]} 页等 {len(pages)} 页'
    return f'第 {",".join(map(str, pages))} 页'


def suggest_text(file: str) -> list:
    return ['建议下一步（文字型）:',
            f'  python3 {SCRIPT_DIR}/pdf.py extract "{file}"',
            '  然后用 read 分页读取产出的 markdown']


def suggest_scan(file: str) -> list:
    return ['建议下一步（按扫描件处理）:',
            f'  bash {SCRIPT_DIR}/render.sh "{file}" --pages 1-3',
            '  然后用 view 逐张查看 PNG（先看目录/首页定位，勿全量翻页）']


def all_pages(total: int) -> list:
    return list(range(1, total + 1))


def main() -> None:
    if len(sys.argv) < 3 or not os.path.isfile(sys.argv[2]):
        print('用法: pdf.py classify|extract <file.pdf>（参数须为文件——目录或不存在均不可）', file=sys.stderr)
        sys.exit(1)
    cmd, raw = sys.argv[1], sys.argv[2]
    if cmd not in ('classify', 'extract'):
        print(f'未知子命令: {cmd}（可用: classify, extract）', file=sys.stderr)
        sys.exit(1)
    gc_stale()  # classify/extract 都执行：skill 流程 classify 必先跑，谁先跑谁清
    file = os.path.abspath(raw)
    size = os.path.getsize(file)
    total = pdf_pages(file)
    if total is None:
        print(f'无法解析 PDF（可能损坏或加密）: {file}', file=sys.stderr)
        sys.exit(1)

    # 缓存先行（extract 专用）：命中则零解析直接返回——process_pdf 的全额解析只在 miss 时发生
    out_path = None
    if cmd == 'extract':
        out_path = ensure_dirs() / f'{file_digest(file)}.md'
        if out_path.exists():
            cached = out_path.read_text(encoding='utf-8')
            n_lines = cached.count(chr(10)) + 1
            print(f'已提取: {out_path}（共 {n_lines} 行，{len(cached)} 字符，{total} 页）（缓存命中，零解析复用）')
            print(f'读取: read(path="{out_path}")，用 offset/limit 分页（offset 是 markdown 行号，非 PDF 页码）')
            return

    pi = load_inspector()
    r = None
    if pi is not None:
        try:
            r = pi.process_pdf(file)
        except Exception as e:
            print(f'pdf-inspector 解析失败（{e}），尝试降级 pdftotext…', file=sys.stderr)
            r = None

    if cmd == 'classify':
        lines = [f'文件: {file}（{fmt_mb(size)}，{total} 页）']
        if r is not None:
            pdf_type = getattr(r, 'pdf_type', 'unknown')
            conf = getattr(r, 'confidence', None)
            conf_s = f'，置信度 {conf:.2f}' if isinstance(conf, (int, float)) else ''
            ms = getattr(r, 'processing_time_ms', None)
            ms_s = f'，{ms}ms' if isinstance(ms, (int, float)) else ''
            lines.append(f'类型: {pdf_type}{conf_s}（pdf-inspector{ms_s}）')
            markdown = getattr(r, 'markdown', None) or ''
            missing = list(getattr(r, 'pages_needing_ocr', None) or [])
            if markdown and not missing:
                lines.append('文本层: 完整')
                lines.extend(suggest_text(file))
            elif markdown:
                lines.append(f'文本层: 大部分有效，但{fmt_pages(missing, total)}无有效文本')
                lines.append('建议下一步（混合型）:')
                lines.append(f'  1. python3 {SCRIPT_DIR}/pdf.py extract "{file}"   ← 提取有效文本页')
                lines.append(f'  2. 缺失页按需渲染查看: bash {SCRIPT_DIR}/render.sh "{file}" --pages {",".join(map(str, missing[:4]))}')
            elif pdf_type == 'text_based':
                # 有文本层但提取为空 = 文本层损坏（乱码），等价扫描件
                lines.append(f'文本层: 存在但损坏（乱码），{fmt_pages(missing or all_pages(total), total)}不可用')
                lines.extend(suggest_scan(file))
            else:
                lines.append(f'文本层: 无（扫描图片，{total} 页全部需视觉阅读）')
                lines.extend(suggest_scan(file))
        else:
            kind = fallback_classify(file)
            if kind is None:
                lines.append('类型: 未知（依赖均不可用）')
                lines.append(INSTALL_HINT)
            elif kind == 'text':
                lines.append('类型: 文字型（pdftotext 降级判定）')
                lines.extend(suggest_text(file))
            else:
                lines.append('类型: 扫描型（pdftotext 降级判定）')
                lines.extend(suggest_scan(file))
        print('\n'.join(lines))
        return

    if cmd == 'extract':
        markdown = ''
        if r is not None:
            markdown = getattr(r, 'markdown', None) or ''
        else:
            txt = run(['pdftotext', '-layout', file, '-'])
            if txt is None:
                print(INSTALL_HINT)
                sys.exit(1)
            markdown = txt
        if not markdown.strip():
            # 空提取 = 无有效文本层（纯扫描件 / 乱码层 / 降级引擎空结果），等价扫描件路径。
            # 两条引擎路径统一在此拦截——否则会把空 .md 落盘，模型误读为"该 PDF 没内容"。
            missing = (list(getattr(r, 'pages_needing_ocr', None) or []) if r is not None else []) or all_pages(total)
            print(f'该 PDF 无有效文本层（{fmt_pages(missing, total)}），无法提取文字。')
            print('\n'.join(suggest_scan(file)))
            return

        out_path.write_text(markdown, encoding='utf-8')
        n_lines = markdown.count('\n') + 1
        print(f'已提取: {out_path}（共 {n_lines} 行，{len(markdown)} 字符，{total} 页）')
        print(f'读取: read(path="{out_path}")，用 offset/limit 分页（offset 是 markdown 行号，非 PDF 页码）')
        print('再生: 该文件被清理后重新运行本命令即可（自动重新提取）')
        if r is not None:
            missing = list(getattr(r, 'pages_needing_ocr', None) or [])
            if missing and len(missing) < total:
                print(f'注意: {fmt_pages(missing, total)}无有效文本层，如需这些页内容请 render 后 view:')
                print(f'  bash {SCRIPT_DIR}/render.sh "{file}" --pages {",".join(map(str, missing[:6]))}')
        return


if __name__ == '__main__':
    main()
