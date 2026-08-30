---
name: pdf-route
description: 读取和分析 PDF 文件（含扫描件）。只要遇到 .pdf 文件需要读取、总结、搜索、提取内容或查看页面，就必须使用本 skill——包括用户直接给出 PDF 路径、提到 PDF/剧本/卷宗/扫描件/合同/论文/手册，或任务内容明显存放在 PDF 中时，即使没有明说"读取 PDF"也要用。自动区分文字型与扫描型：文字型提取为 markdown 供 read 分页读取，扫描型渲染为图片供 view 查看。
---

# PDF 处理

PDF 是二进制格式——直接 cat/head 只会得到乱码或空白，一切读取都从下面的分类开始。

## 第一步：分类（必做，勿跳过）

首次使用先确保依赖就位（已安装则秒过）：

```bash
python3 -c "import pdf_inspector" 2>/dev/null || python3 -m pip install --user pdf-inspector
```

然后分类：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/pdf.py classify "$ARGUMENTS"
```

分类结果末尾有「建议下一步」，**直接照做即可**——它已经根据文件类型给出了正确的命令。为什么要先分类：约一半 PDF 自带文本层（可直接提取为文本，毫秒级、零损耗），另一半是扫描图片（只能视觉阅读），两者处理路径完全不同，猜错的代价是浪费一整轮工具调用。

## 路径 A：文字型 → 提取 + read

分类建议提取时：

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/pdf.py extract "$ARGUMENTS"
```

产出 markdown 文件并回报路径，用 read 分页读取（offset/limit 续读）。三点注意：

- 产物保留页结构：每页以 `<!-- Page N -->` 开头（N = PDF 物理页码）。「第 N 页说了什么」、按页码引用、章节定位都以此为准；`grep -n '<!-- Page' 文件` 可得页→行号映射，再用 offset 定向 read
- read 的 offset 是 **markdown 行号，不是 PDF 页码**——换算靠 grep 页标记，不要心算
- 同一 PDF 重复 extract 直接复用已提取的文件（按内容 hash 缓存），不会重复耗时

## 路径 B：扫描型 → 渲染 + view

分类判定为扫描型（或文本层损坏不可用）时，先明确本次要找什么（任务本身可推断则不必问用户），再按需渲染，**勿全量翻页**——每页都是一次图片加载：

```bash
${CLAUDE_SKILL_DIR}/scripts/render.sh "$ARGUMENTS" --pages 1-3
```

产出 PNG 路径列表，用 view 逐张查看（没有 view 工具的环境用 Read 读图片文件）。使用要点：

- **看图自检（防幻觉，最重要）**：看完第一张页图后，先在心里引用一句页面上的原文。引用不出来 = 你实际没看到图（工具可能只返回了链接或报错）——此时必须停止并告知用户「无法查看图片内容」，绝不能凭文件名、标题或常识推测剧情。扫描件的"内容"只存在于图像里，编造的故事看起来可能很合理，但全是假的。
- 找内容先看目录页/首页（通常 1-3 页）定位，再定向渲染目标页，而不是从第 1 页翻到最后一页
- 每轮 view ≤2 张可获得最高清晰度；一次看多张会被像素预算摊薄降采样，小字可能糊
- 同一页重复查看直接 view 已有 PNG（路径重渲染也相同），无需重新 render；若某页图像打不开或内容明显异常（空白/花屏），重新 render 该页一次再试

## 路径 C：混合型

分类显示部分页无文本层时：先 extract——产物是一份覆盖**全部物理页**的 markdown，有效页是正常文本，无文本层页是「（本页无有效文本层…）」占位符，位置即缺口在阅读顺序中的位置（页码不漂移）。缺的页按路径 B 按需渲染补看；占位符只是标记，不代表该页没有内容。

## 依赖与报错

- classify/extract **只走 pdf-inspector，不做降级**：缺失时脚本直接报错并给出安装命令（第一步已自检；pip 报 externally-managed-environment 就加 `--break-system-packages`），装好再跑。不降级的原因：降级会失去乱码文本层检测与混合型逐页识别，恰好重新引入本 skill 要防的「乱码当正文」事故，宁可 fail fast
- 渲染扫描页依赖 poppler-utils（`sudo apt install poppler-utils` / `brew install poppler`），页数检测（pdfinfo）同包
- 损坏/加密 PDF 会在 classify/extract 阶段明确报错——按报错处理（换文件或解密），不要绕路重试
