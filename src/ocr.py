# -*- coding: utf-8 -*-
"""OCR 引擎（P0 调优结论固化）

经过 P0-4 ~ P0-10 的系统性实测，最终工作点如下：

    ┌ 检测 det   limit_side_len=640 / limit_type=max   →  16 ms
    ├ 方向 cls   直接关闭（横排 UI 文本不需要）           →   0 ms
    ├ 识别 rec   batch_num=24                          → 222 ms
    └ 执行后端   DirectML（无 GPU 时自动回落 CPU）
                                                    合计 ≈ 240~330 ms

关键实测结论（反直觉，务必保留）：
  1. 瓶颈是 rec（占基线 76%），不是 det。只调 det 参数收益很小。
  2. CPU 上加大 rec_batch_num 会让总耗时【变慢】（868→1767ms）——
     rapidocr 会把一批内 crop padding 到同宽，CPU 上 padding 全是白算。
     现在代码里对 CPU 路径强制纠偏为 1（实测全窗 715ms → 322ms，2.2x）。
  3. GPU 恰好补上这个短板：DML + batch24 比 CPU 同配置快 3.8x。
     所以「GPU + 大 batch」必须成对使用，单独用任一个都没意义。
  4. use_angle_cls 对横排界面文本零收益，白花 50ms。
  5. ★ 纯 CPU 路径必须限制 intra_op_num_threads（见 _default_cpu_threads）：
     onnxruntime 默认用全部核心，单次识别把整机拉满 —— 实测 20 核机器上
     占用 14.4 核 / 单帧 225ms（卡）；限到 2 线程后占用 2.2 核 / 单帧 273ms
     （顺滑）。用户感知的是「卡不卡」，不是平均值。这是普通电脑能不能
     带得动的决定性一项。

依赖的 rapidocr 1.2.3 有两个 bug 需要绕开：
  * 传 det_*/rec_* 参数时若不一起给 *_model_path 会 KeyError
  * OrtInferSession 只支持 CPU/CUDA EP，DirectML 需要 monkey patch
"""
import ctypes
import os
import sys
from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------- 默认参数
DET_LIMIT_SIDE = 640      # det 输入长边上限
REC_BATCH = 24            # rec 批大小（必须配合 GPU 才有收益）
USE_ANGLE_CLS = False     # 横排 UI 文本无需方向分类
MIN_DET_HEIGHT = 160      # 输入最小高度（见 _ensure_min_height 的说明）
CPU_THREADS = 0           # CPU 推理线程数，0 = 按核数自动（见 _default_cpu_threads）


def _default_cpu_threads():
    """CPU 推理线程数：按核数给一个「不抢满整机」的值。

    ⚠ 这是普通电脑能不能带得动的关键，实测结论（probe/p41）：
    onnxruntime 的 CPU EP 默认 intra_op_num_threads = 全部逻辑核，单次推理
    就把整机 CPU 拉满。后果不是「平均占用高」，而是**每次识别那 200~700ms 里
    机器明显卡一下** —— 用户感知到的就是这个卡顿，而不是平均值。

    固定 det640 / rec_batch=1 / 消息区裁剪，只变线程数（20 逻辑核机器）：

        intra_op 线程     单帧 ms     占用核     帧CPU秒   峰值机器占用
        全部核心             225      14.37       3.24       72%   ← 卡
        4                    188       5.21       0.98       26%
        2                    273       2.23       0.61       11%   ← 顺滑
        1                    453       1.09       0.50        5%

    所以按核数分档：小机器 2、中等 3、大机器 4。墙钟会长一点，
    但只要短于消息到达间隔（正常群聊 1~3 秒一条），用户完全无感。
    """
    n = os.cpu_count() or 4
    if n <= 4:
        return 2
    if n <= 8:
        return 3
    return 4


def _ensure_min_height(image, min_h=MIN_DET_HEIGHT):
    """把过矮的输入垂直 pad 到 min_h。返回 (padded, dy)。

    ⚠ 这是硬约束，不是可调参数。PP-OCR 的 DBNet backbone 有 5 次下采样
    （1/32），输入高度 72px 时特征图只剩 2px 高，检测头直接失效返回 0 框。
    实测同一块内容：72px 高 → **0 个框**；pad 到 200px 高 → 正常识别。

    踩坑场景：东财把「代码 + 名称」画在客户区 y≈104px 处的一条 72px 高的
    带子里，直接裁出来送进 OCR 会静默返回空，让人误判成「跳转失败」。
    在入口统一兜住，调用方拿到的坐标会自动减回 dy，无需感知 padding。
    """
    if image is None or not hasattr(image, "shape"):
        return image, 0
    h = image.shape[0]
    if h >= min_h:
        return image, 0
    dy = (min_h - h) // 2
    rest = min_h - h - dy
    pad = ((dy, rest), (0, 0)) + (((0, 0),) if image.ndim == 3 else ())
    return np.pad(image, pad, mode="edge"), dy


