"""Build web copies from the approved 69-page client album without altering the design.

Usage: python scripts/build-alexey-portfolio.py /path/to/approved-album.pdf
Requires PyMuPDF and Pillow. Original PDF is copied byte for byte.
"""
import io
import json
import shutil
import sys
from pathlib import Path

import fitz
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'projects/alexey-apartment-52m'
MEDIA = DEST / 'media'
DOCS = DEST / 'documents'
MEDIA.mkdir(parents=True, exist_ok=True)
DOCS.mkdir(parents=True, exist_ok=True)
source = Path(sys.argv[1])
doc = fitz.open(source)
if len(doc) != 69 or 'Общие положения и условия реализации' not in doc[1].get_text():
    raise SystemExit('Unexpected album: use the approved 69-page version dated 23.11.2026')
shutil.copyfile(source, DOCS / 'alexey-complete-project.pdf')
manifest = {}
for page_no in range(5, 70):
    page = doc[page_no - 1]
    if 34 <= page_no <= 64:
        images = page.get_images(full=True)
        biggest = max(images, key=lambda x: x[2] * x[3])
        image = Image.open(io.BytesIO(doc.extract_image(biggest[0])['image'])).convert('RGB')
        image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
        image.save(MEDIA / f'page-{page_no}.webp', quality=88, method=6)
    else:
        pix = page.get_pixmap(matrix=fitz.Matrix(2400 / page.rect.width, 2400 / page.rect.width), alpha=False)
        image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
        image.save(MEDIA / f'page-{page_no}.webp', lossless=True, method=6)
    manifest[str(page_no)] = {'width': image.width, 'height': image.height}
    image.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
    image.save(MEDIA / f'page-{page_no}-thumb.webp', quality=85, method=6)
    manifest[str(page_no)]['thumbWidth'] = image.width
    manifest[str(page_no)]['thumbHeight'] = image.height

# Each excerpt starts with the unchanged general notes, retaining implementation limitations.
for name, start, end in [('plans-and-furniture',5,8),('kitchen-drawings',9,16),('electrical-layout',17,33),('interior-views',34,57),('decor-views',58,64),('material-collages',65,69)]:
    part = fitz.open()
    part.insert_pdf(doc, from_page=1, to_page=1)
    part.insert_pdf(doc, from_page=start-1, to_page=end-1)
    part.set_metadata({'title': f'Алексей — {name}', 'author': 'МАКСимум мебель'})
    part.save(DOCS / f'alexey-{name}.pdf', garbage=4, deflate=True)
(DEST / 'media-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'pages':len(doc), 'views':31, 'electrical_sheets':17, 'media_files':len(list(MEDIA.iterdir())), 'pdf_files':len(list(DOCS.iterdir()))}))
