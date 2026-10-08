# -*- coding: utf-8 -*-
"""联动执行层

点击高亮词 → 在目标行情软件里定位到该股。

═══ 核心发现（P0 实测，2026-10）═══

【方案 A｜通达信 UWM_STOCK 广播消息】★ 首选，已验证成功
    通达信注册了一个自定义窗口消息：
        UINT UWM_STOCK = RegisterWindowMessage("Stock");
        PostMessage(HWND_BROADCAST, UWM_STOCK, MARKET*1000000 + CODE, 0);
    实测 600519 → 7600519、000001 → 6000001 均成功跳转。

    这条路的最大价值：**不需要抢前台、不需要输入焦点、不打断用户打字**。
    因为它是消息级注入，通达信在自己的消息循环里处理，用户当前在哪个
    窗口打字完全不受影响。

【方案 A2｜同花顺 0x0490 跨进程内存消息】★ 同花顺首选（2026-10-07 实测）
    同花顺接受一条自定义消息 0x0490：
        payload = chr(0x11)+代码（沪）/ chr(0x21)+代码（深）
        mem = VirtualAllocEx(同花顺进程)   # 目标进程里开内存
        WriteProcessMemory(同花顺进程, mem, payload)
        SendMessageTimeout(hwnd, 0x0490, 0, mem)   # lParam 传指针
        VirtualFreeEx + CloseHandle
    竞品 WzSLinker V9.2.6 逆向（out/wzs_ths_dis.txt）用的就是这条通道；
    本机实测：happ.exe 主窗口**最小化状态下**发 600519/000858 均成功切换
    （⚠ 窗口标题不变，验证要看窗口内容不能看标题）。
    同样零键盘零抢焦点，北交所代码（4/8/9 开头）无对应编码，回落键盘精灵。

【方案 B｜键盘精灵（SendInput）】兜底
    通达信 / 同花顺 / 东方财富 PC 版传统上都支持「敲 6 位代码 + 回车」跳转。
    实测坑极多，务必注意：
      * Windows 前台锁会让 SetForegroundWindow 静默失败，需 AttachThreadInput 绕
      * 通达信的「综合复盘聚合版」等网页型版面会把焦点交给内嵌 CEF，
        此时 SendInput / PostMessage(WM_CHAR) / WM_KEYDOWN 全部无效（已实测）
      * 按下第一个数字键后焦点会转移到键盘精灵窗口，后续按键需要重新取焦点
    因此键盘精灵只作为非通达信目标或方案 A 失效时的兜底。

【方案 C｜网页行情页】最后兜底
    东财 / 同花顺网页版，一定能用，但不是「与本地客户端联动」。
"""
import ctypes
import ctypes.wintypes as wt
import os
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---------------- 通达信自定义消息 ----------------
HWND_BROADCAST = 0xFFFF
TDX_MSG_NAME = "Stock"
# 编码规则：沪市代码前加 7，其它市场前加 6
TDX_MARKET_SH = 7
TDX_MARKET_SZ = 6

# ---------------- 同花顺 0x0490 内存消息 ----------------
THS_MSG_STOCK = 0x0490           # SendMessage 消息号（WzSLinker 同款，实测有效）
THS_MARKET_PREFIX = {"sh": "\x11", "sz": "\x21"}
VIRTUAL_MEM = 0x3000             # MEM_COMMIT | MEM_RESERVE
PAGE_READWRITE = 0x04
MEM_RELEASE = 0x8000
PROCESS_ALL_ACCESS = 0x1F0FFF
PROCESS_VM_WRITE = 0x0020 | 0x0008 | 0x0010   # VM_WRITE|VM_OPERATION|VM_READ，权限不够时的降级选项
SMTO_ABORTIFHUNG = 0x0002

user32.RegisterWindowMessageW.argtypes = [wt.LPCWSTR]
user32.RegisterWindowMessageW.restype = wt.UINT
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
                                       wt.UINT, wt.UINT, ctypes.POINTER(wt.DWORD)]