@dataclass
class TextBox:
    """一个 OCR 文本框（坐标为图像内像素，左上原点）"""
    text: str
    x0: int
    y0: int
    x1: int
    y1: int
    conf: float

    @property
    def w(self):
        return self.x1 - self.x0

    @property
    def h(self):
        return self.y1 - self.y0

    @property
    def cx(self):
        return (self.x0 + self.x1) // 2

    @property
    def cy(self):
        return (self.y0 + self.y1) // 2

    def shifted(self, dx, dy):
        return TextBox(self.text, self.x0 + dx, self.y0 + dy,
                       self.x1 + dx, self.y1 + dy, self.conf)

    def __repr__(self):
        return "<TB %r (%d,%d)-(%d,%d) c=%.2f>" % (
            self.text, self.x0, self.y0, self.x1, self.y1, self.conf)


def _available_eps():
    try:
        import onnxruntime as ort
        return ort.get_available_providers()
    except Exception:
        return []


def _patch_ort_provider(use_gpu, cpu_threads=None):
    """把 rapidocr 写死的 EP 列表替换掉，使其支持 DirectML，并限制 CPU 线程数。

    rapidocr 1.2.3 的 utils.OrtInferSession 只认 CPU/CUDA 两种 provider，
    这里在构造 RapidOCR 之前替换掉 __init__。

    cpu_threads 只在**纯 CPU 路径**生效：GPU 路径下 CPU 只承担少量前后处理，
    限制它反而拖慢端到端；CPU 路径不限线程则会把整机拉满（见 _default_cpu_threads）。
    """
    import rapidocr_onnxruntime.utils as ru
    from onnxruntime import (GraphOptimizationLevel, InferenceSession,
                             SessionOptions)

    def __init__(self, config):
        opt = SessionOptions()
        opt.log_severity_level = 4
        opt.enable_cpu_mem_arena = False
        opt.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
        eps = []
        if use_gpu:
            eps.append(("DmlExecutionProvider", {"device_id": 0}))
        else:
            n = int(cpu_threads or 0) or _default_cpu_threads()
            opt.intra_op_num_threads = n      # 单次推理内部并行度（关键）
            opt.inter_op_num_threads = 1      # 层间并行，多了只会互相抢核
        eps.append(("CPUExecutionProvider",
                    {"arena_extend_strategy": "kSameAsRequested"}))
        self._verify_model(config["model_path"])
        self.session = InferenceSession(config["model_path"],
                                        sess_options=opt, providers=eps)

    ru.OrtInferSession.__init__ = __init__


