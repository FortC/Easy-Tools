# Easy-Tools

Windows 全局动作面板：任意位置按下`鼠标中键`，在光标处弹出一个网格动作面板，
搜索、执行、粘贴文本、整理表格——不用离开当前窗口去找工具栏。

实现依据：[docs/设计方案.md](docs/设计方案.md) v0.1。

## 范围声明（先说不做什么）

本工具**不包含、也永远不应包含**任何规避检测的能力：
不隐藏窗口与进程、不规避录屏与切换检测、不绕过粘贴限制。

"粘贴/键入文本"是一个**通用**能力，就像任何文本编辑器都有复制粘贴一样；
它的用途是把文本放进你当下正在编辑的那个窗口，与检测无关。

## 快速开始

```powershell
pip install -r requirements.txt
python main.py
```

启动后托盘出现图标，任意位置按中键即弹出面板。托盘右键可打开配置中心。

## 灰度更新

设置中心的“软件更新”页会安全检查 Easy-Tools 的当前稳定版本；托盘菜单“检查灰度更新”可直接打开该页面。更新版本按服务器配置的灰度百分比面向匿名安装分组逐步开放；本机生成的随机 ID 仅保存在 `%APPDATA%\\Easy-Tools\\.update-client-id`，不发送到更新服务器。入选后可查看版本说明，确认下载并安装。下载仅获取本机缺失的 SHA-256 内容块，构建校验完成后由独立助手在主程序退出后替换文件并启动新版；配置、日志、卸载程序及安装目录清单外文件保留。

新安装包携带更新服务的信任根证书。客户端优先尝试公司服务 `192.168.99.206:9443`；公司服务无法连接时回退 `https://www.clblog.online:9443`。腾讯云一键包通过 Nginx 在独立的 9443 监听器复用现有 HTTPS 自动签续证书，不改个人主页的 443 配置；私钥和发布令牌仅保存在服务器端。版本发布者可在更新中心发布应用 `easy-tools`、平台 `windows-x64` 的唯一递增版本号，并指定灰度百分比。建议依次发布 10%、25%、50%、100% 的新版本；分组在同一软件各版本间保持稳定，逐级扩大灰度时现有用户仍属于前一档。旧版本不受未入选的新版本影响。仅安装版启用远程更新，源码开发运行不会改写项目源文件。

开发和打包新版：先运行 `python build.py --no-smoke --tag gray-0.1.23`，再运行 `python build_installer.py --no-smoke --tag gray-0.1.23`。`--tag` 将新构建放入独立目录，保留既有 `build`、`dist` 和安装包。构建需 Windows .NET Framework C# 编译器，用于生成独立的安装目录更新助手。发布灰度包前，请在更新中心选择 `easy-tools` / `windows-x64`，首批可选 10%；候选提升至 25%、50%、100% 时发布内容已更新且版本号唯一的新版本。

### 人工验收

```powershell
python main.py                 # 程序跑起来
python verify_manual.py check  # 另开窗口，随时查看当前上下文与命中规则
```

## 目录结构

```
easy_tools/
  core/          纯 Python，零 Qt 依赖，可无 GUI 单测
    actions/     动作与处理器注册表
    chain.py     事件链引擎
    context.py   L1 上下文识别（进程 + 标题正则）
    ocr.py       L2 内容识别（可插拔后端）
    hooks.py     全局钩子编排
    app.py       组合根（把所有部件接起来）
  ui/            PySide6 表现层，只做渲染与交互
  win32/         纯 ctypes 的 Win32 接入层
  config/        schema.py 校验 + defaults.json 默认配置
main.py          托盘常驻 + 面板 + 配置中心
```

分层纪律：```core/``` 里不允许出现任何 Qt import。
业务逻辑因此可以完全无 GUI 测试，UI 出问题不会牵连业务。

## Win32 接入层为什么用 ctypes 而不是 pywin32

设计文档原本指定 pywin32 / pynput，实际改用纯标准库 ```ctypes```，原因：

- pywin32 需要编译或预编译 wheel，与 CPython 版本强绑定（本机是 3.14）；
- pynput 在触屏与部分驱动下有已知兼容问题；
- ctypes 零依赖、打包体积小、行为完全可控。

代价是自己维护结构体定义和 ```argtypes``` 声明——**```argtypes``` 只在调用时校验，
写错不会立刻报错，而是调用那一刻抛 ctypes 异常甚至静默错位**。
为此专门写了 ```tests/test_win32_decls.py``` 做静态声明审计：

