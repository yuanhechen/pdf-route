# pdf-route

PDF 读取路由 skill：把「读 PDF」这件事从模型的猜测变成确定性的脚本路由。遇到任何 .pdf 文件（含扫描件）时自动分类——文字型走文本提取，扫描型走渲染视觉阅读，混合型两条路都走。

遵循 [Agent Skills 规范](https://agentskills.io)，可用于 Claude Code、leow3bot 及任何兼容 agent。

## 安装

```bash
npx skills add https://github.com/yuanhechen/pdf-route -g
```

## 它解决什么问题

约一半 PDF 自带文本层（可直接提取，毫秒级），另一半是扫描图片（只能渲染成图看）。两者处理路径完全不同，模型猜错的代价是浪费整轮工具调用，更糟的是把扫描件硬提取成空文本后**编造内容**。本 skill 的第一步永远是脚本分类，输出末尾直接给出下一步命令，模型照做即可：

```
classify（分类）
 ├─ 文字型 → pdf.py extract → 单份 markdown，每页 <!-- Page N --> 页标记（N=物理页码）
 │            → read 分页读取；grep 页标记得页→行号映射，可定向跳页/按页引用
 ├─ 扫描型 → render.sh 渲染 PNG（一页一图）→ view 逐张查看（防幻觉：看图必须能引用原文）
 └─ 混合型 → 一份覆盖全部物理页的 markdown（有效页=文本，缺失页=占位符，缺口位置可见）
             + 缺失页按需渲染补看
```

## 特性

- **三路分类路由**：文本层完整 / 无 / 部分，各走各的最优路径
- **页结构保留**：文字型/混合型提取为带 `<!-- Page N -->` 页标记的单份 markdown——按页定位、按页引用、grep 得页→行号映射；混合型缺失页以占位符占位，缺口在阅读流中的位置直接可见（基于 pdf-inspector 的 `extract_pages_markdown` 逐页 API，同时避免聚合 API 对混合型不抑制坏 OCR 乱码的问题）
- **防幻觉自检**：扫描件看完第一张图必须引用原文，引不出即停止并告知用户，绝不凭常识编故事
- **内容寻址缓存**：提取与渲染产物按内容 hash 命名，同 PDF 重复处理零成本复用
- **工作目录隔离**：中间产物按项目分 scope 存放（`${TMPDIR}/leow3bot-pdf/<scope>/`），互不混放，7 天未动自动 GC
- **依赖 fail-fast**：classify/extract 只走 [pdf-inspector](https://pypi.org/project/pdf-inspector/)，缺失时直接报错给安装命令，**不静默降级**——降级会失去乱码检测与混合型逐页识别，重新引入本 skill 要防的「乱码当正文」事故

## 依赖

| 依赖 | 必需性 | 安装 |
|---|---|---|
| pdf-inspector | 必需（分类 + 提取，核心能力） | `python3 -m pip install --user pdf-inspector` |
| poppler-utils | 必需（渲染扫描页 + 页数检测） | `sudo apt install poppler-utils` / `brew install poppler` |
| Python ≥3.8 | 必需 | — |

> 依赖缺失时脚本 fail fast 并输出安装命令，不降级运行——静默降级产出的乱码误读比明确报错更危险。

## License

MIT
