#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WorkBuddy「Buddy加油站」每日签到 —— 接口直签优先，UI Automation 兜底。

双路径设计：
  主路径（首选，只读本地登录态）：
    读取本机 WorkBuddy 客户端的登录态文件，按客户端公开的信封格式在本地解开
    **当前登录用户自己的**访问凭据（客户端 5.6.2+ 起登录态为 AES-256-GCM 信封，
    详见 wb_auth_session.py），随后直接调用官方签到接口（查询状态 → 领取）。
    不开窗口、不动界面、不依赖无障碍树，客户端最小化/被遮挡也能领。
    该路径**只读**登录态文件，绝不修改、绝不删除，凭据不落盘、不打日志、不外发。
  兜底路径（界面点击）：
    本地登录态不可用 / 接口异常时，退回 UI Automation：
    ① 唤醒 Chromium 无障碍树（Electron 默认只暴露空壳）
    ② 找加油站面板；不在就「点头像 → 菜单点 Buddy加油站」
    ③ 读按钮文案：含「已领」→ 收工；否则点击领取（最多重试 3 轮）
    ④ 点掉「开心收下」确认框 ⑤ 关面板 → 写状态文件

  --api-only：只走接口直签路径，接口不可用就快速失败（退出码 4），绝不退回界面点击
    —— 供定时任务在无窗口打扰的前提下运行。

定时执行：请使用 WorkBuddy 自带的「自动化」功能按日触发本脚本（见 SKILL.md），
  无需安装任何系统级计划任务，也不会向注册表或启动项写入任何内容。

退出码：0 = 已领 / 领取成功；2 = 未能完成（需人工留意）；3 = 环境异常；
        4 = 接口不可用（--api-only 模式，等下轮重试）

