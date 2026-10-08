# -*- coding: utf-8 -*-
"""窗口追踪层 + 文字采集层

── 窗口追踪 ──────────────────────────────────────────────
绑定微信 / 飞书 / 钉钉等目标窗口，持续跟踪其位置与尺寸变化。
关键点：坐标全部用「虚拟桌面物理像素」。实测本机为双 2560x1080 纵向堆叠，
副屏 y 为负值，任何按「相对屏幕」的假设都会错位。

── 文字采集 ──────────────────────────────────────────────
唯一可用的取屏方式是：
    PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT=2)
实测结论（P0-3，5 种窗口 × 3 种方式）：
    * PrintWindow(hwnd, 0)          → 全部 Chromium/Electron 窗口 100% 黑屏
    * BitBlt（屏幕抓取）             → 可用，但窗口被遮挡时会抓到遮挡物
    * PrintWindow(hwnd, 2)          → 全部成功，且**不受遮挡影响**
所以固定用第 3 种。抓帧成本实测：飞书 26ms、通达信 93ms。

── 变化门控 ──────────────────────────────────────────────
OCR 单帧约 280ms，绝不能挂在定时器上硬跑。
改为先用「降采样指纹 + 差异像素占比」做门控，只有画面真变了才触发 OCR。
实测空转噪声仅 0.001%，阈值取 0.2% 即可获得极高的信噪比。
"""
import ctypes
import ctypes.wintypes as wt
import os
import time
from dataclasses import dataclass, field

import numpy as np

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # Per-Monitor DPI Aware V2
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

PW_RENDERFULLCONTENT = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]

kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]

# 支持的识别源
SOURCE_PROCESSES = {
    "Weixin.exe": "微信 4.x",
    "WeChat.exe": "微信 3.x",
    "DingTalk.exe": "钉钉",
    "Feishu.exe": "飞书",
    "Lark.exe": "飞书",
    "WXWork.exe": "企业微信",
    "QQ.exe": "QQ",
}


@dataclass
class WindowInfo:
    hwnd: int
    pid: int
    exe: str
    title: str
    rect: tuple          # 窗口矩形（虚拟桌面坐标）
    client: tuple        # 客户区矩形（虚拟桌面坐标）
    app: str = ""        # 友好名，如「飞书」

    @property
    def width(self):
        return self.client[2] - self.client[0]

    @property
    def height(self):
        return self.client[3] - self.client[1]

    @property
    def offset(self):
        """客户区相对窗口左上角的偏移（裁掉标题栏用）"""
        return (self.client[0] - self.rect[0], self.client[1] - self.rect[1])

    def alive(self):
        return bool(user32.IsWindow(self.hwnd))

    def __repr__(self):
        return "<%s 0x%08X %dx%d@%d,%d %r>" % (
            self.app or self.exe, self.hwnd, self.width, self.height,
            self.client[0], self.client[1], self.title[:24])


def _proc_exe(pid):
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        n = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def _text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 2)
    user32.GetWindowTextW(hwnd, buf, n + 2)
    return buf.value


def window_info(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = os.path.basename(_proc_exe(pid.value))
    wr = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    cr = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(cr))
    pt = wt.POINT(cr.left, cr.top)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return WindowInfo(
        hwnd=hwnd, pid=pid.value, exe=exe, title=_text(hwnd),
        rect=(wr.left, wr.top, wr.right, wr.bottom),
        client=(pt.x, pt.y, pt.x + (cr.right - cr.left), pt.y + (cr.bottom - cr.top)),
        app=SOURCE_PROCESSES.get(exe, exe),
    )


def find_windows(proc_names=None, min_size=(300, 200), visible_only=True):
    """枚举目标进程的可用窗口，按面积降序"""
    names = None
    if proc_names:
        names = {n.lower() for n in proc_names}
    hits = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        info = window_info(hwnd)
        if names is not None and info.exe.lower() not in names:
            return True
        if not names and info.exe not in SOURCE_PROCESSES:
            return True
        if info.width >= min_size[0] and info.height >= min_size[1]:
            hits.append(info)
        return True

    user32.EnumWindows(cb, 0)
    hits.sort(key=lambda w: -w.width * w.height)
    return hits