user32.SendMessageTimeoutW.restype = wt.BOOL
kernel32.VirtualAllocEx.argtypes = [wt.HANDLE, wt.LPVOID, ctypes.c_size_t,
                                    wt.DWORD, wt.DWORD]
kernel32.VirtualAllocEx.restype = wt.LPVOID
kernel32.WriteProcessMemory.argtypes = [wt.HANDLE, wt.LPVOID, wt.LPCVOID,
                                        ctypes.c_size_t,
                                        ctypes.POINTER(ctypes.c_size_t)]
kernel32.WriteProcessMemory.restype = wt.BOOL
kernel32.VirtualFreeEx.argtypes = [wt.HANDLE, wt.LPVOID, ctypes.c_size_t, wt.DWORD]
kernel32.VirtualFreeEx.restype = wt.BOOL

# ---------------- 目标软件注册表 ----------------
# ⚠ 进程名必须按真机实测填。踩过的坑：
#   东方财富写 EMClient.exe → 实际主程序是 mainfree.exe（另有 maintrade/stockway）
#   同花顺写 Hevo.exe       → 老版主程序是 hexin.exe；但用户机上装的是
#     【同花顺远航版】—— 主程序叫 happ.exe（2026-10-07 EnumWindows 实测，
#     可见窗口「同花顺远航版」← happ.exe），只认 hexin.exe 会导致
#     process_running 永远判否 → 浮条上永远没有「看同花顺」按钮。
#   名字对不上时 find_process_window 永远返回空，表现为「软件明明开着却说未运行」。
TARGETS = {
    "tdx": {"proc": ("TdxW.exe",), "label": "通达信", "method": "uwm_stock"},
    "em": {"proc": ("mainfree.exe", "maintrade.exe", "stockway.exe"),
           "label": "东方财富", "method": "keyboard",
           # 2026-10-07 提速：真机结论「东财 0.03/键会丢键」保留 0.07，
           # 前后固定等待砍半以上（0.5+0.35+0.3 → 0.25+0.15+0.15）
           "delay": 0.07, "settle": 1.2,
           "act_wait": 0.25, "esc_wait": 0.15, "enter_wait": 0.15},
    "ths": {"proc": ("hexin.exe", "happ.exe"),
            "label": "同花顺", "method": "ths_mem",   # 首选 0x490 内存消息
            "delay": 0.05, "settle": 1.2,
            "act_wait": 0.25, "esc_wait": 0.15, "enter_wait": 0.15},
    "emweb": {"proc": None, "label": "东方财富(网页)", "method": "url"},
    "thsweb": {"proc": None, "label": "同花顺(网页)", "method": "url"},
}

SW_RESTORE, SW_SHOW = 9, 5
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_RETURN, VK_ESCAPE = 0x0D, 0x1B
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    # ⚠ 必须把三个分支都列全。只写 KEYBDINPUT 的话 union 只有 24 字节，
    #   sizeof(INPUT) 会算成 32，而 x64 上 Windows 期望的是 40
    #   （type 8 + union 32，因为 MOUSEINPUT 才是最大的分支）。
    #   传错 cbSize 时 SendInput 直接返回 0 / ERROR_INVALID_PARAMETER，
    #   而且**不报错**——静默什么都不做，极难排查。
    #   实测：cbSize=32 → 返回 0；cbSize=40 → 返回 1。
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


# x64 上 INPUT 必须是 40 字节；32 位是 28。不匹配说明结构体定义漏了分支。
_EXPECT_INPUT_SIZE = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
assert ctypes.sizeof(INPUT) == _EXPECT_INPUT_SIZE, (
    "INPUT 结构体大小 %d 不对（应为 %d），SendInput 会静默失败"
    % (ctypes.sizeof(INPUT), _EXPECT_INPUT_SIZE))