Copyright (c) 2026 amtf. All rights reserved.
本文件为「WorkBuddy自动领积分」技能的组成部分，仅供授权范围内使用，
禁止复制、再分发或用于二次开发后对外发布。
"""

from __future__ import annotations

import argparse
import atexit
import base64
import ctypes
import io
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from ctypes import byref, wintypes

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import uiautomation as auto

auto.SetGlobalSearchTimeout(2.0)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.ReleaseMutex.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

# Win32 层直查主窗口：绕开 UIA 桌面枚举（后者在窗口增删瞬间会抛 COMError）
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
WB_CLASS = "Chrome_WidgetWin_1"   # WorkBuddy（Electron）主窗口类名
WB_TITLE = "WorkBuddy"

# ---- 系统级无障碍开关：Chromium 靠它决定是否构建完整无障碍树 ----
SPI_GETSCREENREADER = 0x0046
SPI_SETSCREENREADER = 0x0047
SPIF_SENDCHANGE = 0x0002
VK_ESCAPE = 0x1B

STATE_DIR = os.path.join(os.path.expanduser("~"), ".workbuddy", "wb-daily-credits", "state")
STATE_FILE = os.path.join(STATE_DIR, "fuel-state.json")
LOG_FILE = os.path.join(STATE_DIR, "fuel.log")
TRAVEL_FILE = os.path.join(STATE_DIR, "travel-state.json")

# ---- 实测控件 ID ----
STATION_AID = "fuel-station"          # 面板容器
CLAIM_AID = "fuel-expanded-claim"     # 签到按钮（name 即状态）
CLOSE_AID = "fuel-close"
STREAK_AID = "fuel-streak"
TOTAL_AID = "fuel-total"
AVATAR_AID = r"\:rs\:"                # 左下角头像（MenuItemControl）
MENU_AID = r"\:rr\:"                  # 头像菜单浮层

ENTRY_WORD = "Buddy加油站"
CLAIMED_WORDS = ("已领", "已签到", "已领取")
ACCEPT_WORDS = ("开心收下", "收下")

QUIET = False          # --json/--quiet 时抑制日志输出到 stdout（只留一行 JSON 或不输出）
_KEEP_FG = None        # 原前台窗口，收尾时还原


# --------------------------------------------------------------------------
# 单实例守卫：防止「开机自启 + 定时任务 + 手动」同时触发
# --------------------------------------------------------------------------
class SingleInstance:
    """基于 Windows 命名互斥体的进程单例锁。

    同名互斥体在系统中同一时刻只有一个进程能持有；第二次 acquire 返回 False
    即代表已有另一个 fuel-claim.py 实例在运行，直接跳过本次，避免并发点击客户端。
    """

    MUTEX_NAME = "Local\\WBDailyCredits_ClaimMutex"
    ERROR_ALREADY_EXISTS = 183

    def __init__(self) -> None:
        self._handle = None

    def acquire(self) -> bool:
        try:
            self._handle = kernel32.CreateMutexW(None, False, self.MUTEX_NAME)
            existed = ctypes.get_last_error()  # 必须紧跟调用后取
        except Exception:
            return True  # 任何异常都不阻塞主流程
        if not self._handle:
            return True  # 创建失败不阻塞，让主流程继续
        return existed != self.ERROR_ALREADY_EXISTS

    def release(self) -> None:
        if self._handle:
            try:
                kernel32.ReleaseMutex(self._handle)
            except Exception:
                pass
            try:
                kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None


def slot_window(spec: str) -> tuple[bool, int]:
    """解析 HH:MM[±m] 时段参数，返回 (是否在窗口内, 半窗分钟数)。

    默认 ±30 分钟窗口。支持跨零点：23:50±30 → 有效区间 [23:20, 00:20]。
    非法输入（非 HH:MM 或分钟越界）按“不限定时段”处理（always True）。
    """
    WIN = 30
    m = re.fullmatch(r"(\d{1,2}):(\d{2})(?:±(\d{1,3}))?", spec.strip())
    if not m:
        return True, WIN
    hh, mm = int(m.group(1)), int(m.group(2))
    if hh > 23 or mm > 59:
        return True, WIN
    half = WIN if m.group(3) is None else min(int(m.group(3)), 180)
    base = hh * 60 + mm
    now = time.localtime().tm_hour * 60 + time.localtime().tm_min
    lo, hi = (base - half) % 1440, (base + half) % 1440
    if lo <= hi:
        inside = lo <= now <= hi
    else:  # 跨零点
        inside = now >= lo or now <= hi
    return inside, half


# --------------------------------------------------------------------------
# 状态与日志
# --------------------------------------------------------------------------
def _ensure_dirs() -> None:
    os.makedirs(STATE_DIR, exist_ok=True)


def log(msg: str) -> None:
    _ensure_dirs()
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    if not QUIET:
        print(line, file=sys.stderr)
    try:
        with io.open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_state() -> dict:
    try:
        with io.open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_state(d: dict) -> None:
    _ensure_dirs()
    try:
        with io.open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log("状态写入失败: %s" % e)


def today() -> str:
    return time.strftime("%Y-%m-%d")


# --------------------------------------------------------------------------
# 无障碍树闸门：Chromium 只在「检测到无障碍客户端」时才构建完整树
# --------------------------------------------------------------------------
class A11yGate:
    """临时置位 SPI_SETSCREENREADER，促使 Chromium 展开无障碍树；退出时还原。

    实测：不置位时 Electron 只暴露 8 个空壳 PaneControl（rect 全为 0）；
    置位后约 10 秒内展开到 1000+ 控件。系统原值会被完整保留并恢复。
    """

    def __init__(self) -> None:
        self.saved = None
        self.changed = False

    def on(self) -> None:
        v = ctypes.c_int(0)
        if user32.SystemParametersInfoW(SPI_GETSCREENREADER, 0, byref(v), 0):
            self.saved = v.value
        if self.saved != 1:
            flag = ctypes.c_int(1)
            self.changed = bool(
                user32.SystemParametersInfoW(SPI_SETSCREENREADER, 1, byref(flag), SPIF_SENDCHANGE))
            log("置位 SPI_SETSCREENREADER（原值=%s）-> %s" % (self.saved, self.changed))
        else:
            log("SPI_SETSCREENREADER 已为 1，无需改动")

    def off(self) -> None:
        if self.changed and self.saved == 0:
            flag = ctypes.c_int(0)
            user32.SystemParametersInfoW(SPI_SETSCREENREADER, 0, byref(flag), SPIF_SENDCHANGE)
            log("已还原 SPI_SETSCREENREADER = 0")


# --------------------------------------------------------------------------
# 窗口 / 控件工具
# --------------------------------------------------------------------------
def find_wb():
    """找到 name 恰为 'WorkBuddy' 的主窗口。

    2026-09-29 修复：原写法 `for w in auto.GetRootControl().GetChildren()`
    把枚举调用写在了 try 之外 —— 桌面窗口在创建/销毁瞬间（例如客户端弹出
    「Buddy加油站」提醒浮层）UIA 取元素属性会抛
    COMError(-2147220991, '事件无法调用任何订户')，
    而该异常发生在**迭代器内部**，循环体里的 try 完全接不住，
    会一路冒泡到 main() 兜底 except → sys.exit(3)，当天就此错过。

    现在改为三级查找，全部包在 try 内：
      ① Win32 FindWindowW（不碰 UIA，0.000s，最快）
      ② UIA PaneControl 按名查找（注意：WorkBuddy 主窗口在 UIA 里是
         PaneControl 而非 WindowControl，ClassName=Chrome_WidgetWin_1）
      ③ 最后才退化到桌面枚举（保留原语义，但整体纳入 try）
    """
    # ① Win32 层直查：不触发 UIA 枚举，最稳最快
    try:
        hwnd = user32.FindWindowW(WB_CLASS, WB_TITLE)
        if hwnd:
            ctrl = auto.ControlFromHandle(hwnd)
            if ctrl is not None and (ctrl.Name or "").strip() == "WorkBuddy":
                return ctrl
    except Exception:
        pass

    # ② UIA 按名查找（重试若干次，覆盖"窗口刚开始创建"的瞬间）
    for _ in range(3):
        try:
            w = auto.PaneControl(searchDepth=1, Name="WorkBuddy")
            if w.Exists(0.5, 0.15):
                return w
        except Exception:
            pass
        time.sleep(0.25)

    # ③ 退化：枚举桌面顶层窗口（整体包 try，避免旧写法那个崩溃）
    try:
        for w in auto.GetRootControl().GetChildren():
            try:
                if (w.Name or "").strip() == "WorkBuddy":
                    return w
            except Exception:
                continue
    except Exception:
        pass
    return None


def snapshot(root, max_depth=26):
    """收集所有后代控件（含 AutomationId 与屏幕矩形）。"""
    rows = []
    stack = [(root, 0)]
    while stack:
        c, d = stack.pop()
        if d > max_depth:
            continue
        try:
            children = c.GetChildren()
        except Exception:
            children = []
        for ch in children:
            try:
                r = ch.BoundingRectangle
                rows.append({
                    "d": d,
                    "type": ch.ControlTypeName,
                    "name": (ch.Name or "").strip(),
                    "aid": (ch.AutomationId or ""),
                    "rect": (r.left, r.top, r.right, r.bottom),
                    "ctrl": ch,
                })
            except Exception:
                pass
            stack.append((ch, d + 1))
    return rows


def by_aid(rows, aid):
    for r in rows:
        if r["aid"] == aid:
            return r
    return None


def activate(hwnd) -> bool:
    """把窗口拉到前台。窗口不在前台时菜单/浮层往往点不开。"""
    try:
        user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        user32.ShowWindow(hwnd, 5)   # SW_SHOW
        time.sleep(0.4)
        if user32.GetForegroundWindow() == hwnd:
            return True
        fg = user32.GetForegroundWindow()
        tf = user32.GetWindowThreadProcessId(fg, None)
        tm = kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(tf, tm, True)
        user32.BringWindowToTop(hwnd)
        ok = user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(tf, tm, False)
        time.sleep(0.7)
        return bool(ok) or user32.GetForegroundWindow() == hwnd
    except Exception as e:
        log("激活窗口失败: %s" % e)
        return False


def remember_fg():
    global _KEEP_FG
    try:
        _KEEP_FG = user32.GetForegroundWindow()
    except Exception:
        _KEEP_FG = None


def restore_fg():
    """还原到脚本运行前的前台窗口，尽量不打扰用户。"""
    if not _KEEP_FG:
        return
    try:
        if user32.IsWindow(_KEEP_FG) and user32.GetForegroundWindow() != _KEEP_FG:
            user32.SetForegroundWindow(_KEEP_FG)
    except Exception:
        pass


def press_esc(times=2) -> None:
    """ESC 清理可能残留的菜单/浮层。"""
    for _ in range(times):
        try:
            user32.keybd_event(VK_ESCAPE, 0, 0, 0)
            time.sleep(0.05)
            user32.keybd_event(VK_ESCAPE, 0, 2, 0)
        except Exception:
            pass
    time.sleep(0.6)


def click(row, dry=False) -> bool:
    """点击控件。

    顺序很重要：Invoke 模式 → Legacy 默认动作 → 控件点击 → 坐标点击。
    实测加油站入口按钮的 rect 覆盖了「Buddy加油站」与「去邀约」两个区块，
    几何中心恰好落在分界线上，纯坐标点击点不开；必须优先用 Invoke。
    """
    if row is None:
        return False
    if dry:
        return True
    ctrl = row["ctrl"]
    for label, fn in (
        ("Invoke", lambda: ctrl.GetInvokePattern().Invoke()),
        ("Legacy", lambda: ctrl.GetLegacyIAccessiblePattern().DoDefaultAction()),
        ("Click", lambda: ctrl.Click(simulateMove=False)),
    ):
        try:
            fn()
            return True
        except Exception:
            continue
    # 坐标兜底：取纵向 30% 处，避开可能的分界线
    l, t, r, b = row["rect"]
    try:
        auto.Click((l + r) // 2, t + max(2, int((b - t) * 0.3)))
        return True
    except Exception:
        return False


def tree_ready(rows) -> bool:
    return len(rows) > 100 and any(r["type"] == "MenuItemControl" for r in rows)


def wake(timeout=60.0):
    """唤醒无障碍树并等待就绪，返回 (window, rows)。

    关键：置位系统开关 + 前台化窗口 + 轮询等待。三者缺一都可能拿到空壳。
    """
    t0 = time.time()
    w = None
    while time.time() - t0 < timeout:
        w = find_wb()
        if w is None:
            time.sleep(1.0)
            continue
        try:
            w.DocumentControl(searchDepth=3)
        except Exception:
            pass
        try:
            w.PaneControl(searchDepth=2)
        except Exception:
            pass
        rows = snapshot(w)
        if tree_ready(rows):
            log("无障碍树就绪：控件=%d，用时 %.1fs" % (len(rows), time.time() - t0))
            return w, rows
        # 树还没展开 —— 前台化通常能立刻促成
        activate(w.NativeWindowHandle)
        time.sleep(1.5)
    rows = snapshot(w) if w else []
    log("无障碍树未就绪（控件=%d，%.1fs）" % (len(rows), time.time() - t0))
    return w, rows


# --------------------------------------------------------------------------
# 加油站面板操作
# --------------------------------------------------------------------------
def find_avatar(rows):
    """头像：优先 aid == ':rs:'，否则取纵向最靠下的 MenuItemControl。"""
    r = by_aid(rows, AVATAR_AID)
    if r is not None and r["type"] == "MenuItemControl":
        return r
    av, bottom = None, -1
    for x in rows:
        if x["type"] == "MenuItemControl" and x["rect"][3] > bottom and x["rect"][3] > 0:
            bottom, av = x["rect"][3], x
    return av


def find_menu(rows):
    """头像菜单浮层。"""
    r = by_aid(rows, MENU_AID)
    if r is not None and r["rect"][3] > 0:
        return r
    for x in rows:
        if x["type"] == "MenuControl" and x["rect"][3] > 0:
            return x
    return None


def find_entry(rows, menu):
    """在菜单浮层内找 Buddy加油站 入口。

    注意：官方把「Buddy加油站」和「去邀约」合并进同一个 ButtonControl
    （name = 'Buddy加油站 去邀约 最高得 650 积分/人'），
    所以绝不能按「去邀约」排除；同时必须限定在菜单 rect 内，
    否则会误匹配到聊天区里提到「Buddy加油站」的文字。
    """
    ml, mt, mr, mb = menu["rect"]
    fallback = None
    for r in rows:
        if r["rect"][3] <= 0 or ENTRY_WORD not in r["name"]:
            continue
        if not (r["rect"][0] >= ml - 8 and r["rect"][1] >= mt - 8
                and r["rect"][2] <= mr + 8 and r["rect"][3] <= mb + 8):
            continue
        if r["type"] == "ButtonControl":
            return r
        if fallback is None:
            fallback = r
    return fallback


def open_menu(w, rows, avatar, attempts=5):
    """点头像展开菜单，返回 (window, rows, menu)。"""
    hwnd = w.NativeWindowHandle
    for i in range(1, attempts + 1):
        press_esc(1)
        log("展开头像菜单（第 %d 次）" % i)
        click(avatar)
        time.sleep(2.2)
        w2 = find_wb()
        if w2 is None:
            continue
        rows2 = snapshot(w2)
        menu = find_menu(rows2)
        if menu is not None:
            log("菜单已展开 rect=%s" % (menu["rect"],))
            return w2, rows2, menu
        if i >= 3:
            activate(hwnd)
    return w, rows, None


def open_station(w, rows):
    """确保加油站面板展开。返回 (window, rows, panel, claim)。"""
    # 已经开着？
    if by_aid(rows, STATION_AID) is not None and by_aid(rows, CLAIM_AID) is not None:
        log("加油站面板已展开")
        return w, rows, by_aid(rows, STATION_AID), by_aid(rows, CLAIM_AID)

    avatar = find_avatar(rows)
    if avatar is None:
        log("未找到头像控件")
        return w, rows, None, None
    log("头像 = %r rect=%s" % (avatar["name"], avatar["rect"]))

    w, rows, menu = open_menu(w, rows, avatar)
    if menu is None:
        log("头像菜单打不开")
        return w, rows, None, None

    entry = find_entry(rows, menu)
    if entry is None:
        log("菜单里没找到「%s」入口" % ENTRY_WORD)
        return w, rows, None, None
    log("入口 = %r rect=%s" % (entry["name"], entry["rect"]))

    log("点击入口，等待面板出现")
    click(entry)
    # 面板出现有延迟，轮询确认
    t0 = time.time()
    while time.time() - t0 < 15:
        time.sleep(1.0)
        w2 = find_wb()
        if w2 is None:
            continue
        rows2 = snapshot(w2)
        claim = by_aid(rows2, CLAIM_AID)
        if claim is not None:
            log("面板已出现（%.1fs）" % (time.time() - t0))
            return w2, rows2, by_aid(rows2, STATION_AID), claim
    log("面板未在 15s 内出现")
    return w, rows, None, None


def read_value(row):
    if row is None:
        return None
    try:
        v = row["ctrl"].GetValuePattern().Value
        if v and v.strip():
            return v.strip()
    except Exception:
        pass
    try:
        return (row["ctrl"].Name or "").strip() or None
    except Exception:
        return None


def _in_panel(rows, panel, pad=6):
    """面板 rect 内的控件。"""
    if panel is None:
        return []
    pl, pt, pr, pb = panel["rect"]
    out = []
    for r in rows:
        if r["rect"][3] <= 0:
            continue
        if (r["rect"][0] >= pl - pad and r["rect"][1] >= pt - pad
                and r["rect"][2] <= pr + pad and r["rect"][3] <= pb + pad):
            out.append(r)
    return out


def _nearest_number(probe_rows, anchor, max_dx=20, max_dy=25):
    """在 anchor 控件附近找承载数字的 TextControl。

    实测：fuel-streak / fuel-total 自身 Name 为空，数字是紧邻的 TextControl，
    且 x 与 anchor 精确对齐 —— '9' 在 x=208 对齐 fuel-streak(x=208)，
    '900' 在 x=309 对齐 fuel-total(x=309)，数字基线略高于标签（dy≈3）。
    """
    if anchor is None:
        return None
    ax, ay = anchor["rect"][0], anchor["rect"][1]
    best, best_d = None, None
    for r in probe_rows:
        if r["type"] != "TextControl" or not r["name"]:
            continue
        if not re.fullmatch(r"[\d,\.]+", r["name"].strip()):
            continue
        dx = abs(r["rect"][0] - ax)
        dy = abs(r["rect"][1] - ay)
        if dx <= max_dx and dy <= max_dy:
            d = dx + dy
            if best_d is None or d < best_d:
                best_d, best = d, r["name"].strip()
    return best


def read_stats(rows, panel):
    """读取「每日 N 分 / 已领 N 天 / 累计 N 分」。"""
    stats = {}
    pr_rows = _in_panel(rows, panel)
    if not pr_rows:
        return stats

    per_day = _nearest_number(pr_rows, by_aid(rows, "fuel-score"))
    if per_day:
        stats["per_day"] = per_day

    streak = _nearest_number(pr_rows, by_aid(rows, STREAK_AID))
    if streak:
        stats["streak"] = streak

    total = _nearest_number(pr_rows, by_aid(rows, TOTAL_AID))
    if total:
        stats["total"] = total

    # 兜底：若 x 对齐失效（官方改版），退回文本正则
    if not stats.get("streak") or not stats.get("total"):
        blob = "".join(r["name"] for r in sorted(
            [x for x in pr_rows if x["name"]], key=lambda x: (x["rect"][1], x["rect"][0])))
        if not stats.get("streak"):
            m = re.search(r"已领[^\d]{0,4}(\d+)[^\d]{0,4}天", blob)
            if m:
                stats["streak"] = m.group(1)
        if not stats.get("total"):
            m = re.search(r"累计领取[^\d]{0,4}([\d,\.]+)[^\d]{0,4}分", blob)
            if m:
                stats["total"] = m.group(1)
        if not stats.get("per_day"):
            m = re.search(r"(\d+)\s*分\s*/\s*天", blob)
            if m:
                stats["per_day"] = m.group(1)
    return stats


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
# ====================================================================
# 接口直签路径：优先读本机登录态解密取凭据，UIA 只是兜底
# --------------------------------------------------------------------
# 桌面客户端登录态文件（workbuddy-desktop.info）里的 accessToken 自 5.6.2 起
# 为 $wbEncrypted 信封加密（AES-256-GCM suite1）。本技能副本按客户端公开的
# 信封格式做「读端」兼容解密（见同目录 wb_auth_session.py），直接在本机把
# **当前登录用户自己的**凭据解出来调接口 —— 不开窗口、不动界面、不依赖
# 无障碍树，客户端最小化/被遮挡也能领。凭据只在内存流转，不落盘、不打日志。
#
# 下方 scan_token_from_memory 仅作**最后兜底**：当登录态文件不可用（客户端
# 异常、文件损坏等）时，才尝试从运行中的客户端读取凭据，保证老环境仍可用。
# ====================================================================

_API_BASE = "https://www.codebuddy.cn"
_JWT_RE = re.compile(rb"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{20,}")


def _find_wb_pids(name: str = "workbuddy.exe") -> list[int]:
    """列出 WorkBuddy.exe 的全部 PID（大小写不敏感）。"""
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    arr = (wintypes.DWORD * 8192)()
    cb = wintypes.DWORD()
    out: list[int] = []
    if not psapi.EnumProcesses(byref(arr), ctypes.sizeof(arr), byref(cb)):
        return out
    for i in range(cb.value // ctypes.sizeof(wintypes.DWORD)):
        pid = arr[i]
        if not pid:
            continue
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        buf = ctypes.create_unicode_buffer(512)
        sz = wintypes.DWORD(512)
        ok = k32.QueryFullProcessImageNameW(h, 0, buf, byref(sz))
        k32.CloseHandle(h)
        if ok and buf.value.lower().endswith(name):
            out.append(pid)
    return out


def _jwt_payload(token: str) -> dict:
    p2 = token.split(".")[1]
    p2 += "=" * (-len(p2) % 4)
    return json.loads(base64.urlsafe_b64decode(p2))


def scan_token_from_memory(timeout_s: float = 150.0) -> str | None:
    """【最后兜底】从运行中的客户端进程读取当前用户自己的凭据。

    仅在本地登录态文件不可用（客户端异常、文件损坏等）时调用；正常路径由
    `wb_auth_session.load_access_token()` 从登录态直接解密取得，不需走到这里。

    判定：长度 > 1000、payload.iss 含 codebuddy.cn、exp 未过期。
    找到即返回；超时/未找到返回 None。整个过程只读，凭据不落盘、不打日志。
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.ReadProcessMemory.argtypes = (
        wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t))
    k32.VirtualQueryEx.argtypes = (
        wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID, ctypes.c_size_t)
    pids = _find_wb_pids()
    if not pids:
        return None
    log("静默直签：扫描 %d 个客户端进程的内存…" % len(pids))
    deadline = time.time() + timeout_s
    now = time.time()

    class _MBI(ctypes.Structure):
        _fields_ = [("BaseAddress", ctypes.c_void_p),
                    ("AllocationBase", ctypes.c_void_p),
                    ("AllocationProtect", wintypes.DWORD),
                    ("RegionSize", ctypes.c_size_t),
                    ("State", wintypes.DWORD),
                    ("Protect", wintypes.DWORD),
                    ("Type", wintypes.DWORD)]

    mbi = _MBI()
    for pid in pids:
        if time.time() > deadline:
            break
        h = k32.OpenProcess(0x0410, False, pid)  # VM_READ | QUERY_INFORMATION
        if not h:
            continue
        addr = 0
        try:
            while time.time() < deadline:
                if k32.VirtualQueryEx(h, ctypes.c_void_p(addr), byref(mbi),
                                      ctypes.sizeof(mbi)) == 0:
                    break
                base = mbi.BaseAddress or 0
                size = mbi.RegionSize
                # 只读已提交的可读区域；跳过超大区域防超时
                if (mbi.State == 0x1000 and (mbi.Protect & 0xFF) in {0x02, 0x04, 0x20, 0x40}
                        and 0 < size <= 64 * 1024 * 1024):
                    buf = ctypes.create_string_buffer(size)
                    got = ctypes.c_size_t(0)
                    if k32.ReadProcessMemory(h, ctypes.c_void_p(base), buf,
                                             size, byref(got)) and got.value:
                        for m in _JWT_RE.finditer(buf.raw[:got.value]):
                            tok = m.group(0).decode("ascii", "ignore")
                            if len(tok) <= 1000:
                                continue
                            try:
                                pl = _jwt_payload(tok)
                            except Exception:
                                continue
                            if "codebuddy.cn" in str(pl.get("iss", "")) and pl.get("exp", 0) > now:
                                return tok
                addr = base + size
                if addr > 0x7FFFFFFEFFFF:
                    break
        finally:
            k32.CloseHandle(h)
    return None


