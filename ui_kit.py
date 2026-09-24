# -*- coding: utf-8 -*-
"""ui_kit —— Tkinter 美化控件库（主题化版，无第三方依赖）。

配套：outputs/ui_theme_preview.py（预览程序）。确认后再移入项目根目录被主程序引用。

⚠️ 使用前提（顺序不能变）：
    在 `import tkinter` **之前** 调用 enable_dpi_awareness()，否则 150%/200% 缩放下
    Tk 逻辑像素与屏幕物理像素不一致。

设计要点：
  * 颜色全部来自 Skin 实例，换肤 = 换 Skin 对象 + 重建界面，绘制代码不用动。
  * 尺寸一律过 u()（乘 DPI 缩放）；字号用「点」，Tk 自动按 DPI 换算（且只接受整数点值）。
  * Tk 无圆角/投影/渐变 → 全部 Canvas 自绘。
"""
import ctypes
import time

# ⚠️ 立即生效（在本模块 import tkinter 之前）：任何 `import ui_kit` 都自动获得 DPI 感知。
#    调用方必须保证 ui_kit 是本程序里最早接触 tkinter 的模块（别先 import tkinter）。
def _apply_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()       # 老系统兜底
        except Exception:
            pass


_apply_dpi_awareness()

import tkinter as tk
from tkinter import ttk


def enable_dpi_awareness():
    """同上（公开别名）。注意：必须在 import tkinter 之前调用才有效。"""
    _apply_dpi_awareness()


# ───────────────────────── 缩放与字体 ─────────────────────────
SCALE = 1.0          # 由 setup_scale(root) 按实际 DPI 覆盖


def setup_scale(root):
    """按屏幕实际 DPI 推导缩放比（150% → 1.5）。"""
    global SCALE
    try:
        SCALE = root.winfo_fpixels("1i") / 96.0
    except Exception:
        SCALE = 1.0
    return SCALE


def u(v):
    """逻辑尺寸 → 物理像素。"""
    return int(round(v * SCALE))


FONT_UI = "Microsoft YaHei UI"


def f(size, bold=False):
    """字号用「点」，Tk 按 DPI 自动换算。⚠️ 只接受整数点值。"""
    size = int(round(size))
    return (FONT_UI, size, "bold") if bold else (FONT_UI, size)


def parent_bg(widget, fallback="#FFFFFF"):
    try:
        return widget.cget("bg")
    except Exception:
        return fallback


# ───────────────────────── 皮肤 ─────────────────────────
class Skin:
    """一套皮肤：色板 + 形态参数。"""

    def __init__(self, key, name, desc, **kw):
        self.key, self.name, self.desc = key, name, desc
        # 色板
        self.bg = "#F4F6FA"
        self.card = "#FFFFFF"
        self.border = "#E4E8F0"
        self.text = "#1F2937"
        self.muted = "#6B7280"
        self.faint = "#9AA3AF"
        self.accent = "#2563EB"
        self.accent_d = "#1D4ED8"
        self.accent_l = "#EEF2FF"
        self.track = "#E8ECF3"
        self.ok = "#15803D"
        self.warn = "#B45309"
        self.bad = "#B91C1C"
        self.grad_a = "#1E3A8A"
        self.grad_b = "#4F46E5"
        # 形态
        self.header = "flat"          # flat | sidebar | hero
        self.option_style = "check"   # check | pill
        self.picker_style = "entry"   # entry | dropzone
        self.radius_card = 10
        self.radius_btn = 9
        self.shadow = True
        self.btn_h = 48
        self.font_base = 10
        self.sidebar_w = 200
        self.win = (900, 690)
        self.__dict__.update(kw)

    def palette(self):
        return {k: v for k, v in self.__dict__.items() if isinstance(v, str)}


SKINS = {
    "A": Skin("A", "卡片化", "保持原有信息架构，只换皮 —— 改动最小",
              header="flat", option_style="check", picker_style="entry",
              radius_card=10, radius_btn=9, shadow=True, btn_h=48,
              win=(900, 690)),
    "B": Skin("B", "侧边栏", "左侧导航栏，功能分组更清晰",
              header="sidebar", option_style="check", picker_style="entry",
              radius_card=10, radius_btn=9, shadow=True, btn_h=48,
              sidebar_w=200, win=(1020, 700)),
    "C": Skin("C", "品牌头图", "顶部渐变头图 + 大号选择区，视觉最强",
              header="hero", option_style="pill", picker_style="dropzone",
              radius_card=12, radius_btn=10, shadow=False, btn_h=52,
              win=(920, 750)),
}
SKIN_ORDER = ("A", "B", "C")


