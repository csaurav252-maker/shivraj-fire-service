"""
GST Tax Invoice helper for Shivraj Fire Service & Industrial Gases.

- Tax calculation (CGST + SGST for same-state, IGST for other states)
- Amount in words (Indian numbering: Lakh / Crore)
- PDF generation: "TAX INVOICE - ORIGINAL FOR RECIPIENT" (A4)

Needs only: reportlab
"""
import datetime
import io
import re
from decimal import Decimal, ROUND_HALF_UP
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# ---------------------------------------------------------------------------
# 1) SELLER DETAILS  -> PUT YOUR REAL GSTIN HERE
# ---------------------------------------------------------------------------
SELLER = {
    "name": "SHIVRAJ FIRE SERVICE & INDUSTRIAL GASES",
    "address": "Laxmi Nagar, Near Old BSNL Office, Malkapur, Dist. Buldhana, Maharashtra",
    "proprietors": "Swapnil Deshmukh & Ganesh Deshmukh",
    "phone": "9011763817",
    "gstin": "",            # <-- e.g. "27ABCDE1234F1Z5"  (15 characters)
    "state": "Maharashtra",
    "state_code": "27",
}

# ---------------------------------------------------------------------------
# 2) DEFAULT HSN / SAC CODES AND GST RATES
#    These are only DEFAULTS. Please confirm every rate with your CA / the
#    current GST rate notification before issuing real invoices.
#    A product row in Supabase can override them with optional columns
#    "hsn_code" and "gst_rate" (see README text given with this file).
# ---------------------------------------------------------------------------
GST_SERVICE_REFILL = ("998719", 18)    # refilling / maintenance service
GST_SERVICE_RENTAL = ("997319", 18)    # cylinder rental
GST_GOODS_DEFAULTS = [
    # (keyword searched in gas_type + category + name, hsn, rate)
    ("medical oxygen", "280440", 18),   # VERIFY medical oxygen rate with your CA
    ("oxygen",         "280440", 18),
    ("nitrogen",       "280430", 18),
    ("hydrogen",       "280410", 18),
    ("argon",          "280421", 18),
    ("co2",            "281121", 18),
    ("acetylene",      "290121", 18),
    ("accessories",    "842490", 18),
]
GST_FIRE_EQUIPMENT_DEFAULT = ("8424", 18)   # fire extinguishers

PLACEHOLDER_GSTIN_TEXT = "______________"

STATES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan",
    "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura",
    "17": "Meghalaya", "18": "Assam", "19": "West Bengal", "20": "Jharkhand",
    "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "26": "Dadra & Nagar Haveli and Daman & Diu", "27": "Maharashtra", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman & Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh",
}

GSTIN_REGEX = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def is_valid_gstin(gstin: str) -> bool:
    return bool(GSTIN_REGEX.match((gstin or "").strip().upper()))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _d(x) -> Decimal:
    return Decimal(str(x))


def _q2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt_inr(x) -> str:
    """Indian digit grouping: 1,23,456.78"""
    x = _q2(_d(x))
    sign = "-" if x < 0 else ""
    whole, frac = f"{abs(x):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    return f"{sign}{whole}.{frac}"


def fmt_qty(q) -> str:
    q = _d(q)
    return str(int(q)) if q == q.to_integral_value() else f"{q:.2f}".rstrip("0").rstrip(".")


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
         "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
         "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10] + ((" " + _ONES[n % 10]) if n % 10 else "")


