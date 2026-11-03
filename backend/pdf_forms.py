"""Fill the supplied originals without recreating their artwork or signing them."""

import re
from pathlib import Path
import fitz
from fastapi import HTTPException

FORM_DIR = Path(__file__).parent / "forms"


def extract_layout(content, code):
    doc = fitz.open(stream=content, filetype="pdf")
    pages = []
    for pi, page in enumerate(doc):
        fields = []

        def add(rect, kind="text", label="", **extra):
            r = fitz.Rect(rect)
            if r.width < 4 or r.height < 4:
                return
            fields.append(
                {
                    "id": f"p{pi}f{len(fields)}",
                    "rect": [round(v, 2) for v in r],
                    "kind": kind,
                    "label": label or f"Feld {len(fields) + 1}",
                    **extra,
                }
            )

        words = page.get_text("words")
        # Printed underline runs and boxes, including boxes adjacent to punctuation.
        for block in page.get_text("rawdict")["blocks"]:
            for line in block.get("lines", []):
                chars = [ch for span in line["spans"] for ch in span["chars"]]
                text = "".join(c["c"] for c in chars)
                for match in re.finditer(r"_{2,}|□", text):
                    selected = chars[match.start() : match.end()]
                    r = fitz.Rect(selected[0]["bbox"])
                    for ch in selected[1:]:
                        r |= fitz.Rect(ch["bbox"])
                    if r.y0 > 760:
                        continue
                    nearby = " ".join(
                        w[4] for w in words if abs(w[1] - r.y0) < 8 and w[2] <= r.x0 + 2
                    )[-100:]
                    if match.group() == "□":
                        add([r.x0, r.y0 + 2, r.x0 + 9, r.y0 + 11], "checkbox", nearby)
                    else:
                        # Keep every signature / hand-mark line blank, names and functions remain fillable.
                        below = " ".join(
                            w[4]
                            for w in words
                            if r.y1 <= w[1] < r.y1 + 25 and r.x0 - 3 <= w[0] < r.x1
                        )
                        if any(
                            s in below + " " + nearby
                            for s in ("Unterschrift", "Handzeichen", "Kürzel")
                        ):
                            continue
                        add([r.x0, r.y0 - 1, r.x1, r.y1 - 1], label=nearby)
        if pi == 0 and code in (
            "Spez71-04",
            "Spez71-05",
            "Spez71-06",
            "Spez71-07",
            "Spez71-08",
            "Spez71-09",
        ):
            table = page.find_tables().tables[0]
            if code == "Spez71-04":
                xs = [
                    57,
                    168.74,
                    225.36,
                    282.05,
                    338.78,
                    395.47,
                    452.18,
                    501.94,
                    551.51,
                ]
                labels = [
                    "Produkt",
                    "Verpackung",
                    "Ware",
                    "MHD/VD",
                    "Schädlinge",
                    "Schimmel",
                    "Soll °C",
                    "Ist °C",
                ]
                for ri, row in enumerate(table.rows[2:]):
                    cell = next(c for c in row.cells if c)
                    y0, y1 = cell[1], cell[3]
                    for ci in range(8):
                        if 1 <= ci <= 5:
                            mid = (xs[ci] + xs[ci + 1]) / 2
                            add(
                                [xs[ci] + 9, y0 + 4, xs[ci] + 18, y0 + 13],
                                "checkbox",
                                f"Zeile {ri + 1}: {labels[ci]} ja",
                                row=ri,
                                column=ci,
                                choice="ja",
                            )
                            add(
                                [mid + 9, y0 + 4, mid + 18, y0 + 13],
                                "checkbox",
                                f"Zeile {ri + 1}: {labels[ci]} nein",
                                row=ri,
                                column=ci,
                                choice="nein",
                            )
                        else:
                            add(
                                [xs[ci] + 2, y0 + 1, xs[ci + 1] - 2, y1 - 1],
                                label=f"Zeile {ri + 1}: {labels[ci]}",
                                row=ri,
                                column=ci,
                            )
            elif code == "Spez71-05":
                add([60, 317, 547, 332], label="Lagerraumnummer / Bezeichnung")
                for ri, row in enumerate(table.rows[2:]):
                    cell = next(c for c in row.cells if c)
                    y0, y1 = cell[1], cell[3]
                    for x, label in [(250, "ja"), (285, "nein")]:
                        add(
                            [x, y0 + 6, x + 10, y0 + 16],
                            "checkbox",
                            f"Anforderung {ri + 1}: {label}",
                        )
                    add([313, y0 + 2, 492, y1 - 2], label=f"Maßnahmen {ri + 1}")
                    add([498, y0 + 2, 549, y1 - 2], label=f"Erledigt {ri + 1}")
            else:
                xs = (
                    [57, 126.26, 197.21, 268.49, 367.27, 459.46, 546.35]
                    if code in ("Spez71-06", "Spez71-07")
                    else [57, 109.55, 164.55, 246.75, 331.85, 395.6, 494.85, 547.07]
                    if code == "Spez71-08"
                    else [57, 109.55, 164.55, 246.75, 324.65, 395.6, 501.95, 547.07]
                )
                labels = table.extract()[0]
                for ri, row in enumerate(table.rows[1:]):
                    # Some date cells span two measurements. Use the physical measurement row for each date.
                    reference = next(c for c in row.cells[1:] if c)
                    y0, y1 = reference[1], reference[3]
                    for ci in range(len(xs) - 1):
                        if ("06" in code or "07" in code) and ci == 4:
                            continue
                        if ("08" in code or "09" in code) and ci == 6:
                            continue
                        add(
                            [xs[ci] + 2, y0 + 1, xs[ci + 1] - 2, y1 - 1],
                            label=f"Zeile {ri + 1}: {labels[ci]}",
                            row=ri,
                            column=ci,
                        )
        pages.append(
            {"width": page.rect.width, "height": page.rect.height, "fields": fields}
        )
    return {
        "pages": pages,
        "repeat_page": 0 if code[-2:] in ("04", "06", "07", "08", "09") else None,
    }


