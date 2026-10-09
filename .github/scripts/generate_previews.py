"""Create web-friendly previews without changing portfolio originals."""
from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError

ROOT = Path(__file__).resolve().parents[2]
count = 0
original_bytes = 0
preview_bytes = 0
for group in ("kitchens", "wardrobes"):
    directory = ROOT / "images" / group
    output = directory / "previews"
    output.mkdir(parents=True, exist_ok=True)
    for source in sorted(directory.glob("*.jpg")):
        dest = output / (source.stem + ".webp")
        if dest.exists():
            continue
        try:
            with Image.open(source) as image:
                image = ImageOps.exif_transpose(image)
                image.thumbnail((800, 640), Image.Resampling.LANCZOS)
                image = image.convert("RGB")
                image.save(dest, "WEBP", quality=74, method=5)
            original_bytes += source.stat().st_size
            preview_bytes += dest.stat().st_size
            count += 1
        except (OSError, UnidentifiedImageError) as error:
            raise RuntimeError(f"Cannot create preview for {source}") from error
print(f"Created {count} previews; original bytes={original_bytes}; preview bytes={preview_bytes}")
