# Changelog

本项目所有显著变更均记录于此，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.2.0] - 2026-10-21

### Changed

- 全部界面图标升级为 [Lucide](https://github.com/lucide-icons/lucide) 开源矢量图标（ISC 许可，免费可商用）：动作面板 27 个默认动作、面板顶部的设置/关闭按钮、复选框勾选样式均由 emoji / 程序绘制替换为统一风格的描边图标；高 DPI 屏按 2 倍像素渲染，清晰不糊。
- 应用图标（app.ico / 托盘 / 快捷方式）重做为品牌蓝渐变圆角底 + Lucide 闪电（zap）图案，提供 16–256px 六档尺寸，小尺寸自动加粗描边保证可辨识。
- 配置中心左侧导航 14 个页面全部配上语义对应的矢量图标，替换原先的 Unicode 符号（⬆ ✦ ▤ ⌖ 等）。
- README 配图（动作面板、配置中心动作库）更新为新图标界面。

### Added

- 动作编辑新增「图标选择器」：内置 50 个 Lucide 图标，支持搜索、网格预览、点击选用；也可继续填写 emoji 或任意文字；旧配置中的 emoji 图标自动按原样显示，完全向后兼容。
- 动作库列表「图标」列直接显示图标预览，配置所见即所得。

### Fixed

- 勾选框（check.png）改由 Lucide check 矢量渲染，替换原先的手绘位图，边缘平滑无锯齿。

## [0.1.25] 及之前

见 Git 提交历史（项目初始发布版本）。