# ───────────────────────── 绘制原语 ─────────────────────────
def rr(cv, x1, y1, x2, y2, r, fill, tags=None):
    """圆角矩形填充：4 个扇形 + 2 个矩形拼合（outline 同色，避免接缝）。"""
    tags = tags or ()
    r = max(0, r)
    kw = dict(fill=fill, outline=fill, tags=tags)
    d = 2 * r
    if r > 0:
        cv.create_arc(x1, y1, x1 + d, y1 + d, start=90, extent=90, style="pieslice", **kw)
        cv.create_arc(x2 - d, y1, x2, y1 + d, start=0, extent=90, style="pieslice", **kw)
        cv.create_arc(x1, y2 - d, x1 + d, y2, start=180, extent=90, style="pieslice", **kw)
        cv.create_arc(x2 - d, y2 - d, x2, y2, start=270, extent=90, style="pieslice", **kw)
    cv.create_rectangle(x1 + r, y1, x2 - r, y2, **kw)
    cv.create_rectangle(x1, y1 + r, x2, y2 - r, **kw)


def rr_border(cv, x1, y1, x2, y2, r, border, fill, tags=None):
    """带 1px 描边的圆角矩形 = 外圈描边色 + 内缩 1px 填充色。"""
    rr(cv, x1, y1, x2, y2, r, border, tags)
    rr(cv, x1 + 1, y1 + 1, x2 - 1, y2 - 1, max(0, r - 1), fill, tags)


def h_gradient(cv, x1, y1, x2, y2, c1, c2, steps=160):
    """水平渐变：堆细竖线（Tk 无渐变）。"""
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    w = x2 - x1
    for i in range(steps):
        t = i / (steps - 1)
        c = "#%02X%02X%02X" % (int(r1 + (r2 - r1) * t),
                               int(g1 + (g2 - g1) * t),
                               int(b1 + (b2 - b1) * t))
        cv.create_rectangle(x1 + w * i / steps, y1, x1 + w * (i + 1) / steps + 1, y2,
                            fill=c, outline=c)


def text_w(cv, text, font):
    """量文字宽度（临时画一个再删）。"""
    t = cv.create_text(-9999, -9999, text=text, font=font)
    b = cv.bbox(t)
    cv.delete(t)
    return (b[2] - b[0]) if b else 0


def shadow(cv, x1, y1, x2, y2, r, base_bg="#F4F6FA"):
    """伪投影：卡片下方叠 3 层渐淡圆角块（Tk 无真阴影）。"""
    for i, tone in enumerate(("#E7EBF3", "#ECEFF6", "#F1F4F9"), start=1):
        rr(cv, x1 + i, y1 + i + 1, x2 + i, y2 + i + 1, r, tone)


def logo_mark(cv, x, y, size, sk, fill=None, line_color="#FFFFFF"):
    """应用图标：圆角方块 + 3 条横线（模拟票据）。"""
    fill = fill or sk.accent
    rr(cv, x, y, x + size, y + size, size * 0.25, fill)
    for i, t in enumerate((0.36, 0.50, 0.64)):
        cv.create_line(x + size * 0.26, y + size * t,
                       x + size * (0.74 if i < 2 else 0.58), y + size * t,
                       fill=line_color, width=u(1.6), capstyle="round")


