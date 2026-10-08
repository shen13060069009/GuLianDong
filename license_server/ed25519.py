# -*- coding: utf-8 -*-
"""Ed25519 纯 Python 实现（RFC 8032 参考算法，零第三方依赖）。

为什么不用 cryptography 库：
  * 服务端（宝塔机器）不保证有 pip / 编译环境，纯标准库到哪里都能跑；
  * 客户端少拖一个 ~4MB 的依赖；
  * 签名/验签在授权场景里调用频率极低（激活一次、心跳 7 天一次），
    纯 Python 的速度（百毫秒级）完全够用。

公开领域算法实现，仅做最小改动 + 中文注释。
"""
import hashlib
import os

# ---------------------------------------------------------------- 域参数
_p = 2**255 - 19
_l = 2**252 + 27742317777372353535851937790883648493
_d = -121665 * pow(121666, _p - 2, _p) % _p
_I = pow(2, (_p - 1) // 4, _p)


def _sha512(m):
    return hashlib.sha512(m).digest()


def _inv(x):
    return pow(x, _p - 2, _p)


def _xrecover(y):
    xx = (y * y - 1) * _inv(_d * y * y + 1)
    x = pow(xx, (_p + 3) // 8, _p)
    if (x * x - xx) % _p != 0:
        x = (x * _I) % _p
    if x % 2 != 0:
        x = _p - x
    return x


_By = 4 * _inv(5) % _p
_Bx = _xrecover(_By)
_B = (_Bx % _p, _By % _p, 1, _Bx * _By % _p)
_IDENT = (0, 1, 1, 0)


def _edwards_add(P, Q):
    """点加法（扩展坐标）"""
    x1, y1, z1, t1 = P
    x2, y2, z2, t2 = Q
    a = (y1 - x1) * (y2 - x2) % _p
    b = (y1 + x1) * (y2 + x2) % _p
    c = t1 * 2 * _d * t2 % _p
    dd = z1 * 2 * z2 % _p
    e = b - a
    f = dd - c
    g = dd + c
    h = b + a
    return (e * f % _p, g * h % _p, f * g % _p, e * h % _p)


def _scalarmult(P, e):
    """标量乘法（双加链）"""
    Q = (0, 1, 1, 0)
    while e > 0:
        if e & 1:
            Q = _edwards_add(Q, P)
        P = _edwards_add(P, P)
        e >>= 1
    return Q


def _decode_point(s):
    y = int.from_bytes(s, "little") & ((1 << 255) - 1)
    x = _xrecover(y)
    if x & 1 != (s[31] >> 7) & 1:
        x = _p - x
    P = (x, y, 1, x * y % _p)
    # 校验点在曲线上
    xx, yy = P[0], P[1]
    if (-xx * xx + yy * yy - 1 - _d * xx * xx * yy * yy) % _p != 0:
        raise ValueError("点不在曲线上")
    return P


def _point_compress(P):
    """压缩点。⚠ P 是扩展坐标 (X,Y,Z,T)，必须先除以 Z 归一化成仿射——
    标量乘过程中的点 Z≠1，直接用 X/Y 会得到垃圾编码（实测踩坑）。"""
    zinv = _inv(P[2])
    x = P[0] * zinv % _p
    y = P[1] * zinv % _p
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def publickey(seed):
    """32 字节种子 → 32 字节公钥"""
    h = _sha512(seed)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    A = _scalarmult(_B, a)
    return _point_compress(A)


def sign(msg, seed, pk=None):
    """Ed25519 签名，返回 64 字节签名"""
    if pk is None:
        pk = publickey(seed)
    h = _sha512(seed)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    r = int.from_bytes(_sha512(h[32:] + msg), "little") % _l
    R = _scalarmult(_B, r)
    Rs = _point_compress(R)
    hram = int.from_bytes(_sha512(Rs + pk + msg), "little") % _l
    s = (r + hram * a) % _l
    return Rs + int.to_bytes(s, 32, "little")


def verify(msg, sig, pk):
    """验签。True/False（任何格式问题都返回 False，不抛异常）"""
    try:
        if len(sig) != 64 or len(pk) != 32:
            return False
        R = _decode_point(sig[:32])
        A = _decode_point(pk)
        s = int.from_bytes(sig[32:], "little")
        if s >= _l:
            return False
        h = int.from_bytes(_sha512(sig[:32] + pk + msg), "little") % _l
        sB = _scalarmult(_B, s)
        hA = _scalarmult(A, h)
        RhA = _edwards_add(R, hA)
        return _point_compress(sB) == _point_compress(RhA)
    except Exception:
        return False


# ---------------------------------------------------------------- 密钥生成工具
def genkeypair():
    """生成 (seed私钥, 公钥)，均为 32 字节。只在本机生成一次用。"""
    seed = os.urandom(32)
    return seed, publickey(seed)


if __name__ == "__main__":
    import time
    t0 = time.time()
    seed, pk = genkeypair()
    print("priv(保存到服务端):", seed.hex())
    print("pub (内置到客户端):", pk.hex())
    m = b"hello stocklens"
    sig = sign(m, seed, pk)
    print("签名验证:", verify(m, sig, pk), "| 篡改后:", verify(m + b"x", sig, pk))
    print("耗时 %.2fs" % (time.time() - t0))