class WINDOWPLACEMENT(ctypes.Structure):
    """用 rcNormalPosition 拿「还原后」的窗口矩形。

    窗口最小化时 GetWindowRect 返回的是任务栏按钮大小
    （实测同花顺 / 通达信都是 160x28），拿它当「窗口多小」会直接把
    主窗口误判成噪声窗口丢掉。
    """
    _fields_ = [("length", wt.UINT), ("flags", wt.UINT), ("showCmd", wt.UINT),
                ("ptMinPosition", wt.POINT), ("ptMaxPosition", wt.POINT),
                ("rcNormalPosition", wt.RECT)]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG),
                ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_char * 260)]


user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.IsIconic.argtypes = [wt.HWND]
user32.SetFocus.argtypes = [wt.HWND]
user32.GetForegroundWindow.restype = wt.HWND
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowPlacement.argtypes = [wt.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.GetWindowPlacement.restype = wt.BOOL

kernel32.GetCurrentThreadId.restype = wt.DWORD
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
kernel32.Process32First.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
kernel32.Process32Next.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
kernel32.CloseHandle.restype = wt.BOOL


# ---------------------------------------------------------------- 工具
def market_prefix(code):
    """6 位代码 → 交易所前缀

    2026-10-07 修正北交所判据：除 43/83/87 外，**920 开头也是北交所**
    （对照 WzSLinker 逆向的编码表：7=沪 60/68，6=深 00/30，4=北 43/83/87/92）。
    注意 900xxx 是沪市 B 股，不能用「9 开头」一刀切。
    """
    if code.startswith(("4", "8", "92")):
        return "bj"
    if code.startswith(("6", "9")):
        return "sh"
    return "sz"


def tdx_encode(code):
    """通达信消息参数编码：沪市前加 7、深市前加 6、北交所前加 4"""
    mp = market_prefix(code)
    head = {"sh": TDX_MARKET_SH, "sz": TDX_MARKET_SZ, "bj": 4}[mp]
    return head * 1000000 + int(code)


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
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _effective_rect(hwnd):
    """窗口的「有效矩形」：最小化时返回还原矩形，否则返回当前矩形。"""
    r = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return (0, 0, 0, 0)
    if user32.IsIconic(hwnd):
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            n = wp.rcNormalPosition
            if n.right > n.left and n.bottom > n.top:
                return (n.left, n.top, n.right, n.bottom)
    return (r.left, r.top, r.right, r.bottom)


def find_process_window(proc_name, min_size=(400, 300)):
    """找出某进程（或任一候选进程）面积最大的「主窗口」。

    proc_name 可以是字符串，也可以是候选名元组 —— 东财主程序会在
    mainfree.exe / maintrade.exe / stockway.exe 之间切换，只认一个会漏。

    返回 (hwnd, area, rect, title, iconic)。调用方如果要往窗口里注入按键，
    iconic=True 时必须先 ShowWindow(SW_RESTORE)。

    ⚠ 最小化陷阱（2026-10 实测）：窗口最小化后 GetWindowRect 只剩任务栏按钮
      大小（同花顺 160x28、通达信 160x28）。老逻辑「可见 && 尺寸达标」会把
      最小化的主窗口整个丢掉，于是 process_running 判「未运行」，
      **菜单里的联动项直接消失** —— 用户看到的现象是「怎么没有同花顺联动」。
      修法：尺寸拿 _effective_rect（最小化时用还原矩形），可见性放宽到
      「可见 or 最小化」。
    """
    names = {proc_name.lower()} if isinstance(proc_name, str) \
        else {n.lower() for n in proc_name}
    hits = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if os.path.basename(_proc_exe(pid.value)).lower() not in names:
            return True
        iconic = bool(user32.IsIconic(hwnd))
        # 菜单类/托盘类窗口（隐藏且未最小化）不算候选
        if not user32.IsWindowVisible(hwnd) and not iconic:
            return True
        rect = _effective_rect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w < min_size[0] or h < min_size[1]:
            return True
        hits.append((hwnd, w * h, rect, _text(hwnd), iconic))
        return True

    user32.EnumWindows(cb, 0)
    return max(hits, key=lambda x: x[1]) if hits else None


def list_processes():
    """{exe 名小写: [pid, ...]} —— 一次进程快照，不依赖任何窗口。"""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return {}
    out = {}
    try:
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = kernel32.Process32First(snap, ctypes.byref(pe))
        while ok:
            name = pe.szExeFile.decode("gbk", "ignore").lower()
            out.setdefault(name, []).append(pe.th32ProcessID)
            ok = kernel32.Process32Next(snap, ctypes.byref(pe))
    finally:
        kernel32.CloseHandle(snap)
    return out


def process_running(proc_name):
    """软件在不在运行 —— 走进程快照，**与窗口无关**。

    为什么不能拿 find_process_window 代劳：窗口会被最小化、隐藏到托盘，
    或者干脆还没建出来。窗口判据失效 ≠ 软件没开。菜单里「有没有这一项」
    必须依赖这个更可靠的判据，否则最小化一下联动项就没了。
    """
    names = {proc_name.lower()} if isinstance(proc_name, str) \
        else {n.lower() for n in proc_name}
    procs = list_processes()
    return any(n in procs for n in names)


# ---------------------------------------------------------------- 方案 A：UWM_STOCK
def jump_tdx(code, verbose=True):
    """通过通达信自定义消息跳转。后台静默，不抢焦点。"""
    if not process_running("TdxW.exe"):
        return False, "通达信未运行"
    msg = user32.RegisterWindowMessageW(TDX_MSG_NAME)
    if msg == 0:
        return False, "注册 Stock 消息失败"
    param = tdx_encode(code)
    user32.PostMessageW(HWND_BROADCAST, msg, param, 0)
    hit = find_process_window("TdxW.exe")
    if hit:
        user32.PostMessageW(hit[0], msg, param, 0)
    if verbose:
        print("  通达信 UWM_STOCK=0x%04X  参数=%d" % (msg, param))
    return True, "已通过 UWM_STOCK 跳转到 %s（参数 %d）" % (code, param)


# ---------------------------------------------------------------- 方案 A2：同花顺 0x0490
def jump_ths_mem(code, verbose=True):
    """同花顺 0x0490 跨进程内存消息跳转。零键盘、零抢焦点、最小化也生效。

    返回 (True, detail)  成功
         (False, reason) 失败（调用方回落键盘精灵）
    """
    prefix = THS_MARKET_PREFIX.get(market_prefix(code))
    if prefix is None:
        return False, "北交所代码(%s)无 0x0490 编码" % code
    hit = find_process_window(TARGETS["ths"]["proc"])
    if not hit:
        return False, "同花顺未运行"
    hwnd, _, rect, title, _iconic = hit
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid.value)
    if not handle:
        handle = kernel32.OpenProcess(PROCESS_VM_WRITE, False, pid.value)
    if not handle:
        return False, "无法打开同花顺进程(PID=%d)" % pid.value
    mem = None
    try:
        payload = (prefix + code).encode("ascii")
        mem = kernel32.VirtualAllocEx(handle, None, len(payload) + 1,
                                      VIRTUAL_MEM, PAGE_READWRITE)
        if not mem:
            return False, "目标进程内存分配失败"
        if not kernel32.WriteProcessMemory(handle, mem, payload,
                                           len(payload), None):
            return False, "目标进程内存写入失败"
        res = wt.DWORD()
        ok = user32.SendMessageTimeoutW(hwnd, THS_MSG_STOCK, 0, mem,
                                        SMTO_ABORTIFHUNG, 2000, ctypes.byref(res))
        if not ok:
            err = ctypes.get_last_error()
            if err == 121:                       # ERROR_TIMEOUT
                return False, "同花顺消息超时（窗口假死？）"
            return False, "SendMessage 失败 GetLastError=%d" % err
        if verbose:
            print("  同花顺 0x%04X payload=%r  hwnd=0x%08X%s"
                  % (THS_MSG_STOCK, payload, hwnd,
                     "  ← 最小化中也生效" if _iconic else ""))
        return True, "已通过 0x0490 消息切换到 %s（无需抢焦点）" % code
    finally:
        if mem:
            kernel32.VirtualFreeEx(handle, mem, 0, MEM_RELEASE)
        kernel32.CloseHandle(handle)


