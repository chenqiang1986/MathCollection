"""Crop and persist figure regions from uploaded source images or PDFs."""

import io
import tempfile
import uuid
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from common.storage import figures_dir, raw_upload_path

FIGURE_PADDING = 0.015  # 1.5% margin on each side of the model's bbox
PDF_RENDER_SCALE = 3.0  # 3× the PDF's 72-DPI base (≈216 DPI) for crop quality

# 90° clockwise = PIL ROTATE_270 (PIL constants are counter-clockwise).
_CW_ROTATION = {
    0: None,
    90: Image.ROTATE_270,
    180: Image.ROTATE_180,
    270: Image.ROTATE_90,
}


def _load_page_image(src_path: Path, page: int) -> Image.Image:
    """Return the source as an RGB PIL image. For PDFs, render the given
    1-indexed page; for raster images, ignore `page` and open directly."""
    if src_path.suffix.lower() == ".pdf":
        pdf = pdfium.PdfDocument(str(src_path))
        try:
            if page < 1 or page > len(pdf):
                raise ValueError(
                    f"figure_page {page} out of range for {len(pdf)}-page PDF"
                )
            pdf_page = pdf[page - 1]
            try:
                bitmap = pdf_page.render(scale=PDF_RENDER_SCALE)
                return bitmap.to_pil().convert("RGB")
            finally:
                pdf_page.close()
        finally:
            pdf.close()
    with Image.open(src_path) as im:
        return im.convert("RGB")


def source_page_count(source_image: str) -> int:
    """Number of pages in the source. 1 for raster images; the PDF page
    count for PDFs."""
    src_path = raw_upload_path(source_image)
    if not src_path.exists():
        raise FileNotFoundError(f"source image not found: {src_path}")
    if src_path.suffix.lower() != ".pdf":
        return 1
    pdf = pdfium.PdfDocument(str(src_path))
    try:
        return len(pdf)
    finally:
        pdf.close()


def render_source_page_to_png_bytes(source_image: str, page: int = 1) -> bytes:
    """Render the given 1-indexed source page to PNG bytes. For PDFs the
    page is rasterized at the same scale used for cropping; for raster
    sources `page` is ignored and the image is re-encoded as PNG."""
    src_path = raw_upload_path(source_image)
    if not src_path.exists():
        raise FileNotFoundError(f"source image not found: {src_path}")
    im = _load_page_image(src_path, int(page) if page else 1)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def render_source_page_to_temp_png(source_image: str, page: int = 1) -> Path:
    """Render the given 1-indexed source page to a temporary PNG and
    return its path. For PDFs the page is rasterized at the same scale
    used for cropping; for raster sources `page` is ignored and the
    image is re-encoded as PNG. Caller is responsible for unlinking
    the returned file when done."""
    src_path = raw_upload_path(source_image)
    if not src_path.exists():
        raise FileNotFoundError(f"source image not found: {src_path}")
    im = _load_page_image(src_path, int(page) if page else 1)
    fd, tmp_name = tempfile.mkstemp(prefix="mc_src_page_", suffix=".png")
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "wb") as f:
            im.save(f, "PNG")
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


def save_figure(
    source_image: str,
    bbox: list[float],
    rotation: int = 0,
    page: int = 1,
) -> str:
    """Crop a normalized [x0,y0,x1,y1] region from
    data/<user>/raw/<source_image>, optionally rotate clockwise by
    `rotation` (one of 0/90/180/270), and save as a PNG under
    data/<user>/figures/. Returns the saved filename.

    `bbox` values are in [0,1] in the source's frame (per-page for PDFs);
    a small padding is added before clipping. `page` is 1-indexed and is
    only meaningful when the source is a PDF.
    """
    if len(bbox) != 4:
        raise ValueError(f"figure_bbox must have 4 values, got {len(bbox)}")
    rotation = int(rotation)
    if rotation not in _CW_ROTATION:
        raise ValueError(
            f"figure_rotation must be 0, 90, 180, or 270 (got {rotation})"
        )
    x0, y0, x1, y1 = (float(v) for v in bbox)
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    x0 = max(0.0, x0 - FIGURE_PADDING)
    y0 = max(0.0, y0 - FIGURE_PADDING)
    x1 = min(1.0, x1 + FIGURE_PADDING)
    y1 = min(1.0, y1 + FIGURE_PADDING)

    src_path = raw_upload_path(source_image)
    if not src_path.exists():
        raise FileNotFoundError(f"source image not found: {src_path}")

    fdir = figures_dir()
    fdir.mkdir(parents=True, exist_ok=True)
    im = _load_page_image(src_path, int(page) if page else 1)
    w, h = im.size
    px_box = (
        int(round(x0 * w)),
        int(round(y0 * h)),
        int(round(x1 * w)),
        int(round(y1 * h)),
    )
    if px_box[2] - px_box[0] < 1 or px_box[3] - px_box[1] < 1:
        raise ValueError(f"figure_bbox crops to empty region: {bbox}")
    cropped = im.crop(px_box)

    transpose_op = _CW_ROTATION[rotation]
    if transpose_op is not None:
        cropped = cropped.transpose(transpose_op)

    filename = f"{uuid.uuid4()}.png"
    cropped.save(fdir / filename, "PNG")
    return filename