class StockOCR:
    """股票识别专用 OCR 引擎"""

    def __init__(self, gpu="auto", det_limit=DET_LIMIT_SIDE,
                 rec_batch=REC_BATCH, use_cls=USE_ANGLE_CLS,
                 cpu_threads=CPU_THREADS, verbose=False):
        eps = _available_eps()
        has_dml = "DmlExecutionProvider" in eps
        if gpu == "auto":
            self.use_gpu = has_dml
        elif gpu in (True, "dml", "gpu"):
            self.use_gpu = has_dml
        else:
            self.use_gpu = False
        self.available_eps = eps

        # 纯 CPU 路径上「大 batch」是负优化，必须纠偏而不是等用户踩：
        # rapidocr 会把一批里的 crop padding 到同宽，CPU 上这些 padding 全是白算。
        # 实测全窗 det773：batch24 = 715ms，batch1 = 322ms（2.2x）。
        if not self.use_gpu and rec_batch > 1:
            if verbose:
                print("[OCR] 纯 CPU 路径：rec_batch %d → 1（CPU 上大批次反而更慢）"
                      % rec_batch)
            rec_batch = 1

        self.cpu_threads = int(cpu_threads or 0) or _default_cpu_threads()
        _patch_ort_provider(self.use_gpu, self.cpu_threads)

        from rapidocr_onnxruntime import RapidOCR
        kwargs = dict(
            rec_batch_num=rec_batch,
            det_limit_side_len=det_limit,
            det_limit_type="max",
            use_angle_cls=use_cls,
            # rapidocr 1.2.3 的 KeyError 规避：必须带上 model_path
            det_model_path=None,
            rec_model_path=None,
        )
        if use_cls:
            kwargs["cls_model_path"] = None
        self.engine = RapidOCR(**kwargs)
        self.rec_batch = rec_batch
        self.det_limit = det_limit
        self.use_cls = use_cls
        # (det_limit, rec_batch, use_cls) → RapidOCR 实例，避免反复重建模型
        self._engine_cache = {(det_limit, rec_batch, use_cls): self.engine}
        if verbose:
            print("[OCR] gpu=%s  det_limit=%d  rec_batch=%d  cls=%s  cpu_threads=%s"
                  % (self.use_gpu, det_limit, rec_batch, use_cls,
                     self.cpu_threads if not self.use_gpu else "-"))

    def recognize(self, image):
        """image: numpy RGB 数组或图片路径 → [TextBox]"""
        if isinstance(image, str):
            from PIL import Image
            image = np.asarray(Image.open(image).convert("RGB"))
        # 矮条输入保护：不 pad 的话 det 会静默返回 0 框（见 _ensure_min_height）
        image, dy = _ensure_min_height(image)
        out = self.engine(image)
        res = out[0] if isinstance(out, tuple) else out
        boxes = []
        for item in (res or []):
            try:
                poly, text, conf = item[0], item[1], item[2]
            except Exception:
                continue
            if not text:
                continue
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            boxes.append(TextBox(
                text=str(text),
                x0=int(min(xs)), y0=int(min(ys)) - dy,
                x1=int(max(xs)), y1=int(max(ys)) - dy,
                conf=float(conf),
            ))
        return boxes

    def reconfigure(self, det_limit=None, rec_batch=None, use_cls=None):
        """按当前窗口尺寸调整识别参数。

        为什么必须能动态调：det 的 limit_side_len 决定输入被缩放到多少。
        窗口越宽，若仍用窄窗口的参数（如 640），小字会被压得读不出来
        —— 实测在 2564px 宽的通达信窗口上，det640 会让识别几乎全废，
        换到 2000 才正常。而窗口窄时用小值可以显著提速。

        关键点：切换参数 = 重建 RapidOCR（要重新加载 3 个 onnx 模型）。
        多窗口并存时（如飞书 1325px 与通达信 2564px 交替），若每次都重建，
        光是加载模型就把帧率打死。所以这里做**引擎缓存**，同配置只建一次。
        """
        det_limit = det_limit if det_limit is not None else self.det_limit
        rec_batch = rec_batch if rec_batch is not None else self.rec_batch
        use_cls = use_cls if use_cls is not None else self.use_cls
        if not self.use_gpu and rec_batch > 1:      # CPU 路径统一纠偏为 1
            rec_batch = 1
        key = (det_limit, rec_batch, use_cls)
        if key == (self.det_limit, self.rec_batch, self.use_cls):
            return False
        cached = self._engine_cache.get(key)
        if cached is None:
            # 重建前重新挂一次 patch：_patch_ort_provider 改的是模块级全局，
            # 同进程里若又构造过另一个配置不同的实例，闭包会被覆盖成别的值。
            _patch_ort_provider(self.use_gpu, self.cpu_threads)
            from rapidocr_onnxruntime import RapidOCR
            kwargs = dict(
                rec_batch_num=rec_batch,
                det_limit_side_len=det_limit,
                det_limit_type="max",
                use_angle_cls=use_cls,
                det_model_path=None,
                rec_model_path=None,
            )
            if use_cls:
                kwargs["cls_model_path"] = None
            cached = RapidOCR(**kwargs)
            # 上限 4 套（覆盖常见窗口宽度档位），超了丢最早的一套
            if len(self._engine_cache) >= 4:
                self._engine_cache.pop(next(iter(self._engine_cache)))
            self._engine_cache[key] = cached
        self.engine = cached
        self.det_limit = det_limit
        self.rec_batch = rec_batch
        self.use_cls = use_cls
        return True

    def texts(self, image):
        return [b.text for b in self.recognize(image)]


if __name__ == "__main__":
    import time

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(root, "out", "captures", "Feishu_C.png")
    if not os.path.exists(src):
        print("缺样本，先跑 probe/p03_capture_test.py")
        sys.exit(0)

    from PIL import Image
    img = Image.open(src).convert("RGB")
    W, H = img.size
    crop = np.asarray(img.crop((230, 80, min(W, 1000), H)))

    eng = StockOCR(verbose=True)
    eng.recognize(crop)  # warmup
    ts = []
    for _ in range(5):
        t0 = time.perf_counter()
        boxes = eng.recognize(crop)
        ts.append((time.perf_counter() - t0) * 1000)
    print("\n热态耗时: min=%.0f  avg=%.0f  max=%.0f ms" % (min(ts), sum(ts) / len(ts), max(ts)))
    print("文本块 %d 个，前 12 条：" % len(boxes))
    for b in boxes[:12]:
        print("   (%4d,%4d)-(%4d,%4d) c=%.2f  %s" % (b.x0, b.y0, b.x1, b.y1, b.conf, b.text))
