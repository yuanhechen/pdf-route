#!/usr/bin/env bash
# PDF 页渲染脚本（leow3bot pdf-route skill）：扫描型 PDF → PNG，供 view 查看。
# 用法: render.sh <file.pdf> --pages 1,3,5-8 [--dpi 150]
#
# 输出固定为 ${TMPDIR}/leow3bot-pdf/<工作目录scope>/<内容hash>-p<页码3位>.png——
# scope = 工作目录 basename + 完整路径 6 位 hash（与 pdf.py 规则一致）：
# 不同项目的中间产物互不混放、可整目录手动清理；同一目录下同一文件同一页
# 永远产出同一路径（重复渲染直接覆盖，模型重复查看直接 view 已有 PNG）。
# 7 天未动的陈旧产物在每次启动时自动 GC（先于本次渲染执行）。
set -euo pipefail

file="" pages="" dpi=150
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pages) pages="$2"; shift 2 ;;
    --dpi)   dpi="$2";   shift 2 ;;
    *)       file="$1";  shift ;;
  esac
done

if [[ -z "$file" || ! -f "$file" ]]; then
  echo "用法: render.sh <file.pdf> --pages 1,3,5-8 [--dpi 150]" >&2
  exit 1
fi

if ! command -v pdftoppm >/dev/null 2>&1; then
  echo "缺少依赖 poppler-utils（渲染 PDF 页需要）。安装:" >&2
  echo "  Debian/WSL: sudo apt install poppler-utils" >&2
  echo "  macOS:      brew install poppler" >&2
  exit 1
fi

file="$(readlink -f "$file")"
# || true 防 set -euo pipefail 在 pdfinfo 失败（损坏/加密）时静默退出——统一走下方校验
total=$(pdfinfo "$file" 2>/dev/null | awk '/^Pages:/{print $2}' || true)
if ! [[ "$total" =~ ^[0-9]+$ ]]; then
  echo "无法解析 PDF（可能损坏或加密）: $file" >&2
  exit 1
fi
hash=$(sha256sum "$file" | cut -c1-12)

# scope 子目录：basename 可辨认 + 完整路径 6 位 hash 防同名目录冲突。
# 用 pwd -P（物理路径）与 pdf.py 的 os.getcwd() 对齐，经符号链接进入时也一致。
root="${TMPDIR:-/tmp}/leow3bot-pdf"
phys="$(pwd -P)"
scope="$(basename "$phys")"; scope="${scope:0:32}"
outdir="$root/${scope}-$(printf '%s' "$phys" | sha256sum | cut -c1-6)"
mkdir -p -m 700 "$root" "$outdir" 2>/dev/null || mkdir -p "$root" "$outdir"
chmod 700 "$root" "$outdir" 2>/dev/null || true

# 老化 GC：删 7 天未动的陈旧文件，再移除清空后的 scope 目录（7 天内的产物不动）
find "$root" -type f -mtime +7 -delete 2>/dev/null || true
find "$root" -mindepth 1 -type d -empty -delete 2>/dev/null || true

# 展开页码列表: "1,3,5-8" → 1 3 5 6 7 8
expand_pages() {
  local IFS=','
  for part in $1; do
    if [[ "$part" == *-* ]]; then
      seq "${part%-*}" "${part#*-}"
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

rendered=()
# 清掉上次中途失败可能残留的 tmp 产物，防止下方 ls 误取旧页当成本页
rm -f -- "$outdir/${hash}-tmp"*.png 2>/dev/null || true
for p in $(expand_pages "$pages"); do
  # 钳制页码范围，防模型传 0/负数/超界
  if ! [[ "$p" =~ ^[0-9]+$ ]]; then
    echo "跳过无效页码: $p（共 $total 页）" >&2
    continue
  fi
  p=$((10#$p))  # 归一前导零（如 008），防 bash 算术按八进制解析报错
  if (( p < 1 || p > total )); then
    echo "跳过无效页码: $p（共 $total 页）" >&2
    continue
  fi
  pdftoppm -r "$dpi" -png -f "$p" -l "$p" "$file" "$outdir/${hash}-tmp" >/dev/null 2>&1
  # pdftoppm 输出带总页数宽度的后缀（如 -tmp-01.png），统一重命名为 p001 风格
  tmpfile=$(ls "$outdir/${hash}-tmp"*.png 2>/dev/null | head -1)
  [[ -z "$tmpfile" ]] && { echo "第 $p 页渲染失败" >&2; continue; }
  dst="$outdir/${hash}-p$(printf '%03d' "$p").png"
  mv -f "$tmpfile" "$dst"
  rendered+=("$dst")
done

if [[ ${#rendered[@]} -eq 0 ]]; then
  echo "没有渲染出任何页面" >&2
  exit 1
fi

echo "已渲染 ${#rendered[@]} 页（${dpi} DPI，共 $total 页）:"
for f in "${rendered[@]}"; do
  echo "  $f"
done
echo "用 view 逐张查看（每轮 ≤2 张可获最高清晰度）。"
echo "同一页重复查看直接 view 以上路径（重新渲染也是同一路径）。"
