#!/usr/bin/env bash
# PDF 页渲染脚本（leow3bot pdf-route skill）：扫描型 PDF → PNG，供 view 查看。
# 用法: render.sh <file.pdf> --pages 1,3,5-8 [--dpi 150]
#
# 输出固定为 ${TMPDIR}/leow3bot-pdf/<工作目录scope>/<内容hash>-p<页码3位>[-d<dpi>].png——
# scope = 工作目录 basename + 完整路径 6 位 hash（与 pdf.py 规则一致）：
# 不同项目的中间产物互不混放、可整目录手动清理；同一文件同一页同一 DPI 永远
# 产出同一路径，已有 PNG 直接复用（不重复渲染）。非默认 DPI 在文件名附加
# -d<dpi>，避免 150 与 300 DPI 互相覆盖或误命中缓存。
# 7 天未动的陈旧产物在每次启动时自动 GC（先于本次渲染执行）。
set -euo pipefail

file="" pages="" dpi=150
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pages)
      if [[ $# -lt 2 ]]; then echo "缺少 --pages 的值" >&2; exit 1; fi
      pages="$2"; shift 2 ;;
    --dpi)
      if [[ $# -lt 2 ]]; then echo "缺少 --dpi 的值" >&2; exit 1; fi
      dpi="$2"; shift 2 ;;
    -h|--help)
      echo "用法: render.sh <file.pdf> --pages 1,3,5-8 [--dpi 150]"; exit 0 ;;
    *) file="$1"; shift ;;
  esac
done

if [[ -z "$file" || ! -f "$file" ]]; then
  echo "用法: render.sh <file.pdf> --pages 1,3,5-8 [--dpi 150]" >&2
  exit 1
fi

# 先校验 DPI：非数字会在下面算术里报错，超大值会渲染出巨图；统一给出可行动报错
if ! [[ "$dpi" =~ ^[0-9]+$ ]] || (( 10#$dpi < 10 || 10#$dpi > 1200 )); then
  echo "无效 --dpi: $dpi（应为 10-1200 的整数）" >&2
  exit 1
fi
dpi=$((10#$dpi))

if ! command -v pdftoppm >/dev/null 2>&1; then
  echo "缺少依赖 poppler-utils（渲染 PDF 页需要）。安装:" >&2
  echo "  Debian/WSL: sudo apt install poppler-utils" >&2
  echo "  macOS:      brew install poppler" >&2
  exit 1
fi

# 绝对路径化用 python3 realpath（本 skill 硬依赖）——GNU readlink -f 在 macOS 不存在
file="$(python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$file")"
# || true 防 set -euo pipefail 在 pdfinfo 失败（损坏/加密）时静默退出——统一走下方校验
total=$(pdfinfo "$file" 2>/dev/null | awk '/^Pages:/{print $2}' || true)
if ! [[ "$total" =~ ^[0-9]+$ ]] || (( 10#$total < 1 )); then
  echo "无法解析 PDF（可能损坏或加密）: $file" >&2
  exit 1
fi
total=$((10#$total))
# sha256: GNU coreutils 的 sha256sum 优先，macOS 用自带的 shasum -a 256 兜底
hash=$({ sha256sum "$file" 2>/dev/null || shasum -a 256 "$file"; } | cut -c1-12 || true)
if [[ -z "$hash" ]]; then
  echo "缺少 sha256sum/shasum（内容 hash 需要）" >&2
  exit 1
fi

# scope 子目录：basename 可辨认 + 完整路径 6 位 hash 防同名目录冲突。
# 用 pwd -P（物理路径）与 pdf.py 的 os.getcwd() 对齐，经符号链接进入时也一致。
root="${TMPDIR:-/tmp}/leow3bot-pdf"
phys="$(pwd -P)"
scope="$(basename "$phys")"; scope="${scope:0:32}"
outdir="$root/${scope}-$(printf '%s' "$phys" | { sha256sum 2>/dev/null || shasum -a 256; } | cut -c1-6)"
mkdir -p -m 700 "$root" "$outdir" 2>/dev/null || mkdir -p "$root" "$outdir"
chmod 700 "$root" "$outdir" 2>/dev/null || true

# 老化 GC：删 7 天未动的文件；空 scope 目录也须闲置 7 天才删——并发渲染/提取
# 可能刚建好目录尚未落盘，无差别删除会让对方写失败（旧版此处会误删新目录）。
find "$root" -type f -mtime +7 -delete 2>/dev/null || true
find "$root" -mindepth 1 -type d -empty -mtime +7 -delete 2>/dev/null || true

# 展开页码列表: "1,3,5-8" → 1 3 5 6 7 8（去空白；倒序/非法范围告警跳过）
expand_pages() {
  local IFS=','
  local part start end
  for part in $1; do
    part="${part//[[:space:]]/}"
    [[ -z "$part" ]] && continue
    if [[ "$part" == *-* ]]; then
      start="${part%-*}"
      end="${part#*-}"
      if [[ "$start" =~ ^[0-9]+$ && "$end" =~ ^[0-9]+$ ]]; then
        start=$((10#$start)); end=$((10#$end))
        if (( start <= end )); then
          seq "$start" "$end"
        else
          echo "跳过无效页码范围: $part（起始页大于结束页，共 $total 页）" >&2
        fi
      else
        echo "跳过无效页码范围: $part（共 $total 页）" >&2
      fi
    else
      echo "$part"
    fi
  done
}

if [[ -z "$pages" ]]; then
  echo "未指定 --pages。扫描件勿全量渲染——先渲染 1-3 页定位，再按需定向渲染:" >&2
  echo "  render.sh \"$file\" --pages 1-3" >&2
  exit 1
fi

# 先逐项校验再排序去重：若先 sort -n -u，非法 token（如 "1x" 的数值键与 "1" 相同）
# 可能顶掉合法页码，导致该页被静默跳过
valid_pages=""
while IFS= read -r token; do
  token="${token//[[:space:]]/}"
  [[ -z "$token" ]] && continue
  # 钳制页码范围，防模型传 0/负数/超界
  if ! [[ "$token" =~ ^[0-9]+$ ]]; then
    echo "跳过无效页码: $token（共 $total 页）" >&2
    continue
  fi
  p=$((10#$token))
  if (( p < 1 || p > total )); then
    echo "跳过无效页码: $p（共 $total 页）" >&2
    continue
  fi
  valid_pages+=" $p"
done < <(expand_pages "$pages")

page_list=()
while IFS= read -r p; do
  [[ -z "$p" ]] && continue
  page_list+=("$p")
done < <(for p in $valid_pages; do echo "$p"; done | sort -n -u)

if [[ ${#page_list[@]} -eq 0 ]]; then
  echo "没有有效页码可渲染（共 $total 页）" >&2
  exit 1
fi

dpi_suffix=""
if (( dpi != 150 )); then
  dpi_suffix="-d${dpi}"
fi

page_png() {
  printf '%s/%s-p%03d%s.png\n' "$outdir" "$hash" "$1" "$dpi_suffix"
}

# 从本次临时前缀里找出指定页的产物（页码后缀由 pdftoppm 按总页数补零，不能硬编码宽度）
find_tmp_png() {
  local p=$1 f num_part num
  for f in "$outdir/$tmp"*.png; do
    [[ -e "$f" ]] || continue
    num_part="${f##*-}"
    num=$((10#${num_part%.png}))
    if (( num == p )) && [[ -s "$f" ]]; then
      echo "$f"
      return 0
    fi
  done
  return 1
}

# 把批量渲染产生的临时页搬到最终路径；范围内非空文件才收，其余清掉防误用
collect_tmp() {
  local start=$1 end=$2 f num_part num dst
  for f in "$outdir/$tmp"*.png; do
    [[ -e "$f" ]] || continue
    num_part="${f##*-}"
    num=$((10#${num_part%.png}))
    if (( num < start || num > end )) || [[ ! -s "$f" ]]; then
      rm -f -- "$f"
      continue
    fi
    dst="$(page_png "$num")"
    if ! mv -f "$f" "$dst"; then
      rm -f -- "$f"
    fi
  done
}

# 单页渲染 + 失败重试 1 次（WSL drvfs 瞬断、杀软扫描锁文件等）；报错带 pdftoppm 原始信息
render_one() {
  local p=$1 dst f err
  dst="$(page_png "$p")"
  err=$(pdftoppm -r "$dpi" -png -f "$p" -l "$p" "$file" "$outdir/$tmp" 2>&1 >/dev/null) || true
  f="$(find_tmp_png "$p")" || true
  if [[ -z "$f" ]]; then
    err=$(pdftoppm -r "$dpi" -png -f "$p" -l "$p" "$file" "$outdir/$tmp" 2>&1 >/dev/null) || true
    f="$(find_tmp_png "$p")" || true
  fi
  if [[ -z "$f" ]]; then
    echo "第 $p 页渲染失败${err:+（pdftoppm: ${err//$'\n'/；}）}" >&2
    return 1
  fi
  if ! mv -f "$f" "$dst"; then
    echo "第 $p 页落盘失败: $dst" >&2
    return 1
  fi
}

# 连续页批量渲染：一次 pdftoppm 调用只解析一次 PDF（逐页调用会每页重解析整本，
# 大文件上差距明显）。批量失败/漏页再逐页重试，保留单页故障隔离。
render_batch() {
  local start=$1 end=$2 p
  pdftoppm -r "$dpi" -png -f "$start" -l "$end" "$file" "$outdir/$tmp" >/dev/null 2>&1 || true
  collect_tmp "$start" "$end"
  for ((p = start; p <= end; p++)); do
    if [[ ! -s "$(page_png "$p")" ]]; then
      render_one "$p" || true
    fi
  done
}

# tmp 前缀带 PID：同一 PDF 并发渲染（模型并行调用/重试）时两进程 hash 相同，
# 共享前缀会互删互抢对方的 tmp 文件——稍慢的一方全页失败；带 PID 后各自独立。
# 最终都 mv 到相同的 pNNN.png，rename 覆盖幂等且内容一致，无碍
tmp="${hash}-tmp$$"
# 清掉本进程 PID 前缀残留 + 旧版（无 PID）前缀的陈旧 tmp，防止下方收集误取旧页
rm -f -- "$outdir/$tmp"*.png "$outdir/${hash}-tmp-"*.png 2>/dev/null || true

# 已有同内容同页同 DPI 的 PNG 直接复用（重复 render 零成本）；只渲染缺失页
missing=()
reused=0
for p in "${page_list[@]}"; do
  if [[ -s "$(page_png "$p")" ]]; then
    reused=$((reused + 1))
  else
    missing+=("$p")
  fi
done

i=0
while (( i < ${#missing[@]} )); do
  start=${missing[$i]}
  j=$i
  while (( j + 1 < ${#missing[@]} )) && (( missing[$((j + 1))] == missing[$j] + 1 )); do
    j=$((j + 1))
  done
  end=${missing[$j]}
  if (( start == end )); then
    render_one "$start" || true
  else
    render_batch "$start" "$end"
  fi
  i=$((j + 1))
done

# 以最终文件是否存在为准汇总（批量成功但个别页缺失也在这里暴露）
rendered=()
failed=()
for p in "${page_list[@]}"; do
  dst="$(page_png "$p")"
  if [[ -s "$dst" ]]; then
    rendered+=("$dst")
  else
    failed+=("$p")
  fi
done

if [[ ${#rendered[@]} -eq 0 ]]; then
  echo "没有渲染出任何页面（共 $total 页）" >&2
  exit 1
fi

echo "已就绪 ${#rendered[@]} 页（${dpi} DPI，共 $total 页）:"
for f in "${rendered[@]}"; do
  echo "  $f"
done
if (( reused > 0 )); then
  echo "其中 $reused 页复用已有 PNG（同文件同页同分辨率不重复渲染）。"
fi
if (( ${#failed[@]} > 0 )); then
  echo "有 ${#failed[@]} 页渲染失败: ${failed[*]}" >&2
fi
echo "用 view 逐张查看（每轮 ≤2 张可获最高清晰度）。"
echo "同一页重复查看直接 view 以上路径（同分辨率重复运行不再渲染）。"