- 约 55 个 API 的参数个数与声明比对；
- 关键结构体在 64 位下的 ```sizeof```；
- 常量值与 VK 表。

改 ```win32/api.py``` 时必须同步更新这张表，否则一个写错的参数个数
会潜伏到某个低频路径上才炸。这层审计已经抓到过 ```GetWindow``` /
```GetAncestor``` 少声明一个参数、```SM_CYSCREEN``` 取错常量导致负分辨率等问题。

## 中键消费的两难

Windows 低级钩子**无法"取消"一个已经发出的鼠标事件**。所以只能诚实二选一：

| 模式 | 行为 | 代价 |
| --- | --- | --- |
| ```swallow_tap```（默认） | 短按吞掉 down/up，弹出面板；按住拖动时用合成的滚轮事件还原滚动 | 原生中键的其他绑定失效 |
| ```native_passthrough``` | 完全不吞，中键行为 100% 原生保留 | 会绑定中键的软件里，按中键会先触发它的行为 |

两种模式都实现在 ```win32/mouse.py```，配置中心「触发设置」页可切换。

## 上下文识别（L1 / L2）

- **L1 应用识别**：前台进程名 + 窗口标题正则，零成本、100% 准。
- **L2 内容识别**：客户端区 OCR 关键词匹配，后台线程 + 防抖 + 缓存。

**面板弹出不等待 OCR**（方案决策 2）：先按 L1 出图，OCR 结果到达后动态补充分组。

OCR 后端可插拔。```rapidocr_onnxruntime``` 装不上是常态（onnxruntime 未必有对应
Python 版本的 wheel），所以程序会**明确显示"不可用 + 具体原因"**，
而不是静默假装成功——用户必须能分得清"L2 规则没命中"和"OCR 坏了"。
其余功能（L1、面板、事件链、文本输入）完全不依赖 OCR。

## 事件链

```json
{
  "id": "chain_copy_clean_paste",
  "steps": [
    {"type": "action", "id": "clipboard.copy"},
    {"type": "action", "id": "text.strip", "input": "{clipboard}"},
    {"type": "action", "id": "text.drop_blank_lines", "input": "{last.output}"},
    {"type": "action", "id": "input.paste", "input": "{last.output}"}
  ]
}
```

步骤类型：```action``` / ```condition``` / ```wait``` / ```loop``` / ```setvar``` / ```stop```。

变量：```{clipboard}``` ```{selection}``` ```{ocr_text}``` ```{last.output}``` ```{stepN.output}``` ```{var:name}```，
另有 ```{date}``` ```{time}``` ```{datetime}``` ```{weekday}```。未知占位符**原样保留**，绝不抛异常。

条件表达式：```always``` ```never``` ```has_table``` ```is_empty``` ```is_number```
```contains:xx``` ```ncontains:xx``` ```regex:xx``` ```len_gt:10``` ```var:name```。