# ---------------------------------------------------------------- 方案 B：键盘精灵
def force_foreground(hwnd, retries=3):
    """抢前台。SetForegroundWindow 受前台锁限制，用 AttachThreadInput 绕过。"""
    user32.ShowWindow(hwnd, SW_RESTORE if user32.IsIconic(hwnd) else SW_SHOW)
    for _ in range(retries):
        if user32.GetForegroundWindow() == hwnd:
            return True
        fg = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)
        cur_tid = kernel32.GetCurrentThreadId()
        attached = []
        try:
            for tid in (fg_tid, tgt_tid):
                if tid and tid != cur_tid and user32.AttachThreadInput(cur_tid, tid, True):
                    attached.append(tid)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            for tid in attached:
                user32.AttachThreadInput(cur_tid, tid, False)
        time.sleep(0.12)
        if user32.GetForegroundWindow() == hwnd:
            return True
    return False


_SENDINPUT_WARNED = set()
_WINERR_HINT = {
    5: "  (ERROR_ACCESS_DENIED：目标进程权限高于本进程，UIPI 拦截)",
    87: "  (ERROR_INVALID_PARAMETER：cbSize 或结构体布局不对)",
    0: "  (GetLastError 未设置，通常是被沙箱/安全软件静默丢弃)",
}


