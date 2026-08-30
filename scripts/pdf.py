#!/usr/bin/env python3
# PDF 分类/提取脚本（leow3bot pdf-route skill）。
# 用法:
#   pdf.py classify <file.pdf>   逐页检测 + 路由建议；文字型/混合型顺手完成提取并预热缓存
#   pdf.py extract  <file.pdf>   提取 → markdown 落盘（hash 缓存，命中零解析；md 被清理后再生用）
#
# 事实来源：pdf-inspector 的逐页 API extract_pages_markdown（缺失时直接报错并给安装
# 命令，不静默降级——降级会失去乱码文本层检测与混合型逐页识别）。classify 与
# extract 读同一 API，结论永不打架。为什么 classify 不用 process_pdf 的聚合结论路由：
# 其文档级 gate 会把「字少图多」的 PDF（幻灯片导出、图多字少的报告）整本判成
# image_based——聚合 markdown 整个丢弃、全部页标记需 OCR，可提取的文本因此被引导去
# 逐页看图；实测逐页 API 与聚合 API 成本相同（毫秒级），没有理由不逐页先行。
# 渲染扫描页用 render.sh（poppler）；页数检测用 pdfinfo（同 poppler-utils）。
# 输出面向模型：末尾必带「建议下一步」，模型照做即可，无需自行推理路由。
#
# 页结构：extract_pages_markdown 逐页提取，页间插 <!-- Page N --> 标记（N 为 PDF
# 物理页码，与 pdf-inspector 原生格式一致）；无文本层页留占位符——缺口在阅读流中
# 的位置直接可见，混合型因此获得单一页序产物（有效页是文本、缺失页是占位符，按需
# 渲染补看）。
#
# classify 顺手预热缓存：文字型/混合型检测完直接把 md 落盘，建议下一步从「先
# extract 再 read」缩短为「直接 read」；对同一文件，classify/extract 全流程只做
# 一次逐页解析（再次 classify / 后续 extract 均缓存命中）。
#
# 含图提示：图片里的文字（图表数值、截图内容）是像素，不在任何文本提取结果里。
# 对「有文本层但页面上有大图」的页用 pdfimages 检测并提示——模型据此决定是否渲染
# 该页视觉补看（渲染出的 PNG 同时含原生文字与图内文字）。
#
# 输出布局: ${TMPDIR}/leow3bot-pdf/<工作目录scope>/<内容hash>-pg.md——scope 为工作
# 目录 basename + 完整路径 6 位 hash（与 render.sh 规则一致）：不同项目互不混放、
# 可整目录手动清理；7 天未动的陈旧产物在每次运行时自动 GC。

from __future__ import annotations  # 兼容 Python ≤3.9：延迟求值注解（str | None 等 3.10+ 语法）

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get('TMPDIR', '/tmp')) / 'leow3bot-pdf'
SCRIPT_DIR = Path(__file__).resolve().parent
GC_MAX_AGE_DAYS = 7
IMG_AREA_RATIO = 0.15  # 图像显示面积占页面比例 ≥ 此值视为「大图」（半页图/截图级；小 logo 不触发）


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


def require_pages_api():
    """classify/extract 共用的逐页 API 入口：依赖缺失或版本过旧时 fail fast。"""
    pi = load_inspector()
    if pi is None:
        print(INSTALL_HINT, file=sys.stderr)
        sys.exit(1)
    if not hasattr(pi, 'extract_pages_markdown'):
        print(UPGRADE_HINT, file=sys.stderr)
        sys.exit(1)
    return pi


INSTALL_HINT = ('依赖缺失: pdf-inspector 未安装（本 skill 不做降级，装好再跑）。安装:\n'
                '  python3 -m pip install --user pdf-inspector\n'
                '  conda/venv 环境去掉 --user；pip 报 externally-managed-environment 则加 --break-system-packages\n'
                '  已装仍报此错时，跑 python3 -c "import pdf_inspector" 看真实报错')

UPGRADE_HINT = ('pdf-inspector 版本过旧（缺逐页提取 API extract_pages_markdown，classify/extract 均依赖）。升级:\n'
                '  python3 -m pip install -U pdf-inspector')


def q(file: str) -> str:
    """路径进 shell 建议命令的安全引用——文件名含空格/$/引号也不会断。"""
    return shlex.quote(file)

PARSE_ERR = 'pdf-inspector 解析失败: {e}\n（文件可能损坏、加密或暂不支持；确为有效 PDF 可尝试 pip install -U pdf-inspector 后重试）'


