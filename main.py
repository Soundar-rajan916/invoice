#!/usr/bin/env python3
"""
Synthetic Indian GST invoice generator with exact ground truth.

For every invoice it writes:
  <out>/pdfs/INV_0001.pdf            the rendered invoice (3 different layouts)
  <out>/ground_truth/INV_0001.json   exactly what is printed on that PDF
  <out>/manifest.jsonl               all ground-truth records, one per line

Fields in the ground truth:
  invoice_number, invoice_date (ISO), supplier_name, supplier_gstin,
  line_items [description, quantity, rate, amount],
  plus taxable_value, cgst, sgst, igst, total (for validation tests).

Optional test cases (all labelled in the ground truth under "expected_flags"):
  --faulty-ratio   wrong total, bad GSTIN checksum, or wrong line amount (R2)
  --duplicates     re-rendered copies, some with a mangled invoice number (R3)
  --multipage-ratio  invoices with many line items that run over several pages (R1)

Install:  pip install faker reportlab
Usage:    python generate_invoices.py --count 100 --out out --seed 42
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import string
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from faker import Faker
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# --------------------------------------------------------------------------
# Static data
# --------------------------------------------------------------------------

STATES = {
    "33": "Tamil Nadu", "29": "Karnataka", "27": "Maharashtra", "07": "Delhi",
    "24": "Gujarat", "36": "Telangana", "32": "Kerala", "09": "Uttar Pradesh",
    "19": "West Bengal", "37": "Andhra Pradesh", "08": "Rajasthan", "03": "Punjab",
}

# (description, min rate, max rate) in rupees
CATALOG = [
    ("Basmati Rice 25 kg bag", 1800, 2600), ("Sunflower Oil 15 L tin", 1900, 2400),
    ("Toor Dal 30 kg bag", 3000, 4200), ("Sugar 50 kg bag", 2100, 2500),
    ("Wheat Flour (Atta) 10 kg", 380, 520), ("Tea Powder 1 kg pack", 350, 600),
    ("Detergent Powder 5 kg", 450, 700), ("Bath Soap 100 g (carton of 48)", 1100, 1700),
    ("Toothpaste 150 g (carton of 36)", 1800, 2600), ("A4 Copier Paper (500 sheets)", 220, 340),
    ("LED Bulb 9W (box of 10)", 650, 950), ("PVC Pipe 1 inch, 3 m", 180, 320),
    ("Cement 50 kg bag", 330, 440), ("Steel Padlock 50 mm", 90, 180),
    ("Plastic Bucket 20 L", 140, 260), ("Mineral Water 1 L (case of 12)", 130, 190),
    ("Glucose Biscuits 800 g (carton)", 700, 1100), ("Hand Sanitizer 500 ml", 120, 220),
    ("Notebook 200 pages (pack of 12)", 480, 720), ("Ballpoint Pen (box of 50)", 220, 380),
    ("Extension Board 4-socket", 260, 480), ("Copper Wire 1.5 sq mm, 90 m", 1600, 2300),
    ("Engine Oil 20W-40, 4 L", 1100, 1700), ("Garbage Bags 30 L (pack of 30)", 150, 260),
    ("Cotton Gloves (dozen)", 180, 300), ("Turmeric Powder 500 g (carton of 20)", 1500, 2200),
]
VARIANTS = ["Grade A", "Grade B", "Type 1", "Type 2", "Economy", "Premium", "Bulk", "Retail"]
GST_RATES = [5, 12, 18, 28]
DATE_FORMATS = ["%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d-%b-%y", "%Y-%m-%d"]

# Each layout changes fonts, labels, colours and structure so extraction cannot
# rely on one template.
LAYOUTS = {
    "classic": dict(
        font="Helvetica", bold="Helvetica-Bold", size=9, accent=colors.black,
        head_bg=colors.lightgrey, head_fg=colors.black, grid=True, zebra=False,
        title="TAX INVOICE", gstin_label="GSTIN:", no_label="Invoice No:", date_label="Date:",
    ),
    "banner": dict(
        font="Helvetica", bold="Helvetica-Bold", size=9, accent=colors.HexColor("#1F4E79"),
        head_bg=colors.HexColor("#1F4E79"), head_fg=colors.white, grid=False, zebra=True,
        title="INVOICE", gstin_label="GSTIN/UIN:", no_label="Invoice #", date_label="Invoice Date:",
    ),
    "compact": dict(
        font="Times-Roman", bold="Times-Bold", size=8, accent=colors.HexColor("#333333"),
        head_bg=colors.white, head_fg=colors.black, grid=False, zebra=False,
        title="Tax Invoice", gstin_label="GST No:", no_label="Bill No.", date_label="Dated:",
    ),
}

# --------------------------------------------------------------------------
# Money and GSTIN helpers
# --------------------------------------------------------------------------

GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def q2(x) -> Decimal:
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def num(x) -> float:
    return float(q2(x))


def inr(x) -> str:
    """Indian digit grouping: 1234567.5 -> 12,34,567.50"""
    whole, frac = f"{q2(x):.2f}".split(".")
    if len(whole) > 3:
        head, tail, parts = whole[:-3], whole[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return f"{whole}.{frac}"


def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        v = GST_CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return GST_CHARS[(36 - total % 36) % 36]


def is_valid_gstin(g: str) -> bool:
    return len(g) == 15 and all(c in GST_CHARS for c in g) and g[14] == gstin_check_char(g[:14])


def make_gstin(rng: random.Random, name: str, state_code: str) -> str:
    first_alpha = next((c for c in name.upper() if c.isalpha()), "A")
    pan = (
        "".join(rng.choices(string.ascii_uppercase, k=3))
        + rng.choice("CF")
        + first_alpha
        + "".join(rng.choices(string.digits, k=4))
        + rng.choice(string.ascii_uppercase)
    )
    body = f"{state_code}{pan}{rng.choice('123456789')}Z"
    return body + gstin_check_char(body)


def break_gstin(rng: random.Random, gstin: str) -> str:
    wrong = rng.choice([c for c in GST_CHARS if c != gstin[14]])
    return gstin[:14] + wrong


# --------------------------------------------------------------------------
# Invoice data
# --------------------------------------------------------------------------

def make_company(rng: random.Random, fake: Faker, state_code: str | None = None) -> dict:
    state_code = state_code or rng.choice(list(STATES))
    name = fake.company()
    addr = fake.address().replace("\n", ", ")
    return dict(
        name=name, address=f"{addr}", state_code=state_code, state=STATES[state_code],
        gstin=make_gstin(rng, name, state_code),
    )


def make_invoice_number(rng: random.Random, supplier_name: str, inv_date: date, seq: int) -> str:
    initials = "".join(w[0] for w in supplier_name.split() if w[0].isalpha())[:3].upper() or "INV"
    fy = f"{inv_date.year % 100:02d}-{(inv_date.year + 1) % 100:02d}"
    n = rng.randint(1, 9999)
    return rng.choice([
        f"INV-{inv_date.year}-{n:05d}",
        f"{initials}/{fy}/{n:04d}",
        f"GST{n:06d}",
        f"SI-{n}",
        f"{initials}{inv_date.year % 100:02d}{seq:03d}",
    ])


def make_line_items(rng: random.Random, n: int) -> list[dict]:
    items, used = [], set()
    for _ in range(n):
        base, lo, hi = rng.choice(CATALOG)
        desc = base
        for _try in range(5):
            if desc not in used:
                break
            desc = f"{base} - {rng.choice(VARIANTS)}"
        if desc in used:
            desc = f"{base} - Lot {len(used) + 1}"
        used.add(desc)
        qty = rng.randint(1, 200)
        rate = q2(Decimal(rng.randint(lo * 100, hi * 100)) / 100)
        items.append(dict(description=desc, quantity=qty, rate=rate, amount=q2(rate * qty)))
    return items


def make_invoice(rng, fake, buyer, seq, end_date, multipage, faulty) -> dict:
    inv_date = end_date - timedelta(days=rng.randint(0, 400))
    supplier = make_company(rng, fake)
    n_lines = rng.randint(30, 70) if multipage else rng.randint(1, 8)
    items = make_line_items(rng, n_lines)

    taxable = q2(sum(i["amount"] for i in items))
    rate = rng.choice(GST_RATES)
    cgst = sgst = igst = Decimal("0.00")
    if supplier["state_code"] == buyer["state_code"]:
        cgst = sgst = q2(taxable * rate / 200)
    else:
        igst = q2(taxable * rate / 100)
    total = q2(taxable + cgst + sgst + igst)

    flags = []
    printed_gstin = supplier["gstin"]
    if faulty == "wrong_total":
        total = q2(total + Decimal(rng.randint(10, 5000)))
        flags.append("total_mismatch")
    elif faulty == "bad_gstin":
        printed_gstin = break_gstin(rng, supplier["gstin"])
        flags.append("gstin_checksum")
    elif faulty == "wrong_line_amount":
        i = rng.randrange(len(items))
        items[i]["amount"] = q2(items[i]["amount"] + Decimal(rng.randint(5, 500)))
        flags.append("line_amount_mismatch")

    return dict(
        invoice_number=make_invoice_number(rng, supplier["name"], inv_date, seq),
        invoice_date=inv_date,
        supplier=dict(supplier, gstin=printed_gstin),
        buyer=buyer, line_items=items, gst_rate=rate,
        taxable_value=taxable, cgst=cgst, sgst=sgst, igst=igst, total=total,
        expected_flags=flags, duplicate_of=None,
    )


def mangle_number(rng: random.Random, n: str) -> str:
    return rng.choice([n.lower(), n.replace("-", " "), n.replace("/", "-"), " " + n])


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def numbered_canvas(font: str, pages_out: list):
    """Canvas that prints 'Page x of y' and reports the page count."""
    class NumberedCanvas(canvas.Canvas):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            n = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self.setFont(font, 8)
                self.drawString(15 * mm, 10 * mm, "This is a computer generated invoice.")
                self.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Page {self._pageNumber} of {n}")
                super().showPage()
            pages_out.append(n)
            super().save()
    return NumberedCanvas


def render_pdf(inv: dict, path: Path, layout: str, date_fmt: str) -> int:
    cfg = LAYOUTS[layout]
    size, accent = cfg["size"], cfg["accent"]
    base = ParagraphStyle("base", fontName=cfg["font"], fontSize=size, leading=size + 3)
    bold = ParagraphStyle("bold", parent=base, fontName=cfg["bold"])
    big = ParagraphStyle("big", parent=bold, fontSize=size + 6, leading=size + 9, textColor=accent)
    right = ParagraphStyle("right", parent=base, alignment=TA_RIGHT)
    right_bold = ParagraphStyle("rbold", parent=right, fontName=cfg["bold"])

    sup, buy = inv["supplier"], inv["buyer"]
    date_str = inv["invoice_date"].strftime(date_fmt)
    story = []

    supplier_block = [
        Paragraph(sup["name"], big),
        Paragraph(sup["address"], base),
        Paragraph(f"<b>{cfg['gstin_label']}</b> {sup['gstin']}", base),
    ]
    meta_block = [
        Paragraph(cfg["title"], ParagraphStyle("t", parent=right_bold, fontSize=size + 4, leading=size + 7)),
        Paragraph(f"<b>{cfg['no_label']}</b> {inv['invoice_number']}", right),
        Paragraph(f"<b>{cfg['date_label']}</b> {date_str}", right),
    ]

    if cfg["head_fg"] == colors.white:  # banner layout
        banner = Table([[Paragraph(f"<font color='white'>{sup['name']}</font>",
                                   ParagraphStyle("bn", parent=big, textColor=colors.white))]],
                       colWidths=[180 * mm])
        banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), accent),
                                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10)]))
        story += [banner, Spacer(1, 4 * mm)]
        header = Table([[supplier_block[1:], meta_block]], colWidths=[105 * mm, 75 * mm])
    elif layout == "compact":
        header = Table([[supplier_block, meta_block]], colWidths=[105 * mm, 75 * mm])
        header.setStyle(TableStyle([("BOX", (0, 0), (0, 0), 0.6, accent),
                                    ("LEFTPADDING", (0, 0), (0, 0), 6),
                                    ("TOPPADDING", (0, 0), (0, 0), 6),
                                    ("BOTTOMPADDING", (0, 0), (0, 0), 6)]))
    else:
        header = Table([[supplier_block, meta_block]], colWidths=[105 * mm, 75 * mm])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [header, Spacer(1, 5 * mm)]

    bill_to = Table([[[
        Paragraph("<b>Bill to</b>", base),
        Paragraph(buy["name"], bold),
        Paragraph(buy["address"], base),
        Paragraph(f"GSTIN: {buy['gstin']}", base),
        Paragraph(f"Place of supply: {buy['state']} ({buy['state_code']})", base),
    ]]], colWidths=[180 * mm])
    story += [bill_to, Spacer(1, 5 * mm)]

    rows = [["#", "Description", "Qty", "Rate (Rs.)", "Amount (Rs.)"]]
    for i, it in enumerate(inv["line_items"], 1):
        rows.append([str(i), Paragraph(it["description"], base), str(it["quantity"]),
                     inr(it["rate"]), inr(it["amount"])])
    table = Table(rows, colWidths=[10 * mm, 82 * mm, 20 * mm, 32 * mm, 36 * mm], repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, 0), cfg["bold"]), ("FONTSIZE", (0, 0), (-1, -1), size),
        ("FONTNAME", (0, 1), (-1, -1), cfg["font"]),
        ("BACKGROUND", (0, 0), (-1, 0), cfg["head_bg"]), ("TEXTCOLOR", (0, 0), (-1, 0), cfg["head_fg"]),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if cfg["grid"]:
        style.append(("GRID", (0, 0), (-1, -1), 0.5, colors.black))
    elif layout == "compact":
        style += [("LINEBELOW", (0, 0), (-1, 0), 0.8, accent), ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.grey)]
    if cfg["zebra"]:
        style.append(("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EEF3F8")]))
    table.setStyle(TableStyle(style))
    story += [table, Spacer(1, 4 * mm)]

    r = inv["gst_rate"]
    tot = [["Taxable value", inr(inv["taxable_value"])]]
    if inv["igst"] > 0:
        tot.append([f"IGST @ {r}%", inr(inv["igst"])])
    else:
        tot += [[f"CGST @ {r / 2:g}%", inr(inv["cgst"])], [f"SGST @ {r / 2:g}%", inr(inv["sgst"])]]
    tot.append(["Total (Rs.)", inr(inv["total"])])
    totals = Table(tot, colWidths=[50 * mm, 36 * mm], hAlign="RIGHT")
    totals.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), cfg["font"]), ("FONTNAME", (0, -1), (-1, -1), cfg["bold"]),
        ("FONTSIZE", (0, 0), (-1, -1), size), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, accent),
    ]))
    story.append(totals)

    pages: list[int] = []
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=18 * mm,
                            title=f"Invoice {inv['invoice_number']}", author=sup["name"])
    doc.build(story, canvasmaker=numbered_canvas(cfg["font"], pages))
    return pages[0]


# --------------------------------------------------------------------------
# Ground truth + main
# --------------------------------------------------------------------------

def ground_truth(inv: dict, file_id: str, layout: str, date_fmt: str, pages: int) -> dict:
    return dict(
        file=f"{file_id}.pdf", layout=layout, pages=pages,
        invoice_number=inv["invoice_number"],
        invoice_date=inv["invoice_date"].isoformat(),
        invoice_date_printed=inv["invoice_date"].strftime(date_fmt),
        supplier_name=inv["supplier"]["name"],
        supplier_gstin=inv["supplier"]["gstin"],
        line_items=[dict(description=i["description"], quantity=i["quantity"],
                         rate=num(i["rate"]), amount=num(i["amount"])) for i in inv["line_items"]],
        taxable_value=num(inv["taxable_value"]), cgst=num(inv["cgst"]), sgst=num(inv["sgst"]),
        igst=num(inv["igst"]), total=num(inv["total"]),
        gstin_valid=is_valid_gstin(inv["supplier"]["gstin"]),
        expected_flags=inv["expected_flags"], duplicate_of=inv["duplicate_of"],
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--count", type=int, default=50, help="number of base invoices")
    ap.add_argument("--out", default="out", help="output directory")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--end-date", default=date.today().isoformat(), help="latest invoice date (YYYY-MM-DD)")
    ap.add_argument("--multipage-ratio", type=float, default=0.15)
    ap.add_argument("--faulty-ratio", type=float, default=0.10)
    ap.add_argument("--duplicates", type=int, default=5, help="extra duplicate invoices")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    fake = Faker("en_IN")
    fake.seed_instance(args.seed)
    end_date = date.fromisoformat(args.end_date)

    out = Path(args.out)
    (out / "pdfs").mkdir(parents=True, exist_ok=True)
    (out / "ground_truth").mkdir(parents=True, exist_ok=True)

    buyer = make_company(rng, fake, state_code="33")
    buyer["name"] = "Sri Murugan Distributors"
    buyer["address"] = "42, Avinashi Road, Coimbatore, Tamil Nadu 641018"

    records, bases = [], []

    def emit(inv: dict, layout: str, file_id: str) -> None:
        date_fmt = rng.choice(DATE_FORMATS)
        pages = render_pdf(inv, out / "pdfs" / f"{file_id}.pdf", layout, date_fmt)
        gt = ground_truth(inv, file_id, layout, date_fmt, pages)
        (out / "ground_truth" / f"{file_id}.json").write_text(json.dumps(gt, indent=2, ensure_ascii=False))
        records.append(gt)

    for n in range(1, args.count + 1):
        faulty = rng.choice(["wrong_total", "bad_gstin", "wrong_line_amount"]) \
            if rng.random() < args.faulty_ratio else None
        inv = make_invoice(rng, fake, buyer, n, end_date, rng.random() < args.multipage_ratio, faulty)
        layout = rng.choice(list(LAYOUTS))
        file_id = f"INV_{n:04d}"
        emit(inv, layout, file_id)
        bases.append((file_id, inv, layout))

    for d in range(1, min(args.duplicates, len(bases)) + 1):
        src_id, src, src_layout = rng.choice(bases)
        dup = copy.deepcopy(src)
        dup["duplicate_of"] = src_id
        dup["expected_flags"] = src["expected_flags"] + ["duplicate"]
        if rng.random() < 0.5:
            dup["invoice_number"] = mangle_number(rng, dup["invoice_number"])
        layout = rng.choice([l for l in LAYOUTS if l != src_layout])
        emit(dup, layout, f"INV_{args.count + d:04d}")

    with open(out / "manifest.jsonl", "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    multi = sum(r["pages"] > 1 for r in records)
    flagged = sum(bool(r["expected_flags"]) for r in records)
    print(f"Wrote {len(records)} invoices to {out}/ "
          f"({multi} multi-page, {flagged} with expected flags)")


if __name__ == "__main__":
    main()