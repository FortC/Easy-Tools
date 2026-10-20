# 图标来源与许可

本目录下的 `.svg` 文件全部来自 [lucide-static](https://github.com/lucide-icons/lucide)
v1.60.0 按需下载的子集，每个文件头部保留原始 `@license` 注释。

- 许可：ISC License（免费商用、可修改、可再分发，无需署名，保留许可声明即可）
- 原始仓库：<https://github.com/lucide-icons/lucide>
- 上游许可全文：<https://github.com/lucide-icons/lucide/blob/main/LICENSE>

## 补充图标

如需新增图标，从上游按同名下载即可（保持文件头 license 注释完整）：

```
curl -LO https://unpkg.com/lucide-static@1.60.0/icons/<name>.svg
```

代码中通过 `easy_tools/ui/icons.py` 的 `pixmap()/icon()/has_icon()` 使用，
颜色在渲染时注入（SVG 内是 `stroke="currentColor"`）。