# ───────────────────────── 控件 ─────────────────────────
class Card(tk.Canvas):
    """圆角白卡：外部布局正常 pack/grid，内部内容塞进 self.body。

    auto=True  → 高度由内容决定（普通卡片）
    fill=True  → 高度由父容器分配（日志卡片）
    """

    def __init__(self, master, sk, radius=None, pad=16, auto=True, fill=False):
        self.sk = sk
        self.pad = u(pad)
        self.auto, self.fill_mode = auto, fill
        self.radius = u(radius if radius is not None else sk.radius_card)
        bg = parent_bg(master, sk.bg)
        super().__init__(master, bg=bg, highlightthickness=0, bd=0,
                         height=u(40) if auto else u(60))
        self.body = tk.Frame(self, bg=sk.card)
        self._win = self.create_window(self.pad, self.pad, window=self.body,
                                       anchor="nw")
        self.body.bind("<Configure>", self._on_body)
        self.bind("<Configure>", self._on_canvas)

    def _on_body(self, e):
        if self.auto:
            want = e.height + 2 * self.pad
            if want != self.winfo_height():
                self.configure(height=want)

    def _on_canvas(self, e):
        w = max(1, e.width - 2 * self.pad)
        if self.fill_mode:
            self.itemconfigure(self._win, width=w,
                               height=max(1, e.height - 2 * self.pad))
        else:
            self.itemconfigure(self._win, width=w)
        self._paint(e.width, e.height)

    def _paint(self, w, h):
        if not self.winfo_exists():
            return
        self.delete("cardbg")
        if w < 4 or h < 4:
            return
        if self.sk.shadow:
            shadow(self, 0, 0, w - u(3), h - u(3), self.radius)
        rr_border(self, 0, 0, w - 1, h - 1, self.radius, self.sk.border,
                  self.sk.card, ("cardbg",))
        self.tag_lower("cardbg")


class RoundButton(tk.Canvas):
    """自绘圆角按钮。kind: primary | ghost | danger。"""

    def __init__(self, master, sk, text="", command=None, kind="primary",
                 height=None, width=None, font_size=12, icon=None):
        self.sk = sk
        self.text, self.command, self.kind, self.icon = text, command, kind, icon
        self.font_size = font_size
        self._enabled = True
        self._hover = False
        bg = parent_bg(master, sk.card)
        h = u(height if height is not None else sk.btn_h)
        kw = dict(height=h, bg=bg, highlightthickness=0, bd=0)
        if width:
            kw["width"] = u(width)
        super().__init__(master, **kw)
        self.configure(cursor="hand2")
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._click)

    def configure_state(self, state="normal", text=None):
        """state: normal | disabled；text 给了就换文案。"""
        self._enabled = (state == "normal")
        if text is not None:
            self.text = text
        self.configure(cursor="hand2" if self._enabled else "arrow")
        self._draw()

    def _colors(self):
        sk = self.sk
        if not self._enabled:
            if self.kind == "ghost":
                return "#FBFCFE", sk.faint, sk.border
            return "#DCE4F5", "#9FB4E0", None
        if self.kind == "primary":
            return sk.accent_d if self._hover else sk.accent, "#FFFFFF", None
        if self.kind == "danger":
            return "#FEF2F2" if self._hover else sk.card, sk.bad, sk.border
        return "#F3F5F9" if self._hover else sk.card, sk.text, sk.border

    def _draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        fill, fg, bd = self._colors()
        if bd:
            rr_border(self, 1, 1, w - 2, h - 2, u(self.sk.radius_btn), bd, fill)
        else:
            rr(self, 1, 1, w - 2, h - 2, u(self.sk.radius_btn), fill)
        cx, cy = w / 2, h / 2
        if self.icon:
            tw = text_w(self, self.text, f(self.font_size, True))
            iw = text_w(self, self.icon, f(self.font_size - 3))
            total = iw + u(10) + tw
            x0 = cx - total / 2
            self.create_text(x0, cy, text=self.icon, font=f(self.font_size - 3),
                             fill=fg, anchor="w")
            self.create_text(x0 + iw + u(10), cy, text=self.text,
                             font=f(self.font_size, True), fill=fg, anchor="w")
        else:
            self.create_text(cx, cy, text=self.text, font=f(self.font_size, True),
                             fill=fg)

    def _enter(self, _e):
        self._hover = True
        self._draw()

    def _leave(self, _e):
        self._hover = False
        self._draw()

    def _click(self, _e):
        if self._enabled and self.command:
            self.command()