def _three(n: int) -> str:
    out = ""
    if n >= 100:
        out = _ONES[n // 100] + " Hundred"
        n %= 100
        if n:
            out += " "
    return out + _two(n)


def number_to_words_indian(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    for div, label in ((10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand")):
        if n >= div:
            parts.append(_two(n // div) + " " + label)
            n %= div
    if n:
        parts.append(_three(n))
    return " ".join(parts)


def amount_in_words(amount) -> str:
    amount = _q2(_d(amount))
    rupees = int(amount)
    paise = int((amount - rupees) * 100)
    words = "Rupees " + number_to_words_indian(rupees)
    if paise:
        words += " and " + number_to_words_indian(paise) + " Paise"
    return words + " Only"


# ---------------------------------------------------------------------------
# HSN / rate lookup
# ---------------------------------------------------------------------------
def get_tax_defaults(category="", gas_type="", service_type="", name=""):
    service = (service_type or "").lower()
    if "refill" in service:
        return GST_SERVICE_REFILL
    if "rental" in service or "deposit" in service:
        return GST_SERVICE_RENTAL
    hay = f"{gas_type} {category} {name}".lower()
    for keyword, hsn, rate in GST_GOODS_DEFAULTS:
        if keyword in hay:
            return hsn, rate
    return GST_FIRE_EQUIPMENT_DEFAULT


def line_tax_info(line: dict):
    """HSN + rate for a cart / invoice line (saved values win over defaults)."""
    d_hsn, d_rate = get_tax_defaults(
        line.get("category", ""), line.get("gas_type", ""),
        line.get("service_type", ""), line.get("name", ""),
    )
    hsn = str(line.get("hsn") or d_hsn)
    rate = line.get("gst_rate")
    rate = d_rate if rate in (None, "") else rate
    return hsn, _d(rate)


# ---------------------------------------------------------------------------
# Invoice calculation
# ---------------------------------------------------------------------------
def compute_invoice(lines, price_mode="exclusive", intra_state=True):
    """
    lines      : list of dicts with price, qty (+ optional hsn / gst_rate / name ...)
    price_mode : "exclusive" -> entered price is WITHOUT GST (GST added on top)
                 "inclusive" -> entered price already INCLUDES GST
    intra_state: True -> CGST + SGST (half each), False -> IGST
    All money values are returned as Decimal (2 decimals); grand_total is a whole rupee.
    """
    zero = Decimal("0.00")
    out_lines = []
    t_taxable = t_cgst = t_sgst = t_igst = zero

    for ln in lines:
        qty = _d(ln["qty"])
        price = _d(ln["price"])
        hsn, rate = line_tax_info(ln)
        amount = _q2(qty * price)

        if price_mode == "inclusive":
            taxable = _q2(amount * 100 / (100 + rate))
            tax = amount - taxable
        else:
            taxable = amount
            tax = _q2(taxable * rate / 100)

        if intra_state:
            cgst = _q2(tax / 2)
            sgst = tax - cgst
            igst = zero
        else:
            cgst = sgst = zero
            igst = tax

        unit_rate = _q2(taxable / qty) if qty else zero
        out_lines.append({
            "name": ln.get("name", ""),
            "capacity": ln.get("capacity", ""),
            "service_type": ln.get("service_type", ""),
            "hsn": hsn, "gst_rate": rate, "qty": qty,
            "unit_rate": unit_rate, "taxable": taxable,
            "cgst": cgst, "sgst": sgst, "igst": igst,
            "line_total": taxable + cgst + sgst + igst,
        })
        t_taxable += taxable
        t_cgst += cgst
        t_sgst += sgst
        t_igst += igst

    raw_total = t_taxable + t_cgst + t_sgst + t_igst
    grand_total = raw_total.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    # tax summary grouped by HSN + rate
    summary = {}
    for l in out_lines:
        key = (l["hsn"], l["gst_rate"])
        s = summary.setdefault(key, {"hsn": l["hsn"], "gst_rate": l["gst_rate"],
                                     "taxable": zero, "cgst": zero, "sgst": zero, "igst": zero})
        for k in ("taxable", "cgst", "sgst", "igst"):
            s[k] += l[k]

    return {
        "lines": out_lines,
        "intra_state": intra_state,
        "taxable": t_taxable, "cgst": t_cgst, "sgst": t_sgst, "igst": t_igst,
        "total_tax": t_cgst + t_sgst + t_igst,
        "raw_total": raw_total,
        "round_off": grand_total - raw_total,
        "grand_total": grand_total,
        "summary": list(summary.values()),
    }


# ---------------------------------------------------------------------------
# Invoice number / dates
# ---------------------------------------------------------------------------
def _parse_time(time_str):
    for fmt in ("%d-%m-%Y %H:%M", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(str(time_str), fmt)
        except ValueError:
            continue
    return datetime.datetime.now()


def invoice_number(log_id, time_str) -> str:
    """e.g. SFS/26-27/0012  (financial year April-March; 14 chars, GST-compliant format)"""
    dt = _parse_time(time_str)
    start = dt.year if dt.month >= 4 else dt.year - 1
    return f"SFS/{str(start)[-2:]}-{str(start + 1)[-2:]}/{int(log_id):04d}"


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def _styles():
    base = dict(fontName="Helvetica", fontSize=8, leading=10)
    return {
        "n": ParagraphStyle("n", **base),
        "b": ParagraphStyle("b", **{**base, "fontName": "Helvetica-Bold"}),
        "c": ParagraphStyle("c", **{**base, "alignment": 1}),
        "cb": ParagraphStyle("cb", **{**base, "alignment": 1, "fontName": "Helvetica-Bold"}),
        "r": ParagraphStyle("r", **{**base, "alignment": 2}),
        "rb": ParagraphStyle("rb", **{**base, "alignment": 2, "fontName": "Helvetica-Bold"}),
        "h": ParagraphStyle("h", **{**base, "alignment": 1, "fontName": "Helvetica-Bold", "fontSize": 7.5, "leading": 9}),
        "title": ParagraphStyle("title", **{**base, "alignment": 1, "fontName": "Helvetica-Bold", "fontSize": 13, "leading": 16}),
        "shop": ParagraphStyle("shop", **{**base, "alignment": 1, "fontName": "Helvetica-Bold", "fontSize": 15, "leading": 18}),
        "tag": ParagraphStyle("tag", **{**base, "alignment": 2, "fontName": "Helvetica-Bold", "fontSize": 8.5}),
        "small": ParagraphStyle("small", **{**base, "fontSize": 7, "leading": 9}),
    }


def _page_border(canvas, doc):
    canvas.saveState()
    canvas.setLineWidth(0.8)
    w, h = A4
    canvas.rect(7 * mm, 7 * mm, w - 14 * mm, h - 14 * mm)
    canvas.setFont("Helvetica", 7)
    canvas.drawRightString(w - 9 * mm, 9 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_invoice_pdf(log: dict, seller: dict = None, copy_label: str = "ORIGINAL FOR RECIPIENT") -> bytes:
    """
    log keys used: id, time, buyer, phone, location, items (list of lines),
                   meta (dict: gstin, state_code, price_mode), paid_amount,
                   balance_due, payment
    Returns the PDF as bytes.
    """
    seller = seller or SELLER
    st = _styles()
    meta = log.get("meta") or {}

    state_code = str(meta.get("state_code") or seller["state_code"])
    price_mode = meta.get("price_mode") or "inclusive"   # old bills (before GST) had no tax added on top
    intra = state_code == str(seller["state_code"])
    place_of_supply = f"{STATES.get(state_code, '')} ({state_code})"
    buyer_gstin = (meta.get("gstin") or "").strip().upper()

    inv = compute_invoice(log.get("items") or [], price_mode=price_mode, intra_state=intra)
    inv_no = invoice_number(log["id"], log["time"])
    inv_dt = _parse_time(log["time"])
    inv_date = inv_dt.strftime("%d-%m-%Y")
    next_service = (inv_dt + datetime.timedelta(days=365)).strftime("%d-%m-%Y")
    pay_mode = str(log.get("payment", "")).split("\n")[0].split(" (Paid")[0].strip() or "-"

    P = lambda text, style="n": Paragraph(escape(str(text)).replace("\n", "<br/>"), st[style])

    story = []
    page_w = A4[0] - 20 * mm   # 190 mm usable

    # --- Title row -------------------------------------------------------
    top = Table([[P("", "n"), P("TAX INVOICE", "title"), P(copy_label, "tag")]],
                colWidths=[55 * mm, 80 * mm, 55 * mm])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                             ("BOX", (0, 0), (-1, -1), 0.8, colors.black),
                             ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F2F2F2"))]))
    story.append(top)

    # --- Seller ----------------------------------------------------------
    gstin_txt = seller["gstin"] or PLACEHOLDER_GSTIN_TEXT
    seller_tbl = Table([
        [P(seller["name"], "shop")],
        [P(seller["address"], "c")],
        [P(f"Prop: {seller['proprietors']}   |   Phone: {seller['phone']}", "c")],
        [P(f"GSTIN: {gstin_txt}   |   State: {seller['state']}, Code: {seller['state_code']}", "cb")],
    ], colWidths=[page_w])
    seller_tbl.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, colors.black),
                                    ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story.append(seller_tbl)

    # --- Invoice info + Bill To -----------------------------------------
    left_info = (f"<b>Invoice No:</b> {escape(inv_no)}<br/>"
                 f"<b>Invoice Date:</b> {inv_date}<br/>"
                 f"<b>Payment Mode:</b> {escape(pay_mode)}")
    right_info = (f"<b>Place of Supply:</b> {escape(place_of_supply)}<br/>"
                  f"<b>Reverse Charge:</b> No<br/>"
                  f"<b>Supply Type:</b> {'Intra-State (CGST + SGST)' if intra else 'Inter-State (IGST)'}")
    bill_to = (f"<b>Bill To (Recipient):</b><br/><b>{escape(str(log.get('buyer', '')))}</b><br/>"
               f"{escape(str(log.get('location', '')))}<br/>"
               f"Phone: {escape(str(log.get('phone') or '-'))}")
    bill_to_r = (f"<b>Recipient GSTIN:</b> {escape(buyer_gstin) if buyer_gstin else 'Unregistered / URP'}<br/>"
                 f"<b>State:</b> {escape(place_of_supply)}")
    info = Table([
        [Paragraph(left_info, st["n"]), Paragraph(right_info, st["n"])],
        [Paragraph(bill_to, st["n"]), Paragraph(bill_to_r, st["n"])],
    ], colWidths=[page_w / 2, page_w / 2])
    info.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, colors.black),
                              ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
                              ("LINEAFTER", (0, 0), (0, -1), 0.5, colors.black),
                              ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(info)
    story.append(Spacer(1, 3 * mm))

    # --- Items table -----------------------------------------------------
    if intra:
        header = ["Sr", "Description of Goods / Services", "HSN / SAC", "Qty", "Rate (Rs.)", "Taxable Value",
                  "GST %", "CGST (Rs.)", "SGST (Rs.)", "Total (Rs.)"]
        widths = [9, 40, 16, 11, 18, 24, 10, 20, 20, 22]
    else:
        header = ["Sr", "Description of Goods / Services", "HSN / SAC", "Qty", "Rate (Rs.)", "Taxable Value",
                  "GST %", "IGST (Rs.)", "Total (Rs.)"]
        widths = [9, 40, 16, 11, 18, 24, 10, 40, 22]

    rows = [[P(h, "h") for h in header]]
    for i, l in enumerate(inv["lines"], 1):
        desc = f"<b>{escape(l['name'])}</b>"
        sub = " | ".join(x for x in (l["capacity"], l["service_type"]) if x and x != "N/A")
        if sub:
            desc += f"<br/><font size=7>{escape(sub)}</font>"
        row = [P(i, "c"), Paragraph(desc, st["n"]), P(l["hsn"], "c"), P(fmt_qty(l["qty"]), "c"),
               P(fmt_inr(l["unit_rate"]), "r"), P(fmt_inr(l["taxable"]), "r"),
               P(f"{l['gst_rate'].normalize():f}%", "c")]
        if intra:
            row += [P(fmt_inr(l["cgst"]), "r"), P(fmt_inr(l["sgst"]), "r")]
        else:
            row += [P(fmt_inr(l["igst"]), "r")]
        row += [P(fmt_inr(l["line_total"]), "r")]
        rows.append(row)

    tot = [P("", "n"), P("TOTAL", "b"), P("", "n"), P("", "n"), P("", "n"), P(fmt_inr(inv["taxable"]), "rb"), P("", "n")]
    if intra:
        tot += [P(fmt_inr(inv["cgst"]), "rb"), P(fmt_inr(inv["sgst"]), "rb")]
    else:
        tot += [P(fmt_inr(inv["igst"]), "rb")]
    tot += [P(fmt_inr(inv["raw_total"]), "rb")]
    rows.append(tot)

    items_tbl = Table(rows, colWidths=[w * mm for w in widths], repeatRows=1)
    items_tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8E8E8")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#F5F5F5")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    story.append(items_tbl)
    story.append(Spacer(1, 3 * mm))

    # --- Words + totals ---------------------------------------------------
    paid = _d(log.get("paid_amount", 0))
    due = _d(log.get("balance_due", 0))
    # old bills: invoice total may differ if tax mode changed; show computed total
    grand = inv["grand_total"]

    left_box = (f"<b>Amount in Words:</b><br/>{escape(amount_in_words(grand))}<br/><br/>"
                f"<b>Amount Paid:</b> Rs. {fmt_inr(paid)}<br/>"
                f"<b>Balance Due:</b> Rs. {fmt_inr(due)}")
    sum_rows = [[P("Taxable Value", "n"), P(fmt_inr(inv["taxable"]), "r")]]
    if intra:
        sum_rows += [[P("CGST", "n"), P(fmt_inr(inv["cgst"]), "r")],
                     [P("SGST", "n"), P(fmt_inr(inv["sgst"]), "r")]]
    else:
        sum_rows += [[P("IGST", "n"), P(fmt_inr(inv["igst"]), "r")]]
    sum_rows += [[P("Round Off", "n"), P(fmt_inr(inv["round_off"]), "r")],
                 [P("GRAND TOTAL (Rs.)", "b"), P(fmt_inr(grand), "rb")]]
    sum_tbl = Table(sum_rows, colWidths=[40 * mm, 35 * mm])
    sum_tbl.setStyle(TableStyle([("LINEABOVE", (0, -1), (-1, -1), 0.8, colors.black),
                                 ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E8E8E8")),
                                 ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    totals = Table([[Paragraph(left_box, st["n"]), sum_tbl]], colWidths=[page_w - 77 * mm, 77 * mm])
    totals.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, colors.black),
                                ("LINEAFTER", (0, 0), (0, 0), 0.5, colors.black),
                                ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(totals)
    story.append(Spacer(1, 3 * mm))

    # --- Tax summary by HSN ----------------------------------------------
    if intra:
        s_header = ["HSN / SAC", "Taxable Value", "GST %", "CGST (Rs.)", "SGST (Rs.)", "Total Tax (Rs.)"]
        s_w = [35, 35, 20, 33, 33, 34]
    else:
        s_header = ["HSN / SAC", "Taxable Value", "GST %", "IGST (Rs.)", "Total Tax (Rs.)"]
        s_w = [40, 45, 25, 40, 40]
    s_rows = [[P(h, "h") for h in s_header]]
    for s in inv["summary"]:
        r = [P(s["hsn"], "c"), P(fmt_inr(s["taxable"]), "r"), P(f"{s['gst_rate'].normalize():f}%", "c")]
        if intra:
            r += [P(fmt_inr(s["cgst"]), "r"), P(fmt_inr(s["sgst"]), "r")]
        else:
            r += [P(fmt_inr(s["igst"]), "r")]
        r += [P(fmt_inr(s["cgst"] + s["sgst"] + s["igst"]), "r")]
        s_rows.append(r)
    sum_hsn = Table(s_rows, colWidths=[w * mm for w in s_w])
    sum_hsn.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                                 ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8E8E8")),
                                 ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story.append(KeepTogether([P("Tax Summary", "b"), Spacer(1, 1 * mm), sum_hsn, Spacer(1, 3 * mm)]))

    # --- Terms + signature ------------------------------------------------
    terms = (f"<b>Service &amp; Testing Warranty</b><br/>"
             f"1 Year Hydraulic &amp; Safety Testing Warranty valid.<br/>"
             f"Next Service / Refill Due Date: <b>{next_service}</b><br/><br/>"
             f"<font size=7>Certified that the particulars given above are true and correct. "
             f"E. &amp; O.E.</font>")
    sign = (f"<b>For {escape(seller['name'])}</b><br/><br/><br/><br/>Authorised Signatory")
    foot = Table([[Paragraph(terms, st["n"]), Paragraph(sign, st["r"])]],
                 colWidths=[page_w * 0.6, page_w * 0.4])
    foot.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, colors.black),
                              ("LINEAFTER", (0, 0), (0, 0), 0.5, colors.black),
                              ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(KeepTogether([foot,
                               Spacer(1, 2 * mm),
                               P("This is a computer generated invoice.", "c")]))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=10 * mm, rightMargin=10 * mm,
                            topMargin=10 * mm, bottomMargin=12 * mm,
                            title=f"Tax Invoice {inv_no}", author=seller["name"])
    doc.build(story, onFirstPage=_page_border, onLaterPages=_page_border)
    return buf.getvalue()
