# pdf-route

PDF 读取路由 skill：把「读 PDF」这件事从模型的猜测变成确定性的脚本路由。遇到任何 .pdf 文件（含扫描件）时自动分类——文字型走文本提取，扫描型走渲染视觉阅读，混合型两条路都走。

遵循 [Agent Skills 规范](https://agentskills.io)，可用于 Claude Code、leow3bot 及任何兼容 agent。

## 安装

```bash
npx skills add yuanhechen/pdf-route -g
```

## 它解决什么问题

约一半 PDF 自带文本层（可直接提取，毫秒级），另一半是扫描图片（只能渲染成图看）。两者处理路径完全不同，模型猜错的代价是浪费整轮工具调用，更糟的是把扫描件硬提取成空文本后**编造内容**。本 skill 的第一步永远是脚本分类，输出末尾直接给出下一步命令，模型照做即可：

```
classify（分类）
 ├─ 文字型 → pdf.py extract → markdown → read 分页读取（内容 hash 缓存，命中零解析）
 ├─ 扫描型 → render.sh 渲染 PNG → view 逐张查看（防幻觉：看图必须能引用原文）
 └─ 混合型 → 先提取有效文本页，缺失页按需渲染补看
```

## 特性

- **三路分类路由**：文本层完整 / 无 / 部分，各走各的最优路径
- **防幻觉自检**：扫描件看完第一张图必须引用原文，引不出即停止并告知用户，绝不凭常识编故事
- **内容寻址缓存**：提取与渲染产物按内容 hash 命名，同 PDF 重复处理零成本复用
- **工作目录隔离**：中间产物按项目分 scope 存放（`${TMPDIR}/leow3bot-pdf/<scope>/`），互不混放，7 天未动自动 GC
- **依赖自举**：优先 [pdf-inspector](https://pypi.org/project/pdf-inspector/)（结构化 markdown），未安装自动降级 pdftotext；缺失时输出安装指引

## 依赖

| 依赖 | 必需性 | 安装 |
|---|---|---|
| poppler-utils | 渲染扫描页必需 | `sudo apt install poppler-utils` / `brew install poppler` |
| pdf-inspector | 可选（提取质量更好） | `python3 -m pip install --user pdf-inspector` |
| Python ≥3.8 | 必需 | — |

## License

MIT