class RoundCheck(tk.Canvas):
    """自绘复选框（18px 圆角方块 + 对勾）。"""

    def __init__(self, master, sk, text="", variable=None, font_size=None):
        self.sk = sk
        self.text = text
        self.var = variable or tk.BooleanVar(value=False)
        self.font_size = font_size or sk.font_base
        bg = parent_bg(master, sk.card)
        self._size = u(18)
        self._tw = None
        super().__init__(master, bg=bg, highlightthickness=0, bd=0,
                         height=self._size, width=u(400), cursor="hand2")
        self._var_trace = self.var.trace_add("write", self._on_var)
        # ⚠️ 换肤会销毁控件，但 trace 还挂在共享的 BooleanVar 上 → 必须在销毁时摘掉，
        #    否则之后每次 set() 都会调已销毁控件的 _draw()，报 invalid command name
        self.bind("<Destroy>", self._on_destroy)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._toggle)

    def _on_var(self, *_):
        self._draw()

    def _on_destroy(self, e):
        if e.widget is not self:
            return
        try:
            self.var.trace_remove("write", self._var_trace)
        except Exception:
            pass

    def _toggle(self, _e):
        self.var.set(not self.var.get())

    def _draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        h = self.winfo_height()
        s = self._size
        y = (h - s) / 2
        x = u(2)
        if self.var.get():
            rr(self, x, y, x + s, y + s, u(4), self.sk.accent)
            self.create_line(x + s * 0.26, y + s * 0.52, x + s * 0.44, y + s * 0.70,
                             x + s * 0.76, y + s * 0.30, fill="#FFFFFF",
                             width=u(2), capstyle="round", joinstyle="round")
        else:
            rr_border(self, x, y, x + s, y + s, u(4), "#CBD3DF", self.sk.card)
        self.create_text(x + s + u(9), h / 2, text=self.text,
                         font=f(self.font_size), fill=self.sk.text, anchor="w")


class PillToggle(tk.Canvas):
    """胶囊开关（C 皮肤用）：选中 = 浅蓝底 + 圆点 + 主色加粗文字。"""

    def __init__(self, master, sk, text="", variable=None, font_size=9):
        self.sk = sk
        self.text = text
        self.var = variable or tk.BooleanVar(value=False)
        self.font_size = font_size
        bg = parent_bg(master, sk.card)
        self._h = u(30)
        super().__init__(master, bg=bg, highlightthickness=0, bd=0,
                         height=self._h, width=u(80), cursor="hand2")
        # ⚠️ 必须等 super().__init__ 之后才能量文字宽度（此前 widget 没有 tk 句柄）
        # ⚠️ 不能叫 self._w —— tkinter 内部用 self._w 存控件路径名，覆盖它会直接崩
        self.pill_w = text_w(self, text, f(font_size, True)) + u(52)
        self.configure(width=self.pill_w)
        self._var_trace = self.var.trace_add("write", self._on_var)
        self.bind("<Destroy>", self._on_destroy)   # 同 RoundCheck：销毁时摘掉 trace
        self.bind("<Button-1>", lambda e: self.var.set(not self.var.get()))
        self.bind("<Configure>", lambda e: self._draw())

    def _on_var(self, *_):
        self._draw()

    def _on_destroy(self, e):
        if e.widget is not self:
            return
        try:
            self.var.trace_remove("write", self._var_trace)
        except Exception:
            pass

    def _draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        h = self.winfo_height()
        w = self.winfo_width()
        if h < 4 or w < 4:
            return
        on = self.var.get()
        if on:
            rr(self, 0, 0, w - 1, h - 1, h / 2, self.sk.accent_l)
            r = u(5)
            cx, cy = u(15), h / 2
            self.create_oval(cx - r, cy - r, cx + r, cy + r,
                             fill=self.sk.accent, outline=self.sk.accent)
            self.create_text(u(26), h / 2, text=self.text,
                             font=f(self.font_size, True),
                             fill=self.sk.accent_d, anchor="w")
        else:
            rr_border(self, 0, 0, w - 1, h - 1, h / 2, self.sk.border, self.sk.card)
            r = u(4)
            cx, cy = u(13), h / 2
            self.create_oval(cx - r, cy - r, cx + r, cy + r,
                             fill=self.sk.card, outline="#C3CBD8")
            self.create_text(u(24), h / 2, text=self.text,
                             font=f(self.font_size), fill=self.sk.muted, anchor="w")


class RoundProgress(tk.Canvas):
    """圆角进度条；停止态用琥珀色。"""

    def __init__(self, master, sk, height=10, width=None):
        bg = parent_bg(master, sk.card)
        kw = dict(bg=bg, highlightthickness=0, bd=0, height=u(height))
        if width:
            kw["width"] = u(width)
        super().__init__(master, **kw)
        self.sk = sk
        self._value = 0.0
        self._stopped = False
        self.bind("<Configure>", lambda e: self._draw())

    def set_value(self, pct, stopped=False):
        try:
            pct = float(pct)
        except (TypeError, ValueError):
            pct = 0.0
        self._value = max(0.0, min(100.0, pct))
        self._stopped = bool(stopped)
        self._draw()

    def reset(self):
        self.set_value(0)

    def _draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        track = self.sk.track
        fill = self.sk.warn if self._stopped else self.sk.accent
        rr(self, 0, 1, w - 1, h - 2, (h - 3) / 2, track)
        fw = (w - 1) * self._value / 100.0
        if fw >= h - 2:
            rr(self, 0, 1, fw, h - 2, (h - 3) / 2, fill)
        elif fw > 2:
            rr(self, 0, 1, 2 * (h - 3) / 2 + 2, h - 2, (h - 3) / 2, fill)


