"""中键呼出的网格动作面板（方案 2.2）。

线程纪律（本文件最容易出致命 bug 的地方）：
全局钩子跑在自己的线程里，直接调 Qt 会炸。面板对外只暴露一个
Signal(x, int, y)，由 Qt 的队列连接自动把调用切回 GUI 线程——
所有 setVisible/布局操作都必须在 GUI 线程，这是不可协商的。

交互：
- 顶部搜索框即时过滤（名称 + 分组）
- 分组标题 + 网格单元格
- 鼠标悬停高亮、方向键移动、Enter 执行、数字键 1-9 直达
- Esc 关闭；点击面板外部关闭
- 越界自动翻转，保证面板始终完整可见
"""

from __future__ import annotations

import ctypes
import html
from typing import Dict, List, Optional

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QColor, QFontMetrics, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsDropShadowEffect,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QSizePolicy, QVBoxLayout,
                               QWidget)

from ..core.app import AppService, PanelItem
from ..core.log import get_logger
from . import theme


log = get_logger("ui.panel")


class ActionPanel(QWidget):
    """动作面板本体。frameless + 置顶 + 不进任务栏。"""

    activated = Signal(str)          # 动作 id
    closed = Signal()
    settings_requested = Signal()

    def __init__(self, service: AppService, parent=None) -> None:
        self.service = service
        self._cells: List["_Cell"] = []
        self._groups: List[str] = []
        self._items: List[PanelItem] = []
        self._cached_groups: Optional[Dict[str, List[PanelItem]]] = None
        self._group_views = []
        self._empty_label = None
        self._rendered_groups = False
        self._selected = 0
        self._active_group = ""
        self._page = 0
        self._page_query = ""
        self._page_render_key = None
        self._mouse_pos: QPoint = QPoint(200, 200)

        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowTitle("Easy-Tools 动作面板")
        self.setFocusPolicy(Qt.StrongFocus)

        self._build()
        self.apply_config()

    # ------------------------------------------------------------ 构建
    def _build(self) -> None:
        root = self._root = QFrame(self)
        root.setObjectName("PanelRoot")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(8, 8, 8, 7)
        outer.setSpacing(4)
        outer.setAlignment(Qt.AlignTop)
        # 投影让面板从桌面"浮起来"，这是观感差别最大的一处
        shadow = QGraphicsDropShadowEffect(root)
        shadow.setBlurRadius(40)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 160))
        root.setGraphicsEffect(shadow)

        header = QWidget(root)
        header.setObjectName("PanelHeader")
        header_lay = QHBoxLayout(header)
        header_lay.setContentsMargins(3, 0, 1, 0)
        header_lay.setSpacing(7)
        self.brand = QLabel("Easy-Tools", header)
        self.brand.setObjectName("PanelBrand")
        header_lay.addWidget(self.brand)
        header_lay.addStretch(1)
        self.settings_btn = QPushButton("⚙", header)
        self.settings_btn.setObjectName("PanelToolButton")
        self.settings_btn.setToolTip("打开设置")
        self.settings_btn.clicked.connect(self._request_settings)
        header_lay.addWidget(self.settings_btn)
        self.close_btn = QPushButton("×", header)
        self.close_btn.setObjectName("PanelToolButton")
        self.close_btn.setToolTip("关闭面板")
        self.close_btn.clicked.connect(self.close_panel)
        header_lay.addWidget(self.close_btn)
        outer.addWidget(header)

        self.search = QLineEdit(root)
        self.search.setPlaceholderText("搜索动作…")
        self.search.setClearButtonEnabled(True)
        self.search.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # 焦点通常在搜索框上；Esc 只挂在面板 keyPressEvent 会漏接
        self.search.installEventFilter(self)
        self.search.textChanged.connect(self._refresh)
        self.search.returnPressed.connect(self._activate_selected)
        outer.addWidget(self.search)

        self.tab_host = QWidget(root)
        self.tab_layout = QGridLayout(self.tab_host)
        self.tab_layout.setContentsMargins(0, 0, 0, 0)
        self.tab_layout.setSpacing(4)
        self.tab_host.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        outer.addWidget(self.tab_host)
        self.scroll = QScrollArea(root)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        # 横向滚动条会在底部啃掉一行高度，且格子宽度由布局自行压缩，
        # 导致最后一列被裁——宽度改由列数显式决定
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.body = QWidget(self.scroll)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 2, 0)
        self.body_layout.setSpacing(2)
        self.body_layout.addStretch(1)
        self.scroll.setWidget(self.body)
        outer.addWidget(self.scroll, 1)

        self.pager = QWidget(root)
        paging = QHBoxLayout(self.pager)
        paging.setContentsMargins(0, 0, 0, 0)
        self.prev_btn = QPushButton("‹ 上一页")
        self.next_btn = QPushButton("下一页 ›")
        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignCenter)
        self.prev_btn.clicked.connect(lambda: self._change_page(-1))
        self.next_btn.clicked.connect(lambda: self._change_page(1))
        paging.addWidget(self.prev_btn)
        paging.addWidget(self.page_label, 1)
        paging.addWidget(self.next_btn)
        outer.addWidget(self.pager)
        self.hint = QLabel("公共操作区 + 当前上下文专属动作", root)
        self.hint.setObjectName("SearchHint")
        self.hint.setMaximumHeight(31)
        outer.addWidget(self.hint)

        self.setLayout(QVBoxLayout())
        lay = self.layout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(root)

    # ------------------------------------------------------------ 配置与样式
    def apply_config(self) -> None:
        cfg = self.service.store.section("panel", {})
        p = theme.palette("light")
        self._accent = p["accent"]
        self._text_dim = p["text_dim"]
        self.setStyleSheet(theme.qss(p))
        self.setWindowOpacity(float(cfg.get("opacity", 0.96)))
        self.search.setVisible(bool(cfg.get("show_search", True)))
        self._cols = max(1, int(cfg.get("columns", 5)))
        self._cell_w = max(48, int(cfg.get("cell_width", 88)))
        self._cell_h = max(40, int(cfg.get("cell_height", 72)))
        # 所有分组都占相同的五列宽度，从左侧对齐；末行不做居中伸展。
        self.scroll.setMinimumWidth(self._cols * (self._cell_w + 4) + 10)
        self._show_groups = bool(cfg.get("show_groups", True))
        self._max_rows = max(1, int(cfg.get("max_rows", 7)))
        self._show_icon = bool(cfg.get("show_icon", True))
        self._offset = int(cfg.get("cursor_offset", 16))
        self._flip = bool(cfg.get("flip_at_edges", True))
        # Snapshot business state once per open/config change. Search keystrokes
        # should only toggle existing widgets, not query the resolver/OCR again.
        self._tabbed = cfg.get("display_mode", "tabs") == "tabs"
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff if self._tabbed else Qt.ScrollBarAsNeeded)
        self.tab_host.setVisible(self._tabbed)
        self._cached_groups = self._collect()
        self._active_group = next(iter(self._cached_groups), "")
        self._page = 0
        self._page_render_key = None
        self._rebuild_tabs()
        self._rendered_groups = False
        self._refresh()

    # ------------------------------------------------------------ 面板打开
    def open_at(self, x: int, y: int) -> None:
        """在屏幕坐标 (x,y) 附近弹出。必须在 GUI 线程调用。"""
        # 必须在 show() 之前记住目标窗口：此刻面板还没抢到焦点，
        # 真正的输入目标仍在前台。晚一步（比如放在动作点击时）记住的就是面板自己。
        from ..core import focus
        focus.remember()
        self.service.refresh_context()          # 更新 L1 上下文
        self._mouse_pos = QPoint(x, y)
        self.apply_config()
        self.search.clear()
        self._selected = 0
        self._ocr_polls_left = self._OCR_POLL_LIMIT
        self._position(x, y)
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.OtherFocusReason)
        self.search.setFocus(Qt.OtherFocusReason)
        # 登记本窗口，任何路径的 remember() 都不会把它当成输入目标
        from ..core import focus as _focus
        _focus.register_our_window(int(self.winId()))
        # Windows 前台锁：后台进程的 activateWindow 可能被静默拒绝，
        # 落到事件循环后再抢一次，否则键盘（含 Esc）完全失灵
        QTimer.singleShot(0, self._reclaim_focus)

        # L2 异步补充：识别完成后动态刷新分组（方案决策 2：面板不等待 OCR）
        if self.service.ocr.status().get("enabled"):
            QTimer.singleShot(120, self._poll_ocr)

    def _reclaim_focus(self) -> None:
        if self.isVisible():
            self.activateWindow()
            self.raise_()
            self.search.setFocus(Qt.OtherFocusReason)

    #: 面板打开后最多再轮询几次 OCR 结果（每次 200ms）。设上限是为了
    #: "面板一直开着"时不会永远每 200ms 跑一次正则匹配。
    _OCR_POLL_LIMIT = 8

    def _poll_ocr(self) -> None:
        if not self.isVisible():
            return
        cfg = self.service.store.section("ocr", {})
        ttl = int(cfg.get("debounce_ms", 300)) + int(cfg.get("cache_ttl_ms", 5000))
        text = self.service.ocr.cached_text(ttl)
        if text and text != self.service.resolver.current().ocr_text:
            self.service.resolver.update_ocr(text, self.service.ocr.status().get("backend", "none"))
            self._cached_groups = self._collect()
            self._rendered_groups = False
            self._page_render_key = None
            self._rebuild_tabs()
            self._refresh()
        self._ocr_polls_left = getattr(self, "_ocr_polls_left", self._OCR_POLL_LIMIT) - 1
        if self._ocr_polls_left > 0:
            QTimer.singleShot(200, self._poll_ocr)

    def _dpr_at(self, x: int, y: int) -> float:
        """物理坐标所在屏幕的 devicePixelRatio。

        钩子给的是物理像素，Qt 的 move()/geometry() 用逻辑像素；
        高分屏 125%/150% 缩放下不换算，面板就会弹在错误位置——
        这是真实故障，不是理论问题。Qt 的 screenAt 只认逻辑坐标，
        所以按"逻辑几何 × 各屏 DPR"手动反查物理归属。
        """
        from PySide6.QtGui import QGuiApplication
        try:
            for scr in QGuiApplication.screens():
                dpr = float(scr.devicePixelRatio()) or 1.0
                g = scr.geometry()
                if (g.x() * dpr <= x < (g.x() + g.width()) * dpr
                        and g.y() * dpr <= y < (g.y() + g.height()) * dpr):
                    return dpr
            scr = QGuiApplication.primaryScreen()
            return (float(scr.devicePixelRatio()) or 1.0) if scr else 1.0
        except Exception:
            return 1.0

    def _position(self, x: int, y: int) -> None:
        """把面板放到光标附近。x,y 是钩子给的物理像素。

        坐标系实测结论（别凭直觉改回去，见 tests/test_panel_position.py）：
        - 低级钩子给的 pt、GetSystemMetrics/virtual_screen、GetWindowRect
          都是物理像素（Qt6 启动时把进程设成了 Per-Monitor DPI 感知）；
        - Qt 的 width()/height() 是逻辑像素，原生尺寸 = 逻辑 × dpr；
        - move() 取的是**逻辑**像素，Qt 会再乘该屏 dpr，所以换算必须做。
        夹取全程用物理像素（面板尺寸先乘 dpr），最后一步再除回去。
        """
        # Widget removal and visibility changes invalidate several nested layouts.
        # Resolve them from the inside out before measuring the top-level window;
        # adjustSize() alone can reuse the previous page's minimum-size cache.
        self.body_layout.activate()
        self.body.updateGeometry()
        self._root.layout().invalidate()
        self._root.layout().activate()
        self._root.updateGeometry()
        self.layout().invalidate()
        self.layout().activate()
        self.adjustSize()
        self.layout().activate()
        self._root.layout().activate()
        self._root.update()
        dpr = self._dpr_at(x, y)
        w, h = int(self.width() * dpr), int(self.height() * dpr)
        from ..win32 import window as w32
        vl, vt, vw, vh = w32.virtual_screen()
        off = int(self._offset * dpr)
        # 水平：右侧优先，右边放不下才翻到左侧
        px = x - off - w if (self._flip and x + off + w > vl + vw) else x + off
        # 垂直：下方优先；下方不够而上方够则上翻；两边都不够就取空间大的一侧
        # 并贴边对齐（否则光标靠屏幕下沿时面板会整体跳到很远的上方）
        if y + off + h <= vt + vh:
            py = y + off
        elif y - off - h >= vt:
            py = y - off - h
        else:
            py = (y + off) if (vt + vh - y) >= (y - vt) else max(vt + 2, y - h)
        px = max(vl + 2, min(px, vl + vw - w - 2))
        py = max(vt + 2, min(py, vt + vh - h - 2))
        # 最后一步：physical -> logical。Qt6 下 move() 会再乘一次该屏 dpr，
        # 不换算就会在 125% 屏上把面板推到光标的 1.25 倍处（实测偏 119px）。
        # 曾经因为探针进程 DPI-unaware、GetWindowRect 返回虚拟化坐标，
        # 误判成「move 直通原生像素」而把这一步删掉——教训见
        # tests/test_panel_position.py 与 .agents/notes。
        self.move(round(px / dpr), round(py / dpr))

    def close_if_outside(self, px: int, py: int) -> None:
        """钩子线程转来的外部按下（物理坐标）：点在面板外则关闭。

        命中测试用 GetWindowRect 的原生物理矩形，与钩子坐标同系，
        不掺合 Qt 的逻辑几何（两者对这类窗口不一致，见 _position 注释）。
        """
        if not self.isVisible():
            return
        from ..win32 import api
        r = api.RECT()
        if api.user32.GetWindowRect(int(self.winId()), ctypes.byref(r)):
            inside = (r.left <= px < r.right) and (r.top <= py < r.bottom)
            if not inside:
                self.close_panel()

    # ------------------------------------------------------------ 内容
    def _collect(self) -> Dict[str, List[PanelItem]]:
        snap = self.service.panel_snapshot()
        groups: Dict[str, List[PanelItem]] = {}
        for name, items in (snap.get("public") or {}).items():
            groups[name] = list(items)
        for name, items in (snap.get("context") or {}).items():
            key = "▸ " + name
            groups[key] = list(items)
        fg = (snap.get("window") or {}).get("process") or ""
        ctx = [k[2:] for k in groups if k.startswith("▸ ")]
        # 状态栏直说"识别到了什么"，识别结果不再靠猜
        if ctx:
            self.hint.setText(
                '<span style="color:%s">当前 %s</span> ｜ 专属：%s<br>'
                '<span style="color:%s;font-size:10px">↑↓ 选择 · Enter 执行 · '
                '1-9 直达 · Esc / 点外部关闭 · 再按中键收起</span>'
                % (self._accent, html.escape(fg or "-"), "、".join(ctx), self._text_dim))
        else:
            self.hint.setText(
                '<span style="color:%s">当前 %s</span>（公共操作区，无专属规则）<br>'
                '<span style="color:%s;font-size:10px">↑↓ 选择 · Enter 执行 · '
                '1-9 直达 · Esc / 点外部关闭 · 再按中键收起</span>'
                % (self._accent, html.escape(fg or "-"), self._text_dim))
        order = self.service.store.section("panel", {}).get("group_order", [])
        ranks = {name: i for i, name in enumerate(order)}
        # A recognized application's private actions must be immediately
        # discoverable. Keep the user's configured order within each zone.
        def group_rank(name):
            private = name.startswith("▸ ")
            configured_name = name if name in ranks else (name[2:] if private else name)
            return (0 if private else 1, ranks.get(configured_name, len(ranks)), name)
        return dict(sorted(groups.items(), key=lambda pair: group_rank(pair[0])))

    def _rebuild_tabs(self):
        while self.tab_layout.count():
            w = self.tab_layout.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self._tab_buttons = {}
        width = self._cols * (self._cell_w + 4) + 10
        self.tab_host.setFixedWidth(width)
        tab_cols = max(1, width // 140)
        for i, (name, items) in enumerate((self._cached_groups or {}).items()):
            button = QPushButton(name)
            button.setObjectName("CategoryTab")
            button.setCheckable(True)
            button.setFocusPolicy(Qt.NoFocus)
            button.setFixedWidth((width - (tab_cols - 1) * 4) // tab_cols)
            button.ensurePolished()
            button.setText(QFontMetrics(button.font()).elidedText(name, Qt.ElideRight, button.width() - 14))
            button.setToolTip("%s · %d 个动作" % (name, len(items)))
            button.clicked.connect(lambda checked=False, n=name: self._choose_group(n))
            self.tab_layout.addWidget(button, i // tab_cols, i % tab_cols)
            self._tab_buttons[name] = button
        # Category headers must never stretch vertically into the action area.
        self.tab_layout.invalidate()
        self.tab_layout.activate()
        self.tab_host.setFixedHeight(self.tab_layout.sizeHint().height())

    def _choose_group(self, name):
        self._active_group = name
        self._page = 0
        self._selected = 0
        # Clear search without an intermediate rebuild using the old category.
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._refresh()
        self.search.setFocus()

    def _change_page(self, delta):
        self._page += delta
        self._selected = 0
        self._refresh()

    def _refresh_tabs(self):
        groups = self._cached_groups or {}
        if self._active_group not in groups:
            self._active_group = next(iter(groups), "")
        keyword = self.search.text().strip().lower()
        query = (keyword, self._active_group)
        if query != self._page_query:
            self._page = 0
            self._page_query = query
        for name, button in self._tab_buttons.items():
            button.setChecked(not keyword and name == self._active_group)
        items = ([item for values in groups.values() for item in values if item.matches(keyword)]
                 if keyword else groups.get(self._active_group, []))
        dpr = self._dpr_at(self._mouse_pos.x(), self._mouse_pos.y())
        screen = QApplication.screenAt(QPoint(round(self._mouse_pos.x() / dpr), round(self._mouse_pos.y() / dpr))) or QApplication.primaryScreen()
        available = screen.availableGeometry().height() if screen else 768
        tab_rows = self.tab_layout.rowCount()
        rows = max(1, min(self._max_rows, (available - 250 - tab_rows * 40) // (self._cell_h + 4)))
        # A small category must not collapse the shared viewport while another
        # category needs several rows. Keep the same grid and pager on every Tab,
        # including searches, empty results and the final partial page.
        largest = max((len(values) for values in groups.values()), default=0)
        rows = min(rows, max(1, (largest + self._cols - 1) // self._cols))
        capacity = rows * self._cols
        pages = max(1, (len(items) + capacity - 1) // capacity)
        self._page = max(0, min(self._page, pages - 1))
        selected = items[self._page * capacity:(self._page + 1) * capacity]
        render_key = (tuple((x.action_id, x.name, x.icon) for x in selected), self._cols, self._cell_w, self._cell_h, self._show_icon)
        if render_key != self._page_render_key:
            self._build_group_views({"": selected})
            self._page_render_key = render_key
        self._cells = [cell for v in self._group_views for cell, item in v["cells"]]
        self._items = selected
        for v in self._group_views:
            if v["title"] is not None:
                v["title"].hide()
        self._empty_label.setVisible(not selected)
        self.scroll.setFixedHeight(rows * (self._cell_h + 4) + 4)
        self.pager.setVisible(True)
        self.pager.setFixedHeight(self.pager.sizeHint().height())
        self.prev_btn.setEnabled(self._page > 0)
        self.next_btn.setEnabled(self._page + 1 < pages)
        self.page_label.setText(("搜索 · " if keyword else "") + "%d 个动作 · %d / %d 页" % (len(items), self._page + 1, pages))
        if self._selected >= len(self._cells):
            self._selected = 0
        self._paint_selection()
        self._position(self._mouse_pos.x(), self._mouse_pos.y())

    def _refresh(self) -> None:
        if self._tabbed:
            self._refresh_tabs()
            return
        self.pager.hide()
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry().height() if screen else 768
        self.scroll.setFixedHeight(min(self._max_rows * (self._cell_h + 4), max(80, available - 210)))
        if self._cached_groups is None:
            self._cached_groups = self._collect()
        if not self._rendered_groups:
            self._build_group_views(self._cached_groups)

        keyword = self.search.text().strip().lower()
        self._cells = []
        self._items = []
        for view in self._group_views:
            visible = []
            for cell, item in view["cells"]:
                show = item.matches(keyword)
                cell.setVisible(show)
                if show:
                    visible.append((cell, item))
                    self._cells.append(cell)
                    self._items.append(item)
            for i, (cell, item) in enumerate(visible):
                view["host"].layout().addWidget(cell, i // self._cols, i % self._cols)
            show_group = bool(visible)
            if view["title"] is not None:
                view["title"].setVisible(show_group and self._show_groups and not keyword)
            view["host"].setVisible(show_group)
        if self._empty_label is not None:
            self._empty_label.setVisible(not self._cells)
        if self._selected >= len(self._cells):
            self._selected = 0
        self._paint_selection()
        self._position(self._mouse_pos.x(), self._mouse_pos.y())

    def _build_group_views(self, groups: Dict[str, List[PanelItem]]) -> None:
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._group_views = []
        self._empty_label = None

        for gname, items in groups.items():
            if not items:
                continue
            title = None
            if self._show_groups and gname:
                title = QLabel(
                    '<span style="color:%s">▌</span>&nbsp;%s'
                    % (self._accent, html.escape(gname)), self.body)
                title.setObjectName("GroupTitle")
                self.body_layout.addWidget(title)
            grid_host = QWidget(self.body)
            grid = QGridLayout(grid_host)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(4)
            grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            group_cells = []
            for i, item in enumerate(items):
                cell = _Cell(item, self._cell_w, self._cell_h, self._show_icon)
                cell.clicked.connect(self._activate)
                grid.addWidget(cell, i // self._cols, i % self._cols)
                group_cells.append((cell, item))
            for c in range(self._cols):
                grid.setColumnStretch(c, 0)
                grid.setColumnMinimumWidth(c, self._cell_w)
            grid_host.setFixedWidth(self._cols * self._cell_w + (self._cols - 1) * 4)
            self.body_layout.addWidget(grid_host, 0, Qt.AlignLeft)
            self._group_views.append({"title": title, "host": grid_host, "cells": group_cells})
        self._empty_label = QLabel("没有匹配的动作（去配置中心添加，或检查搜索词）", self.body)
        self._empty_label.setObjectName("GroupTitle")
        self._empty_label.setWordWrap(True)
        self.body_layout.addWidget(self._empty_label)
        self.body_layout.addStretch(1)
        self._rendered_groups = True

    def _paint_selection(self) -> None:
        for i, c in enumerate(self._cells):
            on = i == self._selected
            if c.property("selected") != ("true" if on else "false"):
                c.set_selected(on)

    # ------------------------------------------------------------ 事件
    def _activate(self, action_id: str) -> None:
        self.activated.emit(action_id)
        self.close_panel()

    def _activate_selected(self) -> None:
        if 0 <= self._selected < len(self._cells):
            self._activate(self._items[self._selected].action_id)

    def eventFilter(self, obj, ev) -> bool:
        # 搜索框持有焦点时，Esc 在这里截获（keyPressEvent 收不到）
        if obj is self.search and ev.type() == QEvent.Type.KeyPress                 and ev.key() == Qt.Key_Escape:
            self.close_panel()
            return True
        return False

    def keyPressEvent(self, ev: QKeyEvent) -> None:
        key = ev.key()
        if self._tabbed and key in (Qt.Key_PageUp, Qt.Key_PageDown):
            self._change_page(-1 if key == Qt.Key_PageUp else 1)
            return
        if key == Qt.Key_Escape:
            self.close_panel()
            return
        if key == Qt.Key_Return or key == Qt.Key_Enter:
            self._activate_selected()
            return
        if key in (Qt.Key_Down, Qt.Key_Right, Qt.Key_Tab) and self._cells:
            self._selected = (self._selected + 1) % len(self._cells)
            self._paint_selection()
            return
        if key in (Qt.Key_Up, Qt.Key_Left, Qt.Key_Backtab) and self._cells:
            self._selected = (self._selected - 1) % len(self._cells)
            self._paint_selection()
            return
        if Qt.Key_0 <= key <= Qt.Key_9 and self._cells:
            idx = key - Qt.Key_0 - 1
            if 0 <= idx < len(self._cells):
                self._activate(self._cells[idx].action_id)
                return
        super().keyPressEvent(ev)

    def close_panel(self) -> None:
        self.hide()
        log.info("面板已关闭")
        self.closed.emit()

    def _request_settings(self) -> None:
        self.close_panel()
        self.settings_requested.emit()

    def closeEvent(self, ev: QCloseEvent) -> None:
        self.close_panel()
        ev.accept()


# 图标行固定高度：与 theme 里 QLabel#CellIcon 的字号保持一致，
# 否则多行图标/缺图标时格子高度会跳动，整块面板看起来就散。
_ICON_H = 34


class _Cell(QFrame):
    """单个动作格子。"""

    clicked = Signal(str)

    def __init__(self, item: PanelItem, w: int, h: int, show_icon: bool, parent=None) -> None:
        super().__init__(parent)
        self._id = item.action_id
        self.setObjectName("Cell")
        self.setFixedSize(w, h)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("%s ｜ %s" % (item.name, item.group))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 3, 2, 3)
        lay.setSpacing(1)
        if show_icon:
            ic = QLabel(item.icon, self)
            ic.setObjectName("CellIcon")
            ic.setAlignment(Qt.AlignCenter)
            ic.setFixedHeight(_ICON_H)   # 固定高度，避免不同字重把格子顶歪
            lay.addWidget(ic)
        nm = QLabel(self)
        nm.setObjectName("CellName")
        nm.setAlignment(Qt.AlignCenter)
        nm.setWordWrap(False)
        nm.setTextFormat(Qt.PlainText)
        lay.addWidget(nm)
        lay.addStretch(1)
        # 长名字必须截断：此前直接 setText(item.name)，名字比格子宽时
        # Qt 会把整行字画出来压到隔壁格子上，用户看到的就是"字盖住邻居"。
        # 这里按格子宽度用省略号截断，完整名字放 tooltip / 状态栏。
        nm.ensurePolished()              # 让样式表的字号生效，否则度量是错的
        avail = max(12, w - 8)           # 左右内边距各 3，再留 2px 余量
        nm.setText(QFontMetrics(nm.font()).elidedText(item.name, Qt.ElideRight, avail))
        nm.setToolTip("%s ｜ %s" % (item.name, item.group))

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        if ev.button() == Qt.LeftButton:
            self.clicked.emit(self._id)
        super().mousePressEvent(ev)


class PanelBridge(QObject):
    """把非 GUI 线程的调用安全地搬到 GUI 线程。"""

    request_settings = Signal()
    request_open = Signal(int, int)
    request_notify = Signal(str, str)      # 任意线程 → GUI：托盘提示
    request_capture = Signal(bool, bool, bool)   # 任意线程 → GUI：发起截图
    request_model_question = Signal(str, bool)   # 任意线程 → GUI：本机模型提问
    model_answer_ready = Signal(str, str, str)   # 问题/OCR、答案、来源
    request_close = Signal(int, int)     # 外部点击（物理坐标）→ 判断关闭
    request_toggle = Signal(int, int)    # 中键再次按下 → 切换面板

    def __init__(self, panel: ActionPanel) -> None:
        super().__init__()
        self._panel = panel
        self.request_open.connect(self._on_open, Qt.QueuedConnection)
        self.request_close.connect(self._panel.close_if_outside, Qt.QueuedConnection)
        self.request_toggle.connect(self._on_toggle, Qt.QueuedConnection)

    def toggle_at(self, x: int, y: int) -> None:
        """任意线程可调用：切换面板（开<->关）。"""
        self.request_toggle.emit(int(x), int(y))

    def notify(self, level: str, message: str) -> None:
        """任意线程可调用：托盘提示（跨线程安全）。"""
        self.request_notify.emit(str(level), str(message))

    @Slot(int, int)
    def _on_toggle(self, x: int, y: int) -> None:
        modal = QApplication.activeModalWidget()
        if modal is not None:
            from .window_presenter import bring_forward
            bring_forward(modal)
            return
        # isVisible/hide 只能在 GUI 线程摸——钩子线程直接调
        # panel.isVisible()/close_panel() 是线程违规，症状是"时灵时不灵"。
        if self._panel.isVisible():
            self._panel.close_panel()
        else:
            self._panel.open_at(x, y)

    def open_at(self, x: int, y: int) -> None:
        """任意线程可调用。"""
        self.request_open.emit(int(x), int(y))

    @Slot(int, int)
    def _on_open(self, x: int, y: int) -> None:
        modal = QApplication.activeModalWidget()
        if modal is not None:
            from .window_presenter import bring_forward
            bring_forward(modal)
            return
        self._panel.open_at(x, y)


class OutsideClickFilter(QObject):
    """全局鼠标事件过滤器：点击面板外部时关闭。"""

    def __init__(self, panel: ActionPanel) -> None:
        super().__init__()
        self._panel = panel

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.MouseButtonPress and self._panel.isVisible():
            gp = ev.globalPosition().toPoint()
            if not self._panel.frameGeometry().contains(gp):
                self._panel.close_panel()
        return False