**剪贴板处理器同时是链步骤**（方案 `2.6）：面板里能一键用的
```text.*``` / ```table.*``` / ```number.*``` / ```case.*``` / ```date.*``` / ```data.*``` 处理器，
在链里直接按 id 引用，不需要为链再实现一遍。

## 文本输入的两种模式

| 模式 | 实现 | 适用 |
| --- | --- | --- |
| ```paste``` | 备份剪贴板 → 写入 → Ctrl+V → 延迟恢复 | 绝大多数程序，兼容性最好 |
| ```type``` | SendInput 逐字符（UTF-16 码元），速度与随机抖动可配 | 会拒绝粘贴的输入框 |

一个刻意的取舍：**当原剪贴板里没有文本（只有图片或文件）时，程序不会恢复它。**
强行写回会破坏用户的数据，"忠实还原"在这里不如"不乱动"正确。

## 测试

```powershell
python -m unittest discover -s tests
```

79 个用例，分四组：

| 文件 | 覆盖 |
| --- | --- |
| ```test_win32_decls.py``` | ctypes 声明审计（参数个数、结构体大小、常量） |
| ```test_processors.py``` | 处理器与条件表达式 |
| ```test_chain.py``` | 事件链变量、分支、循环、中止、失败传播 |
| ```test_ui_smoke.py``` | 面板/设置/链编辑器的真实渲染与交互，并输出截图到 ```evidence/``` |

UI 测试在 ```QT_QPA_PLATFORM=offscreen``` 下跑。**注意**：offscreen 平台没有字体，
截图中中文会显示为豆腐块，这是测试环境特性，不是缺陷；
```evidence/*_real.png``` 是在原生 windows 平台截的，可对照。

## 交互行为（v0.1.1 修定）

- **中键短按**（按住 <400ms、位移 <12px）→ 光标旁弹出面板；**再按一次中键 = 关闭**
- 中键按住拖动 → 原生风格的滚动（可配置关闭）
- **Esc / 点击面板外部** → 关闭面板；执行动作后面板自动让位
- 底部状态栏直说识别结果：`当前：xxx.exe ｜ 专属：规则名` 或 `（公共操作区，无专属规则）`
- 上下文命中同时写入日志：`上下文命中：xxx.exe ← 浏览器（进程 msedge.exe）`，识别问题一行定位

阈值（tap_max_ms / drag_threshold_px）与中键模式（吞掉 or 放行）都在配置中心可调。

## 打包与运行

打包前请从 ```models/MODEL-NOTICE.txt``` 中列出的模型来源下载
```SmolLM2-360M-Instruct-Q4_K_M.gguf```，放到 ```models/``` 目录。
该模型文件约 270 MB，未放入 Git 仓库；llama.cpp CPU 运行时已随源码放在
```vendor/llama/```。Windows 打包还需要 Python 依赖及 .NET Framework C# 编译器。

```powershell
pip install pyinstaller
python build.py            # 构建 + 产物冒烟关卡（推荐）
```
产出 ```dist\Easy-Tools\Easy-Tools.exe```（目录模式，2.6 MB exe / 115 MB 目录）。
双击即托盘常驻：托盘菜单可显示面板、开配置中心、重载配置、退出；
重复双击不会开第二个实例，而是让已有实例弹出面板。

用目录模式而非单文件：单文件每次启动要解压到临时目录，2-5 秒启动延迟，
对一个常驻托盘 + 全台钩子的低延迟工具来说不可接受。

### 产物冒烟关卡（重要）

```python
python smoke_exe.py
```
```build.py
 的最后一步会自动跑它，**不过关就算构建失败**。它验的是外部可观测事实：
进程存活且无异常框、日志文件真的被创建且含钩子状态、config.json 真的落盘、
第二个实例自行退出、进程数恒为 1、杀掉后能重新启动。

为什么要有这一关：开发态测试全绿 ≠ 打包产物能跑。本项目曾出现过两个
只在打包后才暴露的缺陷（只读资源路径在 frozen 下分叉导致首次启动崩溃；
import 阶段的日志配置被静默吞掉导致永远不写日志）。共同根因不是某条路径写错，
而是验收的是源码行为、交付的却是二进制。详见 .agents/notes 里的
```packaged-artifact-smoke-gate```。

### 构建脚本会响亮地失败

```build.py
 在 PyInstaller 返回非零时会立即退出，并明确提示
**不要拿 dist 里的旧产物继续验证**。这提示是被真实事故逼出来的：
一次构建实际失败，但磁盘上还留着上一版 exe，冒烟测试照跑不误，
报出一堆早已修好的 bug。

## 配置

- 配置文件：开发态 ```./data/config.json```，打包后 ```%APPDATA%\\Easy-Tools\\config.json```
- 日志：```./data/easy_tools.log```（1MB × 3 轮转）
- 首次启动从 ```easy_tools/config/defaults.json``` 复制，之后以磁盘为准
- 支持在配置中心一键导入/导出 JSON

所有设置**保存即生效**，由 ```ConfigStore``` 广播变更，钩子与面板即时重载，无需重启。

## 已知限制

- OCR 需要 ```rapidocr_onnxruntime```，本机未安装，当前 OCR 关键词规则不会命中。
- 全局鼠标钩子需要与目标程序同权限级别；部分以管理员身份运行的程序，
  普通权限的 Easy-Tools 截不到它的窗口内容。以管理员身份运行本程序可解决。
- 事件链试运行会**跳过**会产生真实输入或剪贴板副作用的步骤
  （见 ```chain_editor._SIDE_EFFECT_IDS```），只验证数据流。
- 中键被本程序吞掉时（```swallow_tap```），目标软件自己的中键绑定会失效。