class NavItem(tk.Canvas):
    """侧栏导航项：选中 = 浅蓝底 + 左侧主色竖条 + 主色加粗文字。"""

    def __init__(self, master, sk, text="", active=False, command=None, height=40):
        self.sk = sk
        self.text = text
        self.active = active
        self.command = command
        self._hover = False
        super().__init__(master, bg=parent_bg(master, sk.card),
                         highlightthickness=0, bd=0, height=u(height),
                         cursor="hand2")
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: command and command())

    def _set_hover(self, v):
        self._hover = v
        self._draw()

    def set_active(self, v):
        self.active = v
        self._draw()

    def _draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10:
            return
        sk = self.sk
        x1, y1, x2, y2 = u(12), u(2), w - u(12), h - u(2)
        if self.active:
            rr(self, x1, y1, x2, y2, u(8), sk.accent_l)
            rr(self, x1, y1 + u(10), x1 + u(3), y2 - u(10), u(1.5), sk.accent)
            col, fnt = sk.accent_d, f(10.5, True)
        elif self._hover:
            rr(self, x1, y1, x2, y2, u(8), "#F5F7FB")
            col, fnt = sk.text, f(10.5)
        else:
            col, fnt = sk.muted, f(10.5)
        self.create_text(u(30), h / 2, text=self.text, font=fnt, fill=col, anchor="w")


class SegmentedControl(tk.Canvas):
    """分段选择器：等分若干档，点击回调。（用于切换外观）"""

    def __init__(self, master, sk, options, value, command,
                 width=None, height=30, tone="light"):
        self.sk = sk
        self.options = list(options)          # [(key, label), ...]
        self.value = value
        self.command = command
        self.tone = tone                      # light: 浅底槽 | dark: 深色底（头图上）
        bg = parent_bg(master, sk.card)
        super().__init__(master, bg=bg, highlightthickness=0, bd=0,
                         height=u(height), width=u(width or 180), cursor="hand2")
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", self._click)

    def set_value(self, key):
        self.value = key
        self._draw()

    def _draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10:
            return
        sk = self.sk
        if self.tone == "dark":
            trough, sel_bg = "#FFFFFF", "#FFFFFF"
            sel_fg, fg = sk.grad_a, "#FFFFFF"
        else:
            trough, sel_bg = "#EDF0F6", sk.card
            sel_fg, fg = sk.accent_d, sk.muted
        rr(self, 0, 0, w - 1, h - 1, u(8), trough)
        n = len(self.options)
        seg = w / n
        for i, (key, label) in enumerate(self.options):
            x1 = i * seg
            x2 = x1 + seg
            if key == self.value:
                rr(self, x1 + u(2), u(2), x2 - u(2), h - u(2), u(6), sel_bg)
                self.create_text((x1 + x2) / 2, h / 2, text=label,
                                 font=f(9, True), fill=sel_fg)
            else:
                self.create_text((x1 + x2) / 2, h / 2, text=label,
                                 font=f(9), fill=fg)

    def _click(self, e):
        w = self.winfo_width()
        n = len(self.options)
        if n == 0:
            return
        i = min(n - 1, max(0, int(e.x / (w / n))))
        key = self.options[i][0]
        if key != self.value:
            self.value = key
            self._draw()
            if self.command:
                self.command(key)