def _send_inputs(events):
    arr = (INPUT * len(events))()
    for i, (vk, scan, flags) in enumerate(events):
        arr[i].type = INPUT_KEYBOARD
        arr[i].u.ki.wVk = vk
        arr[i].u.ki.wScan = scan
        arr[i].u.ki.dwFlags = flags
        arr[i].u.ki.time = 0
        arr[i].u.ki.dwExtraInfo = None
    n = user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))
    if n != len(events):
        # 这个分支以前是缺的：SendInput 静默失败时上层完全无感，
        # 表现为「脚本说发键成功、目标窗口毫无反应」，极难定位。
        err = ctypes.get_last_error()
        if err not in _SENDINPUT_WARNED:
            _SENDINPUT_WARNED.add(err)
            print("[联动] ⚠ SendInput 只发出 %d/%d 个事件，GetLastError=%d%s"
                  % (n, len(events), err, _WINERR_HINT.get(err, "")))
    return n


def send_text(text, per_char_delay=0.03):
    for ch in text:
        _send_inputs([(0, ord(ch), KEYEVENTF_UNICODE),
                      (0, ord(ch), KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)])
        time.sleep(per_char_delay)


def send_vk(vk, count=1):
    for _ in range(count):
        _send_inputs([(vk, 0, 0), (vk, 0, KEYEVENTF_KEYUP)])
        time.sleep(0.05)


