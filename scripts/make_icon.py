"""生成桌面版应用图标 assets/icon.ico 与 assets/icon.png（Pillow 绘制，无外部素材）。"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 256.0
    # 圆角绿色底
    d.rounded_rectangle([8 * s, 8 * s, 248 * s, 248 * s], radius=56 * s,
                        fill=(26, 158, 92, 255))
    # 音箱喇叭（白色）
    d.rectangle([70 * s, 104 * s, 104 * s, 152 * s], fill=(255, 255, 255, 255))
    d.polygon([(104 * s, 104 * s), (150 * s, 66 * s), (150 * s, 190 * s),
               (104 * s, 152 * s)], fill=(255, 255, 255, 255))
    # 声波
    for r, w in ((34, 12), (62, 12)):
        d.arc([150 * s - r * s, 128 * s - r * s, 150 * s + r * s, 128 * s + r * s],
              start=-55, end=55, fill=(255, 255, 255, 255), width=int(w * s))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw_icon(256)
    big.save(OUT / "icon.png")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    big.save(OUT / "icon.ico", sizes=[(z, z) for z in sizes])
    try:
        big.save(OUT / "icon.icns")  # macOS .app 用
    except Exception as e:  # noqa: BLE001
        print("icns 生成跳过:", e)
    print("已生成", OUT / "icon.ico", "与", OUT / "icon.png")


if __name__ == "__main__":
    main()