def validate_layout(layout, content):
    try:
        doc = fitz.open(stream=content, filetype="pdf")
        if doc.is_encrypted or not 1 <= len(doc) <= 50:
            raise ValueError()
        if len(layout["pages"]) != len(doc):
            raise ValueError()
        ids = set()
        for i, p in enumerate(layout["pages"]):
            if (
                abs(p["width"] - doc[i].rect.width) > 1
                or abs(p["height"] - doc[i].rect.height) > 1
            ):
                raise ValueError()
            if len(p["fields"]) > 1000:
                raise ValueError()
            for f in p["fields"]:
                r = fitz.Rect(f["rect"])
                if (
                    f["id"] in ids
                    or f["kind"] not in ("text", "checkbox")
                    or not doc[i].rect.contains(r)
                    or r.width < 4
                    or r.height < 4
                ):
                    raise ValueError()
                ids.add(f["id"])
        repeat = layout.get("repeat_page")
        if repeat is not None and (
            not isinstance(repeat, int) or not 0 <= repeat < len(doc)
        ):
            raise ValueError()
    except (ValueError, KeyError, TypeError, RuntimeError):
        raise HTTPException(422, "Ungültiges PDF oder Feldlayout")


def expanded_pages(layout, extra_pages):
    pages = [(i, p) for i, p in enumerate(layout["pages"])]
    repeat = layout.get("repeat_page")
    if extra_pages and repeat is None:
        raise HTTPException(422, "Diese Vorlage hat kein wiederholbares Tabellenblatt")
    for _ in range(extra_pages):
        pages.append((repeat, layout["pages"][repeat]))
    return pages


def field_key(page_index, field):
    return f"{page_index}:{field['id']}"


def render(content, layout, values, extra_pages=0):
    source = fitz.open(stream=content, filetype="pdf")
    out = fitz.open()
    pages = expanded_pages(layout, extra_pages)
    allowed = {
        field_key(i, f): f for i, (_, p) in enumerate(pages) for f in p["fields"]
    }
    if set(values) - set(allowed):
        raise HTTPException(422, "Unbekanntes Formularfeld")
    for key, value in values.items():
        f = allowed[key]
        if f["kind"] == "checkbox" and not isinstance(value, bool):
            raise HTTPException(422, "Häkchen müssen Wahrheitswerte sein")
        if f["kind"] == "text" and (not isinstance(value, str) or len(value) > 2000):
            raise HTTPException(422, "Ungültiger Feldtext")
    for i, (original, definition) in enumerate(pages):
        out.insert_pdf(source, from_page=original, to_page=original)
        p = out[-1]
        for f in definition["fields"]:
            value = values.get(field_key(i, f), "")
            if not value:
                continue
            rect = fitz.Rect(f["rect"])
            if f["kind"] == "checkbox":
                p.draw_line(rect.tl, rect.br, color=(0, 0, 0), width=1)
                p.draw_line(rect.tr, rect.bl, color=(0, 0, 0), width=1)
            else:
                fontfile = str(Path(__file__).parent / "fonts/DejaVuSans.ttf")
                font = fitz.Font(fontfile=fontfile)
                if any(not font.has_glyph(ord(c)) for c in value if c not in "\n\r\t"):
                    raise HTTPException(
                        422, "PDF-Schrift enthält ein verwendetes Zeichen nicht"
                    )
                p.insert_font(fontname="kitchen", fontfile=fontfile)
                fitted = False
                for size in (11, 10, 9, 8, 7):
                    if (
                        p.insert_textbox(
                            rect,
                            value,
                            fontname="kitchen",
                            fontsize=size,
                            lineheight=1,
                            color=(0, 0, 0),
                        )
                        >= 0
                    ):
                        fitted = True
                        break
                if not fitted:
                    raise HTTPException(
                        422, f"Text zu lang für {f['label']}; bitte kürzen"
                    )
        if extra_pages:
            matches = p.search_for("Seite")
            if matches:
                box = matches[-1]
                r = fitz.Rect(box.x0, box.y0 - 1, p.rect.width - 20, box.y1 + 2)
                p.add_redact_annot(r, fill=(1, 1, 1))
                p.apply_redactions()
                p.insert_text(
                    (box.x0, box.y1 - 3), f"Seite {i + 1} von {len(pages)}", fontsize=10
                )
    out.subset_fonts()
    return out.tobytes(garbage=4, deflate=True)