def fmt_pages(pages: list, total: int) -> str:
    if not pages:
        return '无'
    if len(pages) == total:
        return f'全部 {total} 页'
    if len(pages) > 8:
        return f'第 {pages[0]}-{pages[-1]} 页等 {len(pages)} 页'
    return f'第 {",".join(map(str, pages))} 页'


def suggest_scan(file: str) -> list:
    return ['建议下一步（按扫描件处理）:',
            f'  bash {SCRIPT_DIR}/render.sh {q(file)} --pages 1-3',
            '  然后用 view 逐张查看 PNG（先看目录/首页定位，勿全量翻页）']


def all_pages(total: int) -> list:
    return list(range(1, total + 1))


# ---------------------------------------------------------------------------
# 页结构拼接：extract 产物是「一份覆盖全部物理页的 markdown，每页以
# <!-- Page N --> 开头」。无文本层页留占位符而非跳过——页码不漂移、
# 混合型的缺口位置在阅读流中直接可见。
# ---------------------------------------------------------------------------

PAGE_NO_TEXT = '（本页无有效文本层，需渲染后视觉查看）'


def page_marker(n: int) -> str:
    # 与 pdf-inspector 原生 include_page_numbers 输出格式一致（convert.rs: <!-- Page N -->）
    return f'<!-- Page {n} -->'


def join_paged(result) -> tuple:
    """extract_pages_markdown 逐页结果 → 带页标记全文。

    PageMarkdown.page 为 0 基；needs_ocr 页 markdown 已被上游置空，这里统一落占位符。
    返回 (markdown, 有效页列表, 无文本层页列表)，页码均为 1 基物理页码。"""
    parts, valid, ocr = [], [], []
    for p in result.pages:
        n = p.page + 1
        md = (getattr(p, 'markdown', None) or '').strip()
        if p.needs_ocr or not md:
            ocr.append(n)
            parts.append(f'{page_marker(n)}\n{PAGE_NO_TEXT}')
        else:
            valid.append(n)
            parts.append(f'{page_marker(n)}\n{md}')
    return '\n\n'.join(parts), valid, ocr


def ocr_reasons(pe, ocr_pages: list) -> set:
    """无文本层页的 OCR 原因（如 scanned / 乱码字体）——区分「纯图」与「乱码层」用。"""
    ocr_set = set(ocr_pages)
    rs = set()
    for pr in (getattr(pe, 'ocr_reasons_by_page', None) or []):
        if getattr(pr, 'page', None) in ocr_set and getattr(pr, 'reasons', None):
            rs.update(pr.reasons)
    return rs


def layout_pages_of(pe) -> list:
    """多栏/表格页（老版本无此属性则返回空）——提示模型这些页的提取阅读顺序可能错乱。"""
    return sorted({*(getattr(pe, 'pages_with_columns', None) or []),
                   *(getattr(pe, 'pages_with_tables', None) or [])})


def recover_from_cache(cached: str, total: int):
    """从缓存 md 还原 (有效页, 无文本层页)；页标记与总页数不符视为脏缓存返回 None。"""
    markers = [int(m) for m in re.findall(r'<!-- Page (\d+) -->', cached)]
    if sorted(markers) != all_pages(total):
        return None
    ocr = {int(m) for m in re.findall(r'<!-- Page (\d+) -->\n' + re.escape(PAGE_NO_TEXT), cached)}
    return [p for p in markers if p not in ocr], sorted(ocr)


def page_areas(file: str, total: int) -> dict:
    """每页面积（点²）。pdfinfo -f/-l 逐页输出 size 行，混合尺寸文档也正确。"""
    out = run(['pdfinfo', '-f', '1', '-l', str(total), file])
    areas = {}
    if out:
        for m in re.finditer(r'^Page\s+(\d+)\s+size:\s+([\d.]+)\s+x\s+([\d.]+)', out, re.M):
            areas[int(m.group(1))] = float(m.group(2)) * float(m.group(3))
    return areas


