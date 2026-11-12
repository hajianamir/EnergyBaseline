"""Create assets/energybaseline.ico (multi-size) with Pillow: bar chart + baseline curve."""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "assets"


def draw(size: int) -> Image.Image:
    s = size
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.2), fill=(11, 79, 108, 255))
    bars = [0.35, 0.55, 0.45, 0.75, 0.6]
    bw = s * 0.12
    for i, h in enumerate(bars):
        x0 = s * 0.14 + i * s * 0.16
        d.rectangle((x0, s * 0.82 - h * s * 0.6, x0 + bw, s * 0.82), fill=(158, 197, 216, 255))
    pts = [(s * 0.12 + i * s * 0.16 + bw / 2, s * 0.82 - (0.42 + 0.05 * i) * s * 0.6) for i in range(5)]
    d.line(pts, fill=(255, 140, 66, 255), width=max(2, int(s * 0.05)))
    return im


def main():
    OUT.mkdir(exist_ok=True)
    big = draw(256)
    big.save(OUT / "energybaseline.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.save(OUT / "energybaseline.png")
    print("wrote", OUT / "energybaseline.ico")


if __name__ == "__main__":
    main()
