"""Sample images for the person-photo tests — made here with Pillow, so no binary fixture. Each can
carry the metadata a phone or an editor writes (EXIF with GPS, XMP, PNG text chunks)."""
from __future__ import annotations

import io

from PIL import Image
from PIL.PngImagePlugin import PngInfo

GPS_IFD = 0x8825
XMP = (b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
       b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description '
       b'xmlns:exif="http://ns.adobe.com/exif/1.0/" exif:GPSLatitude="48,12.5N"/></rdf:RDF></x:xmpmeta>')


def _img(w: int, h: int, mode: str = "RGB"):
    im = Image.new(mode, (w, h), (180, 90, 40) if mode == "RGB" else (180, 90, 40, 255))
    for x in range(min(w, 8)):
        im.putpixel((x, 0), (10, 200, 30) if mode == "RGB" else (10, 200, 30, 255))
    return im


def exif_with_gps() -> bytes:
    ex = Image.Exif()
    ex[0x010F] = "PhoneMaker"                     # Make
    ex[0x0110] = "PhoneModel"                     # Model
    gps = ex.get_ifd(GPS_IFD)
    gps[1], gps[2] = "N", (48.0, 12.0, 30.0)      # latitude
    gps[3], gps[4] = "E", (16.0, 22.0, 10.0)      # longitude
    return ex.tobytes()


def png(w: int = 8, h: int = 8, *, text: bool = False) -> bytes:
    out = io.BytesIO()
    info = PngInfo()
    if text:
        info.add_text("Comment", "taken at home, 48.2N 16.4E")
        info.add_itxt("XML:com.adobe.xmp", XMP.decode())
    _img(w, h).save(out, format="PNG", pnginfo=info if text else None, **({"exif": exif_with_gps()} if text else {}))
    return out.getvalue()


def jpeg(w: int = 8, h: int = 8, *, gps: bool = False) -> bytes:
    out = io.BytesIO()
    kw = {"exif": exif_with_gps(), "comment": b"secret comment", "xmp": XMP} if gps else {}
    _img(w, h).save(out, format="JPEG", quality=90, **kw)
    return out.getvalue()


def webp(w: int = 8, h: int = 8, *, meta: bool = False) -> bytes:
    out = io.BytesIO()
    kw = {"exif": exif_with_gps(), "xmp": XMP} if meta else {}
    _img(w, h, "RGBA").save(out, format="WEBP", quality=90, **kw)
    return out.getvalue()


SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
GIF = b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"


def has_metadata(body: bytes) -> list:
    """Every piece of metadata Pillow and a byte scan can find in ``body`` — empty when clean."""
    found = []
    with Image.open(io.BytesIO(body)) as im:
        if im.getexif():
            found.append("exif")
        for k in ("exif", "xmp", "XML:com.adobe.xmp", "comment", "Comment", "icc_profile"):
            if im.info.get(k):
                found.append(f"info:{k}")
        if getattr(im, "text", None):
            found.append("png-text")
    for marker in (b"Exif\x00\x00", b"PhoneMaker", b"xpacket", b"adobe:ns:meta", b"secret comment", b"taken at home",
                   b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"EXIF", b"XMP "):
        if marker in body:
            found.append(marker.decode(errors="replace"))
    return found