def big_image_pages(file: str, total: int, candidate_pages: list) -> list:
    """「有文本层但含大图」的页：图内文字不在提取结果里，值得提示模型按需渲染。

    pdfimages -list 列出每页图片的像素尺寸与有效 ppi，显示面积 = 像素/ppi*72（点）；
    页内多图累加，占页面面积 ≥ IMG_AREA_RATIO 视为大图。只在有文本层的页里挑——
    无文本层页本来就走渲染，提示是冗余。右锚定解析 x-ppi/y-ppi，避开 object ID
    列的歧义。"""
    if not candidate_pages:
        return []
    out = run(['pdfimages', '-list', file])
    if not out:
        return []
    areas = page_areas(file, total)
    cov = {}
    for line in out.splitlines():
        t = line.split()
        if len(t) < 15 or not t[0].isdigit() or t[2] != 'image':
            continue
        try:
            w, h, xd, yd = float(t[3]), float(t[4]), float(t[-4]), float(t[-3])
        except ValueError:
            continue
        if xd <= 0 or yd <= 0:
            continue
        page = int(t[0])
        cov[page] = cov.get(page, 0.0) + (w / xd * 72.0) * (h / yd * 72.0)
    hits = []
    for p in candidate_pages:
        a = areas.get(p)
        if a and cov.get(p, 0.0) / a >= IMG_AREA_RATIO:
            hits.append(p)
    return sorted(hits)


def emit_page_hints(file: str, total: int, valid: list, layout_pages: list) -> None:
    """有文本层页的两条补充提示（classify 预提取与 extract 都要打，谁在打谁负责）。"""
    img_pages = big_image_pages(file, total, valid)
    if img_pages:
        print(f'提示: {fmt_pages(img_pages, total)}有文本层但含大图——图内文字（图表数值/截图内容）不在提取结果中，涉及时渲染该页:')
        print(f'  bash {SCRIPT_DIR}/render.sh {q(file)} --pages {",".join(map(str, img_pages[:6]))}')
    if layout_pages:
        print(f'提示: {fmt_pages(layout_pages, total)}为多栏/表格布局，若提取文本的阅读顺序可疑，渲染该页复核')


# ---------------------------------------------------------------------------
# classify：逐页检测 → 按页构成路由（全有效=文字型 / 全缺失=扫描型 / 部分=混合型），
# 文字型/混合型顺手落盘预热缓存。缓存命中时零解析直接复用。
# ---------------------------------------------------------------------------

def do_classify(file: str, size: int, total: int, out_path: Path) -> None:
    t0 = time.time()
    md_text, valid, ocr, layout_pages, reasons = None, None, None, [], set()
    note = ''
    if out_path.exists():
        cached = out_path.read_text(encoding='utf-8')
        got = recover_from_cache(cached, total)
        if got is not None:
            md_text, valid, ocr = cached, got[0], got[1]
            note = '（缓存命中，零解析复用）'
    if valid is None:
        pi = require_pages_api()
        try:
            pe = pi.extract_pages_markdown(file)
        except Exception as e:
            print(PARSE_ERR.format(e=e), file=sys.stderr)
            sys.exit(1)
        md_text, valid, ocr = join_paged(pe)
        layout_pages = layout_pages_of(pe)
        reasons = ocr_reasons(pe, ocr)
        note = f'（pdf-inspector 逐页检测，{int((time.time() - t0) * 1000)}ms）'
        if valid:
            out_path.write_text(md_text, encoding='utf-8')  # 预热缓存：后续 extract 必命中

    lines = [f'文件: {file}（{fmt_mb(size)}，{total} 页）']
    if valid and not ocr:
        n_lines = md_text.count('\n') + 1
        lines.append(f'类型: 文字型——全部 {total} 页有文本层{note}')
        lines.append(f'已提取: {out_path}（共 {n_lines} 行，{len(md_text)} 字符）')
        lines.append('建议下一步（文字型）:')
        lines.append(f'  直接 read(path="{out_path}")，用 offset/limit 分页（offset 是 markdown 行号）')
        lines.append('  页结构: 每页以 <!-- Page N --> 开头（N=PDF 物理页码）；grep -n "<!-- Page" 该文件可得页→行号映射')
    elif valid:
        n_lines = md_text.count('\n') + 1
        lines.append(f'类型: 混合型——{fmt_pages(valid, total)}有文本层，{fmt_pages(ocr, total)}无（需视觉阅读）{note}')
        lines.append(f'已提取: {out_path}（共 {n_lines} 行，{len(md_text)} 字符；无文本层页为占位符，位置即阅读顺序）')
        lines.append('建议下一步（混合型）:')
        lines.append(f'  1. read(path="{out_path}") 读有效页文本（页结构同上，grep 页标记可定向跳页）')
        lines.append(f'  2. 缺页按需渲染: bash {SCRIPT_DIR}/render.sh {q(file)} --pages {",".join(map(str, ocr[:4]))}')
    else:
        reason_s = f'，原因: {",".join(sorted(reasons))}' if reasons else ''
        lines.append(f'类型: 扫描型——{total} 页全部无有效文本层{reason_s}{note}')
        lines.extend(suggest_scan(file))
    print('\n'.join(lines))
    if valid:
        emit_page_hints(file, total, valid, layout_pages)
        print(f'再生: 该文件被清理后重新运行本命令或 pdf.py extract 即可（自动重新提取）')