def _api_post(token: str, path: str, timeout: float = 20.0):
    """调官方签到接口。返回 (http_status, body_dict|None)。

    网络层异常（连接被拒/超时/解析失败）统一返回 (None, None)，
    由调用方决定是否重试 —— 绝不让 URLError 冒泡打断整个流程。
    """
    req = urllib.request.Request(
        _API_BASE + path, data=b"{}", method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json",
                 "Authorization": "Bearer " + token,
                 "User-Agent": "WorkBuddy/CheckinScript"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, None
    except Exception as e:
        # URLError / ConnectionRefused / socket.timeout / ssl 错误等一律归为网络不可用
        log("接口网络异常（%s: %s）" % (path, e))
        return None, None


def _api_post_retry(token: str, path: str, attempts: int = 3,
                    gap: float = 5.0, timeout: float = 20.0):
    """带重试的接口调用：仅对网络层失败重试（HTTP 有响应则不重试）。

    开机初期代理软件常尚未就绪 → 连接被拒；此处短重试可让单次运行内自愈，
    避免把一次「网络抖动」放大成整轮失败。
    """
    last = (None, None)
    for i in range(1, attempts + 1):
        sc, body = _api_post(token, path, timeout=timeout)
        if sc is not None:      # 服务端有响应（含 4xx/5xx 业务码）→ 直接返回
            return sc, body
        last = (sc, body)
        if i < attempts:
            log("接口无响应，%.0fs 后重试（%d/%d）" % (gap, i, attempts))
            time.sleep(gap)
    return last


def _load_session_credential():
    """取得调用官方接口所需的登录凭据（只读、本地完成）。

    优先顺序：
      ① 本地登录态解密（`wb_auth_session` 模块，与客户端 at-rest-crypto 保持一致的
         读端兼容解密；密钥按 环境变量 → 密钥文件 → 本机密钥块 → 客户端进程内存
         四级策略自动定位）—— 正常路径通常走到第三级即可，**不需要读取任何进程**；
      ② 上一级全部不可用（例如客户端异常、登录态损坏）时，退回既有的进程内存读取
         作为最后兜底，保证老环境仍能工作。

    返回 (凭据, 域名, 来源说明)；全部失败返回 (None, None, None)。
    """
    # ① 本地登录态解密（主路径）
    try:
        import wb_auth_session as _ses
        token, domain, path = _ses.load_access_token()
        if token:
            log("已从本机登录态取得凭据（%s）" % os.path.basename(path))
            return token, domain, "auth-file"
    except ImportError:
        log("未找到登录态读取模块（wb_auth_session.py），改用其他途径")
    except Exception as e:
        log("本机登录态解密未成功（%s），尝试最后兜底途径" % type(e).__name__)

    # ② 最后兜底：从运行中的客户端进程内存读取（仅当上面完全不可用时）
    try:
        token = scan_token_from_memory(timeout_s=60.0)
        if token:
            log("已从客户端内存取得凭据（兜底途径）")
            return token, None, "process-memory"
    except Exception as e:
        log("兜底途径亦未成功：%s: %s" % (type(e).__name__, e))
    return None, None, None


def try_api_path(args, out) -> int | None:
    """静默直签主流程。成功返回退出码；返回 None 则由调用方落回 UIA 路径。"""
    global _LAST_TOKEN
    token = None
    try:
        token, _domain, _path = _load_session_credential()
        _LAST_TOKEN = token
    except Exception as e:
        log("读取本机登录态失败：%s: %s" % (type(e).__name__, e))
    if not token:
        log("未取得会话凭据（客户端未登录 / 不可用）→ 转界面兜底路径")
        return None

    sc, body = _api_post_retry(token, "/v2/billing/meter/checkin-activity-status")
    if sc != 200 or not isinstance(body, dict) or body.get("code") != 0:
        log("状态接口异常：HTTP=%s body=%s" % (sc, str(body)[:160]))
        return None
    data = body.get("data") or {}
    streak = str(data.get("streak_days", "?"))
    total = str(data.get("total_credits", "?"))

    def _state(summary):
        if not args.dry_run:
            write_state({"date": today(), "status": "ok", "summary": summary,
                         "via": "api", "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                         "streak": streak, "total": total})

    if data.get("today_checked_in"):
        log("接口确认：今日已领 ✔（连领 %s 天 / 累计 %s）" % (streak, total))
        _state("今日已领")
        return out(0, action="already", claimed=True, via="api",
                   summary="今日已领（连领 %s 天 / 累计 %s）" % (streak, total),
                   streak=streak, total=total)

    if args.dry_run:
        log("试运行：接口确认今日未领，未执行领取")
        return out(0, action="dry-run", claimed=False, via="api",
                   summary="试运行：今日未领（连领 %s 天 / 累计 %s）" % (streak, total),
                   streak=streak, total=total)

    cc, cbody = _api_post_retry(token, "/v2/billing/meter/daily-checkin")
    if isinstance(cbody, dict) and cbody.get("code") == 0:
        # 领取成功：重查一次状态拿最新连领/累计
        sc2, b2 = _api_post_retry(token, "/v2/billing/meter/checkin-activity-status")
        d2 = (b2 or {}).get("data") or {} if isinstance(b2, dict) else {}
        streak = str(d2.get("streak_days", cbody.get("streak_days", "?")))
        total = str(d2.get("total_credits", "?"))
        credit = cbody.get("credit") or cbody.get("data", {}).get("credit", 100)
        log("领取成功 ✔ +%s（连领 %s 天 / 累计 %s）" % (credit, streak, total))
        if not args.dry_run:
            write_state({"date": today(), "status": "ok", "summary": "领取成功",
                         "via": "api", "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                         "streak": streak, "total": total})
        return out(0, action="claimed", claimed=True, via="api",
                   summary="领取成功 +%s（连领 %s 天 / 累计 %s）" % (credit, streak, total),
                   streak=streak, total=total)
    if isinstance(cbody, dict) and cbody.get("code") == 10001:
        log("接口返回：今天已签到（幂等确认）")
        _state("今日已领")
        return out(0, action="already", claimed=True, via="api",
                   summary="今日已领（连领 %s 天 / 累计 %s）" % (streak, total),
                   streak=streak, total=total)
    log("领取接口异常：HTTP=%s body=%s → 转界面兜底路径" % (cc, str(cbody)[:160]))
    return None


# --------------------------------------------------------------------------
# 派猫猫旅行（v2.4.0）：与签到不同域（www.workbuddy.cn，路径不带 /v2）
# --------------------------------------------------------------------------
_TRAVEL_BASE = "https://www.workbuddy.cn"
_LAST_TOKEN: str | None = None


def _travel_call(token: str, path: str, method: str = "GET",
                 payload=None, timeout: float = 20.0):
    """调旅行接口。返回 (http_status, body_dict|None)。"""
    data = None
    if method == "POST":
        data = json.dumps(payload if payload is not None else {}).encode()
    req = urllib.request.Request(
        _TRAVEL_BASE + path, data=data, method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json",
                 "Authorization": "Bearer " + token,
                 "User-Agent": "WorkBuddy/CheckinScript"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, None
    except Exception:
        return None, None


def _travel_call_retry(token: str, path: str, method: str = "GET",
                       payload=None, attempts: int = 3, gap: float = 4.0):
    """带重试的旅行只读调用：仅对「无响应」（网络层）重试，有 HTTP 响应即返回。"""
    last = (None, None)
    for i in range(1, attempts + 1):
        sc, body = _travel_call(token, path, method, payload)
        if sc is not None:
            return sc, body
        last = (sc, body)
        if i < attempts:
            time.sleep(gap)
    return last


def travel_loop(token: str, dry: bool = False) -> dict:
    """旅行闭环：查状态 → arrived 则领取 → idle 且未达上限则派遣（先领后派）。

    安全约束：
      * claim 只在 state==arrived 时发；depart 只在 state==idle 且
        daily_limit_reached 为假时发，其余情况绝不发写请求；
      * 只读接口失败 → 整体静默跳过，绝不影响签到主流程；
      * 派出零风险：到达后永久保持 arrived，不领不丢，下次运行补领。
    返回 {"available": bool, "state": str|None, "summary": str, "actions": [..]}。
    """
    acts: list[str] = []
    sc, body = _travel_call_retry(token, "/activity/growth/buddy/travel/status")
    if sc != 200 or not isinstance(body, dict) or body.get("code") != 0:
        log("[旅行] 状态接口不可用（HTTP=%s），本次跳过" % sc)
        return {"available": False, "state": None,
                "summary": "旅行状态不可用（已跳过）", "actions": acts}

    d = body.get("data") or {}
    state = d.get("state")
    loc = (d.get("location") or {}).get("name")

    # ---- ① arrived → 领取 ----
    if state == "arrived":
        if dry:
            log("[旅行] 猫猫已在 %s 到达（试运行，不领取）" % (loc or "?"))
            acts.append("dry: would-claim")
        else:
            cc, cb = _travel_call(token, "/activity/growth/buddy/travel/claim",
                                  "POST", {})
            if isinstance(cb, dict) and cb.get("code") == 0:
                credit = (cb.get("data") or {}).get("reward_credit", "?")
                log("[旅行] 到达奖励领取成功 +%s ✔" % credit)
                acts.append("claimed +%s" % credit)
            else:
                log("[旅行] 领取未成功（HTTP=%s body=%s）" % (cc, str(cb)[:120]))
                acts.append("claim-failed")
    elif state == "traveling" and isinstance(d.get("arrive_at"), int):
        remain = max(0, d["arrive_at"] - d.get("server_now", 0))
        log("[旅行] 猫猫旅行中（%s），约 %d 分钟后到达" % (loc or "?", remain // 60))
        acts.append("traveling")

    # ---- ② 重查状态（领取后状态会变） ----
    sc, body = _travel_call(token, "/activity/growth/buddy/travel/status")
    if sc == 200 and isinstance(body, dict) and body.get("code") == 0:
        d = body.get("data") or d
        state = d.get("state")

    # ---- ③ idle 且未达上限 → 派遣 ----
    if state == "idle" and not d.get("daily_limit_reached"):
        if dry:
            log("[旅行] 空闲且未达每日上限（试运行，不派遣）")
            acts.append("dry: would-depart")
        else:
            ids = []
            _, cfg = _travel_call(token, "/activity/growth/buddy/travel/config")
            if isinstance(cfg, dict) and cfg.get("code") == 0:
                ids = [l.get("id") for l in (cfg.get("data") or {}).get("locations", [])
                       if l.get("id") is not None]
            loc_id = random.choice(ids) if ids else 1  # 地点收益无差异，随机即可
            dc, db = _travel_call(token, "/activity/growth/buddy/travel/depart",
                                  "POST", {"location_id": loc_id})
            if isinstance(db, dict) and db.get("code") == 0:
                dd = db.get("data") or {}
                name = (dd.get("location") or {}).get("name", str(loc_id))
                arrive = dd.get("arrive_at")
                eta = time.strftime("%H:%M", time.localtime(arrive)) \
                    if isinstance(arrive, int) else "?"
                log("[旅行] 已派往 %s，预计 %s 到达（5-10 积分待领）" % (name, eta))
                acts.append("departed→%s" % name)
                state = "traveling"
            else:
                log("[旅行] 派遣未成功（HTTP=%s body=%s）" % (dc, str(db)[:120]))
                acts.append("depart-failed")
    elif d.get("daily_limit_reached"):
        log("[旅行] 今日派遣已达上限")
        acts.append("daily-limit-reached")

    summary = "、".join(acts) if acts else "无动作（%s）" % (state or "unknown")
    if not dry:
        _ensure_dirs()
        try:
            with io.open(TRAVEL_FILE, "w", encoding="utf-8") as f:
                json.dump({"at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "state": state, "actions": acts},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return {"available": True, "state": state, "summary": summary, "actions": acts}


def _maybe_travel(args, result) -> None:
    """在签到收尾点顺带跑旅行闭环（--no-travel 关闭；结果并入 JSON 的 travel 字段）。"""
    if getattr(args, "no_travel", False):
        return
    token = _LAST_TOKEN
    if not token:
        try:
            token = scan_token_from_memory(timeout_s=60.0)
        except Exception:
            token = None
    if not token:
        log("[旅行] 无可用会话令牌，跳过旅行")
        return
    try:
        tr = travel_loop(token, dry=bool(args.dry_run))
        result["travel"] = {"state": tr.get("state"), "actions": tr.get("actions")}
    except Exception as e:
        log("[旅行] 异常（不影响签到）：%s: %s" % (type(e).__name__, e))


def main() -> int:
    global QUIET

    ap = argparse.ArgumentParser(description="WorkBuddy Buddy加油站 每日签到")
    ap.add_argument("--dry-run", action="store_true", help="只检测状态，不点击")
    ap.add_argument("--force", action="store_true", help="忽略「当天已领」短路")
    ap.add_argument("--json", action="store_true", help="只输出一行 JSON")
    ap.add_argument("--keep-open", action="store_true", help="结束后不关闭面板（排障）")
    ap.add_argument("--slot", metavar="HH:MM[±mm]",
                    help="仅在该时段 ±30 分钟内执行（如 09:10；可写 09:10±15 自定义窗口，跨零点也支持）")
    ap.add_argument("--quiet", action="store_true", help="静音：stdout 不输出任何内容，仅写日志文件")
    ap.add_argument("--no-travel", action="store_true",
                    help="关闭派猫猫旅行闭环（默认签到收尾时会顺带先领后派）")
    ap.add_argument("--travel-only", action="store_true",
                    help="只跑旅行闭环，不做签到（供高频轮询任务使用）")
    ap.add_argument("--api-only", action="store_true",
                    help="只走静默直签路径，接口不可用立即退出码4，绝不弹界面（开机守护专用）")
    args = ap.parse_args()
    QUIET = args.json or args.quiet

    result = {"code": 0, "action": "none", "claimed": False}

    def out(code, **kw):
        result.update(kw)
        result["code"] = code
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
        elif code == 0 and not args.quiet:
            print("结论：%s" % result.get("summary", "完成"))
        return code

    # ---- 时段窗口：--slot 之外的时间直接跳过（自启/定时任务按点跑时很省事） ----
    if args.slot:
        inside, win_min = slot_window(args.slot)
        if not inside:
            log("--slot %s：当前 %s 不在窗口内（±%d 分钟），跳过" % (
                args.slot, time.strftime("%H:%M"), win_min))
            return out(0, action="slot-skip", summary="不在指定时段内（已跳过）")

    # ---- 单实例守卫：防自启 + 定时任务 + 手动同时触发 ----
    guard = SingleInstance()
    if not guard.acquire():
        log("已有另一个实例在运行，跳过本次")
        return out(0, action="dup-skip", summary="已有实例在运行（已跳过）")
    atexit.register(guard.release)  # 任何返回/异常路径都会解锁（进程退出时 OS 也会兜底）

    # ---- --travel-only：只跑旅行闭环，不做签到（高频轮询专用） ----
    if args.travel_only:
        global _LAST_TOKEN
        token = None
        try:
            token = scan_token_from_memory(timeout_s=90.0)
            _LAST_TOKEN = token
        except Exception as e:
            log("内存扫描异常：%s: %s" % (type(e).__name__, e))
        if not token:
            return out(3, action="error", reason="no-token",
                       summary="未取得会话令牌（客户端未运行？）")
        tr = travel_loop(token)
        return out(0, action="travel", travel=tr.get("actions"),
                   summary="旅行：%s" % tr.get("summary"))

    # ---- 当日短路：今天已成功领过就彻底安静（旅行闭环仍要跑） ----
    st = read_state()
    if not args.force and not args.dry_run and st.get("date") == today() and st.get("status") == "ok":
        log("今天（%s）已领过，静默退出" % today())
        _maybe_travel(args, result)
        return out(0, action="skip", summary="今日已领（已跳过）")

    # ---- ⓪ 静默直签（v2.3.0）：官方接口优先，不开窗口不打扰；失败落回 UIA 兜底 ----
    rc = try_api_path(args, out)
    if rc is not None:
        _maybe_travel(args, result)
        return rc

    # ---- --api-only：守护模式绝不弹界面，接口不可用就快速失败等下轮 ----
    if args.api_only:
        return out(4, action="error", reason="api-unavailable",
                   summary="静默接口不可用（--api-only 模式，等下轮重试）")

    gate = A11yGate()
    try:
        # ---- ① 唤醒无障碍树 ----
        remember_fg()
        gate.on()
        w, rows = wake()
        if w is None:
            log("未找到 WorkBuddy 窗口（客户端可能没在运行）")
            return out(3, action="error", reason="no-window", summary="未找到客户端窗口")
        if not tree_ready(rows):
            # 假期后首启实测无障碍树展开极慢（曾 132.8s），wake() 的窗口可能不够。
            # 此时 SPI 开关仍置位，树仍在持续展开 —— 再给一轮宽限，避免白跑一天。
            log("无障碍树未就绪，追加等待（保持 SPI 置位）…")
            deadline = time.time() + 45
            while time.time() < deadline:
                time.sleep(3.0)
                w = find_wb() or w
                rows = snapshot(w)
                if tree_ready(rows):
                    log("补等后无障碍树就绪：控件=%d" % len(rows))
                    break
        if not tree_ready(rows):
            log("无障碍树未就绪，无法读取界面控件")
            return out(3, action="error", reason="no-a11y-tree", summary="无障碍树未就绪")

        # ---- ② 打开加油站面板 ----
        w, rows, panel, claim = open_station(w, rows)
        if panel is None or claim is None:
            return out(2, action="error", reason="open-failed", summary="打不开加油站面板")

        stats = read_stats(rows, panel)
        name = claim["name"]
        log("签到按钮文案=%r | 统计=%s" % (name, stats))

        # ---- ③ 已领则直接收工 ----
        if any(k in name for k in CLAIMED_WORDS):
            if not args.keep_open:
                click(by_aid(rows, CLOSE_AID))
            if not args.dry_run:
                write_state({
                    "date": today(), "status": "ok", "summary": "今日已领",
                    "button": name, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **stats,
                })
            log("今日已领 ✔ 无需动作")
            return out(0, action="already", claimed=True,
                       summary="今日已领（已领 %s 天 / 累计 %s）" % (
                           stats.get("streak", "?"), stats.get("total", "?")), **stats)

        if args.dry_run:
            log("试运行：检测到【未领取】，未执行点击")
            if not args.keep_open:
                click(by_aid(rows, CLOSE_AID))
            return out(0, action="dry-run", claimed=False, button=name,
                       summary="试运行：今日未领，按钮=%r" % name, **stats)

        # ---- ④ 领取 ----
        # 2026-09-29 加固：原先只点一次，失败即报 unverified 退出。
        # 实测 9/28 08:31 节后首启时无障碍树展开极慢（288 控件、132.8s），
        # 面板虽已出现但数字/按钮未渲染完，点击落空 → 当天第一次尝试白跑。
        # 现改为最多 3 轮「点击 → 等 3s → 复读状态」，未变「已领」就重试。
        log("检测到未领取（按钮=%r），执行点击" % name)
        before = {(r["aid"], r["name"]) for r in rows}
        ok = False
        name2 = None
        rows2 = rows
        done = False
        for attempt in range(1, 4):
            ok = click(claim) or ok
            time.sleep(3.0)

            w = find_wb()
            rows2 = snapshot(w)

            # 领到后会弹确认框，点掉才算真正到账
            accept = None
            for r in rows2:
                if r["type"] == "ButtonControl" and any(k in r["name"] for k in ACCEPT_WORDS):
                    accept = r
                    break
            if accept is None:
                for r in sorted(rows2, key=lambda x: x["rect"][1]):
                    if (r["type"] == "ButtonControl" and r["name"]
                            and r["aid"] != CLOSE_AID and r["rect"][2] > r["rect"][0]
                            and (r["aid"], r["name"]) not in before):
                        accept = r
                        break
            if accept is not None:
                log("出现确认框，点击 %r" % accept["name"])
                click(accept)
                time.sleep(2.5)
                w = find_wb()
                rows2 = snapshot(w)
            elif attempt == 1:
                log("未发现确认框（可能直接到账）")

            claim2 = by_aid(rows2, CLAIM_AID)
            name2 = claim2["name"] if claim2 else None
            log("点击后按钮文案=%r（第 %d 轮）" % (name2, attempt))

            if name2 and any(k in name2 for k in CLAIMED_WORDS):
                done = True
                break
            # 面板尚未渲染出按钮 / 状态没刷新 —— 重新定位按钮后再来一轮
            claim = by_aid(rows2, CLAIM_AID) or claim
            time.sleep(1.5)

        stats2 = read_stats(rows2, by_aid(rows2, STATION_AID))

        if not args.keep_open:
            click(by_aid(rows2, CLOSE_AID))

        if done:
            write_state({
                "date": today(), "status": "ok", "summary": "领取成功",
                "button": name2, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **stats2,
            })
            log("领取成功 ✔")
            return out(0, action="claimed", claimed=True, clicked=ok,
                       summary="已领取（已领 %s 天）" % stats2.get("streak", "?"), **stats2)

        log("点击后状态未变为「已领」，需人工确认")
        return out(2, action="unverified", claimed=False, clicked=ok, button_after=name2,
                   summary="点击了但未能确认结果")

    finally:
        gate.off()
        restore_fg()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # 兜底：任何异常都不能让计划任务卡住
        import traceback
        log("异常: %s" % e)
        log(traceback.format_exc())
        sys.exit(3)
