import hashlib
import random
from io import BytesIO

from PIL import Image, ImageDraw


def _hsl(hue: float, saturation: float, lightness: float) -> tuple[int, int, int]:
    c = (1 - abs(2 * lightness - 1)) * saturation
    hp = (hue % 1) * 6
    x = c * (1 - abs(hp % 2 - 1))
    if hp < 1:
        r, g, b = c, x, 0
    elif hp < 2:
        r, g, b = x, c, 0
    elif hp < 3:
        r, g, b = 0, c, x
    elif hp < 4:
        r, g, b = 0, x, c
    elif hp < 5:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    m = lightness - c / 2
    return tuple(int((channel + m) * 255) for channel in (r, g, b))


def render(prompt: str, width: int, height: int, seed: str, reference: bytes | None) -> bytes:
    digest = hashlib.sha256(seed.encode()).digest()
    hue = digest[0] / 255
    image = Image.new("RGB", (width, height), _hsl(hue, 0.28, 0.14))
    if reference:
        ref = Image.open(BytesIO(reference)).convert("RGB").resize((width, height))
        image = Image.blend(ref, image, 0.62)
    draw = ImageDraw.Draw(image)
    rng = random.Random(digest)
    accent = _hsl((hue + 0.12) % 1, 0.62, 0.70)
    line = _hsl((hue + 0.48) % 1, 0.45, 0.55)
    for index in range(8):
        x0 = rng.randint(0, max(1, width - 20))
        y0 = rng.randint(0, max(1, height - 20))
        x1 = min(width - 1, x0 + rng.randint(width // 8, max(width // 8, width // 2)))
        y1 = min(height - 1, y0 + rng.randint(6, max(8, height // 6)))
        draw.rectangle((x0, y0, x1, y1), outline=accent if index % 2 == 0 else line, width=2)
    bar = max(44, height // 11)
    draw.rectangle((0, height - bar, width, height), fill=(12, 12, 11))
    draw.text((16, 14), "FIXTURE", fill=accent)
    excerpt = " ".join(prompt.split())
    if len(excerpt) > 90:
        excerpt = excerpt[:89] + "..."
    draw.text((16, height - bar + 14), excerpt, fill=(243, 241, 234))
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
