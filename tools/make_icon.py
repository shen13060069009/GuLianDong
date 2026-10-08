# -*- coding: utf-8 -*-
"""生成股联动 GuLianDong 的应用图标（icon.png / icon.ico）

设计：蓝色圆角底 + 白色放大镜（“股镜”= 股票 + 镜）+ 镜内一根向上的黄色折线。
保持极简，因为 16x16 下细节全糊，只能靠轮廓和色块辨识。
"""
import os

from PIL import Image, ImageDraw

S = 256
BLUE = (37, 99, 235, 255)
GOLD = (255, 214, 64, 255)
WHITE = (255, 255, 255, 255)


def make(size=S):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角底（iOS/Windows 现代图标比例）
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22),
                        fill=BLUE)

    k = size / 256.0
    # 放大镜：镜圈 + 手柄
    cx, cy, r, w = 112 * k, 110 * k, 56 * k, 17 * k
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=WHITE, width=int(w))
    d.line([cx + r * 0.72, cy + r * 0.72, 202 * k, 200 * k],
           fill=WHITE, width=int(20 * k))

    # 镜内上升折线（K 线意象）
    pts = [(84 * k, 128 * k), (101 * k, 106 * k), (116 * k, 118 * k), (139 * k, 86 * k)]
    d.line(pts, fill=GOLD, width=int(13 * k), joint="curve")
    # 折线端点的小方块，像 K 线实体
    for (px, py) in (pts[0], pts[-1]):
        d.rounded_rectangle([px - 6 * k, py - 6 * k, px + 6 * k, py + 6 * k],
                            radius=3 * k, fill=GOLD)
    return img


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(root, "build")
    os.makedirs(out, exist_ok=True)

    img = make()
    png = os.path.join(out, "icon.png")
    img.save(png)

    ico = os.path.join(out, "icon.ico")
    img.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                         (64, 64), (128, 128), (256, 256)])
    print("PNG -> %s" % png)
    print("ICO -> %s" % ico)
    # 缩到 16px 看一眼轮廓是否还立得住
    img.resize((64, 64), Image.LANCZOS).save(os.path.join(out, "icon_64.png"))
    print("预览 -> %s" % os.path.join(out, "icon_64.png"))


if __name__ == "__main__":
    main()