class DropZone(tk.Canvas):
    """大号点击选择区（C 皮肤）。点击触发 command，路径文字由 set_path 更新。"""

    def __init__(self, master, sk, command=None, height=120):
        bg = parent_bg(master, sk.card)
        super().__init__(master, bg=bg, highlightthickness=0, bd=0,
                         height=u(height), cursor="hand2")
        self.sk = sk
        self.command = command
        self.path = ""
        self.hover = False
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", lambda e: self.command and self.command())

    def set_path(self, path):
        self.path = path or ""
        self._draw()

    def _set_hover(self, v):
        self.hover = v
        self._draw()

    def _draw(self):
        if not self.winfo_exists():
            return
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 20 or h < 20:
            return
        sk = self.sk
        border = sk.accent if self.hover else "#C7D2FE"
        rr_border(self, 0, 1, w - 1, h - 2, u(sk.radius_card), border, sk.card)
        self.create_rectangle(u(9), u(10), w - u(9), h - u(11),
                              outline="#C7D2FE", dash=(u(6), u(4)))
        cx = w / 2
        rr(self, cx - u(19), u(22), cx + u(19), u(60), u(10), sk.accent_l)
        rr(self, cx - u(11), u(33), cx - u(2), u(39), u(1.5), sk.accent)
        rr(self, cx - u(12), u(37), cx + u(12), u(53), u(2.5), sk.accent)
        self.create_text(cx, u(78), text="点击此处选择发票文件夹",
                         font=f(11, True), fill=sk.text)
        if self.path:
            self.create_text(cx, u(97), text="当前：" + self.path,
                             font=f(9), fill=sk.muted)


class LogView(tk.Frame):
    """带分级着色的日志区（✓ 绿 / ⚠ 琥珀 / ✗ 红 / 标题蓝）。"""

    def __init__(self, master, sk, pad=0):
        super().__init__(master, bg=sk.card)
        self.sk = sk
        self.txt = tk.Text(self, bd=0, highlightthickness=0, bg=sk.card,
                           fg=sk.text, font=f(9), wrap="word",
                           padx=u(2), pady=u(2), spacing1=u(1), spacing3=u(2))
        self.txt.tag_configure("ok", foreground=sk.ok)
        self.txt.tag_configure("warn", foreground=sk.warn)
        self.txt.tag_configure("bad", foreground=sk.bad)
        self.txt.tag_configure("hl", foreground=sk.accent_d)
        self.txt.tag_configure("dim", foreground=sk.faint)
        self.sb = ttk.Scrollbar(self, orient="vertical", style="P.Vertical.TScrollbar",
                                command=self.txt.yview)
        self.txt.configure(yscrollcommand=self.sb.set)
        self.sb.pack(side=tk.RIGHT, fill=tk.Y, padx=(u(4), 0))
        self.txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.txt.configure(state=tk.DISABLED)

    @staticmethod
    def tag_for(msg):
        if "✓" in msg:
            return "ok"
        if "⚠" in msg:
            return "warn"
        if "✗" in msg:
            return "bad"
        if msg.startswith("===") or msg.startswith("---"):
            return "hl"
        return None

    def append(self, msg):
        self.txt.configure(state=tk.NORMAL)
        self.txt.insert(tk.END, msg + "\n", self.tag_for(msg) or ())
        self.txt.see(tk.END)
        self.txt.configure(state=tk.DISABLED)

    def dump(self):
        """取出所有 (文本, tag) 用于换肤时保留内容。"""
        out = []
        for i, line in enumerate(self.txt.get("1.0", tk.END).splitlines()):
            tags = self.txt.tag_names("%d.0" % (i + 1))
            tag = next((t for t in ("ok", "warn", "bad", "hl", "dim") if t in tags), None)
            out.append((line, tag))
        return out

    def load(self, lines):
        self.txt.configure(state=tk.NORMAL)
        self.txt.delete("1.0", tk.END)
        for text, tag in lines:
            self.txt.insert(tk.END, text + "\n", tag or ())
        self.txt.see(tk.END)
        self.txt.configure(state=tk.DISABLED)


def style_ttk(sk):
    """把 ttk 的 Entry / Scrollbar 调成与皮肤一致的扁平风。"""
    st = ttk.Style()
    try:
        st.theme_use("clam")
    except Exception:
        pass
    st.configure("P.TEntry", fieldbackground=sk.card, foreground=sk.text,
                 insertcolor=sk.text, bordercolor=sk.border, lightcolor=sk.border,
                 darkcolor=sk.border, padding=(u(8), u(7)), relief="flat")
    st.configure("P.Vertical.TScrollbar", gripcount=0, background="#C9D2DE",
                 darkcolor="#C9D2DE", lightcolor="#C9D2DE",
                 troughcolor="#F6F8FB", bordercolor="#F6F8FB",
                 arrowcolor=sk.card, arrowsize=u(10))