# ---------------------------------------------------------------- 取屏
def grab_window(info, crop_client=True):
    """PrintWindow(PW_RENDERFULLCONTENT) 抓一帧，返回 RGB numpy 数组"""
    import win32gui
    import win32ui

    l, t, r, b = info.rect
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        return None
    hdc = win32gui.GetWindowDC(info.hwnd)
    mfc = win32ui.CreateDCFromHandle(hdc)
    dc = mfc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc, w, h)
    dc.SelectObject(bmp)
    ok = ctypes.windll.user32.PrintWindow(info.hwnd, dc.GetSafeHdc(), PW_RENDERFULLCONTENT)
    info_bmp = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)
    arr = np.frombuffer(bits, dtype=np.uint8).reshape(
        (info_bmp["bmHeight"], info_bmp["bmWidth"], 4))
    win32gui.DeleteObject(bmp.GetHandle())
    dc.DeleteDC()
    mfc.DeleteDC()
    win32gui.ReleaseDC(info.hwnd, hdc)
    if not ok:
        return None
    rgb = arr[:, :, :3][:, :, ::-1].copy()
    if crop_client:
        ox, oy = info.offset
        rgb = rgb[max(oy, 0):max(oy, 0) + info.height,
                  max(ox, 0):max(ox, 0) + info.width]
    return rgb


# ---------------------------------------------------------------- 变化门控
class ChangeGate:
    """帧差门控：只有画面真的变化才触发昂贵的 OCR

    实测空转噪声 0.001%，阈值 0.2% 就能得到极高的信噪比。
    """

    def __init__(self, scale=8, thresh=0.002, luma_jump=22):
        self.scale = scale
        self.thresh = thresh
        self.luma_jump = luma_jump
        self._prev = None

    def _fingerprint(self, arr):
        small = arr[::self.scale, ::self.scale]
        if small.ndim == 3:
            small = small.mean(axis=2)
        return small.astype(np.int16)

    def changed(self, arr):
        cur = self._fingerprint(arr)
        if self._prev is None or self._prev.shape != cur.shape:
            self._prev = cur
            return True
        d = float((np.abs(cur - self._prev) > self.luma_jump).mean())
        self._prev = cur
        return d > self.thresh

    def reset(self):
        self._prev = None


class WindowWatcher:
    """轮询监听一批窗口，在画面变化时回调"""

    def __init__(self, proc_names=None, poll_interval=0.2, scale=8, thresh=0.002):
        self.proc_names = proc_names
        self.poll_interval = poll_interval
        self._gates = {}
        self._stopped = False

    def poll_once(self):
        """返回 {hwnd: (WindowInfo, np.ndarray)}，只含发生变化的窗口"""
        results = {}
        for info in find_windows(self.proc_names):
            gate = self._gates.get(info.hwnd)
            if gate is None:
                gate = ChangeGate(scale=8, thresh=0.002)
                self._gates[info.hwnd] = gate
            arr = grab_window(info)
            if arr is None or arr.size == 0:
                continue
            if gate.changed(arr):
                results[info.hwnd] = (info, arr)
        # 清理已关闭的窗口
        alive = {i.hwnd for i in find_windows(self.proc_names)}
        for hwnd in list(self._gates):
            if hwnd not in alive:
                del self._gates[hwnd]
        return results

    def run(self, on_change, duration=None):
        """阻塞式轮询，on_change(info, arr) 在画面变化时被调用"""
        t_end = time.time() + duration if duration else None
        while not self._stopped:
            for hwnd, (info, arr) in self.poll_once().items():
                on_change(info, arr)
            if t_end and time.time() >= t_end:
                break
            time.sleep(self.poll_interval)

    def stop(self):
        self._stopped = True


if __name__ == "__main__":
    print("=" * 88)
    print("窗口追踪层自检")
    print("=" * 88)
    wins = find_windows()
    if not wins:
        print("  未发现任何支持的聊天客户端窗口")
    for w in wins:
        print("  %s" % w)
        print("      客户区 %dx%d @(%d,%d)  窗口偏移 %s"
              % (w.width, w.height, w.client[0], w.client[1], w.offset))

    print("\n取屏 + 门控实测：")
    import time as _t
    for w in wins[:2]:
        ts = []
        for _ in range(5):
            t0 = _t.perf_counter()
            arr = grab_window(w)
            ts.append((_t.perf_counter() - t0) * 1000)
        if arr is None:
            print("  %-20s 抓帧失败" % w.app)
            continue
        gate = ChangeGate()
        gate.changed(arr)
        same = gate.changed(arr)
        print("  %-20s 抓帧 %.0f ms  尺寸 %dx%d  连抓两帧判定变化=%s"
              % (w.app, min(ts), arr.shape[1], arr.shape[0], same))