def jump_keyboard(code, proc_name, label, settle=1.5, per_char_delay=0.03,
                  act_wait=0.25, esc_wait=0.15, enter_wait=0.15,
                  verbose=True):
    """键盘精灵方式：抢前台 → ESC 清场 → 敲代码 → 回车。

    真机实测可用（2026-10）：
        东方财富 mainfree.exe  —— 600519→贵州茅台、600036→招商银行、
                                  601318→中国平安、000858→五粮液，4/4 命中
        同花顺   hexin.exe     —— 同一套节奏，键盘精灵窗口可直接收数字键
    对通达信「网页型版面」无效（焦点被内嵌 CEF 拿走，SendInput/PostMessage 全丢），
    所以通达信优先走方案 A 的 UWM_STOCK；同花顺优先走方案 A2 的 0x0490。

    2026-10-07 提速（对照 WzSLinker 逆向结论）：
      固定等待 0.5/0.35/0.3 → 0.25/0.15/0.15，出键耗时 ~1.6s → ~0.9s。
      每键间隔不动：东财 0.03/键会丢键（真机实测），0.07 才稳——
      WzSLinker 用 interval=0 一口气灌但没做丢键防护，不能照抄。
    """
    hit = find_process_window(proc_name)
    if not hit:
        return False, "%s 未运行" % label
    hwnd, _, rect, title, iconic = hit
    if verbose:
        print("  目标窗口 0x%08X  %s%s"
              % (hwnd, title[:50], "  ← 最小化中，先还原" if iconic else ""))
    if not force_foreground(hwnd):
        return False, "无法把 %s 提到前台" % label
    time.sleep(act_wait)                  # 抢到前台后等窗口真正激活
    send_vk(VK_ESCAPE)                    # 回列表页，清掉上次输入的残留
    time.sleep(esc_wait)
    send_text(code, per_char_delay=per_char_delay)
    time.sleep(enter_wait)
    send_vk(VK_RETURN)
    time.sleep(settle)
    return True, "已在 %s 中跳转到 %s" % (label, code)


# ---------------------------------------------------------------- 方案 C：网页
def jump_web(code, target="emweb", verbose=True):
    import webbrowser
    prefix = market_prefix(code)
    if target == "thsweb":
        url = "https://stockpage.10jqka.com.cn/%s/" % code
    elif prefix == "bj":
        url = "https://quote.eastmoney.com/bj/%s.html" % code
    else:
        url = "https://quote.eastmoney.com/%s%s.html" % (prefix, code)
    webbrowser.open(url)
    if verbose:
        print("  已打开网页: %s" % url)
    return True, url


# ---------------------------------------------------------------- 统一入口
def jump_to(code, target="tdx", verbose=True, **kw):
    cfg = TARGETS.get(target)
    if not cfg:
        return False, "未知目标: %s" % target
    method = cfg["method"]
    if method == "uwm_stock":
        ok, detail = jump_tdx(code, verbose=verbose)
        if ok:
            return ok, detail
        # 通达信消息失败 → 回落键盘精灵
        return jump_keyboard(code, cfg["proc"], cfg["label"], verbose=verbose)
    if method == "ths_mem":
        ok, detail = jump_ths_mem(code, verbose=verbose)
        if ok:
            return ok, detail
        # 0x0490 失败/北交所代码 → 回落键盘精灵
        if verbose:
            print("  [联动] 0x0490 不可用（%s），回落键盘精灵" % detail)
    if method in ("keyboard", "ths_mem"):
        params = dict(settle=cfg.get("settle", 1.5),
                      per_char_delay=cfg.get("delay", 0.03),
                      act_wait=cfg.get("act_wait", 0.25),
                      esc_wait=cfg.get("esc_wait", 0.15),
                      enter_wait=cfg.get("enter_wait", 0.15))
        params.update(kw)                 # 调用方显式传参优先
        return jump_keyboard(code, cfg["proc"], cfg["label"],
                             verbose=verbose, **params)
    return jump_web(code, target, verbose=verbose)


def available_targets():
    """返回当前机器上可用的联动目标"""
    out = []
    for key, cfg in TARGETS.items():
        if cfg["proc"] is None:
            out.append((key, cfg["label"], "always"))
        elif process_running(cfg["proc"]):
            out.append((key, cfg["label"], "running"))
    return out


if __name__ == "__main__":
    print("=" * 84)
    print("联动层自检")
    print("=" * 84)
    for key, label, state in available_targets():
        print("  %-8s %-16s %s" % (key, label, "可用" if state else "未运行"))
    print("\n  编码验证：")
    for c in ("600519", "000001", "300750", "688981", "920000"):
        print("    %s → %-9d (%s)" % (c, tdx_encode(c), market_prefix(c)))