def do_extract(file: str, total: int, out_path: Path) -> None:
    # 缓存先行：命中则零解析直接返回——逐页解析只在 miss 时发生
    if out_path.exists():
        cached = out_path.read_text(encoding='utf-8')
        n_lines = cached.count(chr(10)) + 1
        print(f'已提取: {out_path}（共 {n_lines} 行，{len(cached)} 字符，{total} 页）（缓存命中，零解析复用）')
        print(f'读取: read(path="{out_path}")，用 offset/limit 分页（offset 是 markdown 行号）')
        print(f'页结构: 每页以 <!-- Page N --> 开头（N=PDF 物理页码）；grep -n "<!-- Page" 该文件可得页→行号映射')
        got = recover_from_cache(cached, total)
        # 占位符页码从缓存内容还原，混合型缓存命中也能看到缺页指引
        ocr_cached = got[1] if got else [int(m) for m in re.findall(r'<!-- Page (\d+) -->\n' + re.escape(PAGE_NO_TEXT), cached)]
        valid_cached = got[0] if got else []
        emit_page_hints(file, total, valid_cached, [])
        if ocr_cached:
            print(f'无文本层页: {fmt_pages(ocr_cached, total)}——md 中为占位符，需内容时用 render.sh 渲染')
        print('再生: 该文件被清理后重新运行本命令即可（自动重新提取）')
        return

    pi = require_pages_api()
    try:
        pe = pi.extract_pages_markdown(file)
    except Exception as e:
        print(PARSE_ERR.format(e=e), file=sys.stderr)
        sys.exit(1)
    markdown, valid, ocr = join_paged(pe)
    layout_pages = layout_pages_of(pe)
    if not valid:
        # 零有效页 = 无文本层（纯扫描件 / 乱码层），等价扫描件路径。统一在此
        # 拦截——否则会把只有占位符的 .md 落盘，模型误读为"该 PDF 没内容"。
        print(f'该 PDF 无有效文本层（{fmt_pages(ocr or all_pages(total), total)}），无法提取文字。')
        print('\n'.join(suggest_scan(file)))
        return

    out_path.write_text(markdown, encoding='utf-8')
    n_lines = markdown.count('\n') + 1
    print(f'已提取: {out_path}（共 {n_lines} 行，{len(markdown)} 字符，{total} 页中 {len(valid)} 页有效）')
    print(f'读取: read(path="{out_path}")，用 offset/limit 分页（offset 是 markdown 行号）')
    print(f'页结构: 每页以 <!-- Page N --> 开头（N=PDF 物理页码），按页定位/引用；grep -n "<!-- Page" 该文件可得页→行号映射')
    emit_page_hints(file, total, valid, layout_pages)
    if ocr:
        print(f'无文本层页: {fmt_pages(ocr, total)}——md 中已留占位符（位置即阅读顺序），需内容时渲染:')
        print(f'  bash {SCRIPT_DIR}/render.sh {q(file)} --pages {",".join(map(str, ocr[:6]))}')
    print('再生: 该文件被清理后重新运行本命令即可（自动重新提取）')


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
        # 区分「依赖缺失」与「文件本身问题」，报错才可行动
        if shutil.which('pdfinfo') is None:
            print('缺少依赖 poppler-utils（pdfinfo，页数检测需要）。安装:\n'
                  '  Debian/WSL: sudo apt install poppler-utils\n'
                  '  macOS:      brew install poppler', file=sys.stderr)
        else:
            print(f'无法解析 PDF（可能损坏或加密）: {file}', file=sys.stderr)
        sys.exit(1)

    out_path = ensure_dirs() / f'{file_digest(file)}-pg.md'
    if cmd == 'classify':
        do_classify(file, size, total, out_path)
    else:
        do_extract(file, total, out_path)


if __name__ == '__main__':
    main()
