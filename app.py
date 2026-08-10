from flask import Flask, request, render_template_string, send_file
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.lib.utils import ImageReader
import uuid
import io
import re
import json
import base64
import os
import datetime

import firebase_admin
from firebase_admin import credentials, firestore, storage

app = Flask(__name__)

# ============================================================
# Firebase
# ============================================================

firebase_json_string = os.environ.get("FIREBASE_JSON")
db = None
bucket = None

if firebase_json_string:
    try:
        firebase_dict = json.loads(firebase_json_string)
        cred = credentials.Certificate(firebase_dict)

        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)

        db = firestore.client()

        storage_bucket_name = os.environ.get("FIREBASE_STORAGE_BUCKET")
        try:
            bucket = storage.bucket(storage_bucket_name) if storage_bucket_name else storage.bucket()
        except Exception as storage_error:
            print(f"Firebase Storage initialization error: {storage_error}")
            bucket = None

    except Exception as e:
        print(f"Firebase initialization error: {e}")
        db = None
        bucket = None
else:
    print("Warning: FIREBASE_JSON environment variable is not set!")


# ============================================================
# Helpers
# ============================================================

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "webp"}

def clean_text(text):
    if not text:
        return ""
    return re.sub(r"[^\x20-\x7E\n\x0c]", "", text)


def is_allowed(filename):
    return (
        bool(filename)
        and "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def image_to_pdf(image_bytes):
    """
    Converts a raster image into a one-page PDF without destroying
    the image. The image itself becomes the page background.
    No OCR or text reconstruction is used here.
    """
    img = ImageReader(io.BytesIO(image_bytes))
    width, height = img.getSize()

    # Keep a sensible PDF size while preserving the image's aspect ratio.
    max_points = 1400.0
    scale = min(1.0, max_points / max(width, height))

    page_w = width * scale
    page_h = height * scale

    out = io.BytesIO()
    c = pdf_canvas.Canvas(out, pagesize=(page_w, page_h))
    c.drawImage(
        img,
        0,
        0,
        width=page_w,
        height=page_h,
        preserveAspectRatio=True,
        mask="auto",
    )
    c.showPage()
    c.save()
    out.seek(0)
    return out.read()


def normalize_uploaded_file(filename, file_bytes):
    """
    Returns a PDF for both PDFs and supported images.
    The original visual content is preserved.
    """
    ext = filename.rsplit(".", 1)[1].lower()

    if ext == "pdf":
        # Validate the PDF before saving it.
        PdfReader(io.BytesIO(file_bytes))
        return file_bytes, "application/pdf"

    if ext in {"png", "jpg", "jpeg", "webp"}:
        return image_to_pdf(file_bytes), "application/pdf"

    raise ValueError("Unsupported file type.")


def extract_page_texts(pdf_bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    texts = []
    for page in reader.pages:
        try:
            texts.append(clean_text(page.extract_text() or ""))
        except Exception:
            texts.append("")
    return texts


def get_pdf_page_sizes(pdf_bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    sizes = []
    for page in reader.pages:
        box = page.mediabox
        sizes.append(
            (
                float(box.width),
                float(box.height),
            )
        )
    return sizes


def find_text_rectangles(page, target):
    """
    Finds approximate rectangles for target text in a real text PDF.

    This is deliberately best-effort. If a page is a scanned image,
    there is normally no PDF text layer, so no rectangle is returned.
    The original scanned page is still preserved perfectly.
    """
    target = (target or "").strip()
    if not target:
        return []

    rectangles = []

    try:
        # pypdf visitor_text gives coordinates for text fragments.
        def visitor_text(text, cm, tm, font_dict, font_size):
            value = (text or "").strip()
            if not value:
                return

            if target.lower() not in value.lower():
                return

            try:
                x = float(tm[4])
                y = float(tm[5])
            except Exception:
                return

            # Approximate text width.
            width = max(20.0, min(500.0, len(value) * max(float(font_size), 7) * 0.55))
            height = max(10.0, float(font_size) * 1.35)

            rectangles.append(
                {
                    "x": x - 2,
                    "y": y - 2,
                    "w": width + 4,
                    "h": height + 4,
                }
            )

        page.extract_text(visitor_text=visitor_text)
    except Exception:
        # Some PDFs do not expose usable text coordinates.
        pass

    return rectangles


def download_original_pdf(storage_path):
    if not bucket:
        raise RuntimeError("Firebase Storage is not configured.")

    blob = bucket.blob(storage_path)

    if not blob.exists():
        raise FileNotFoundError("Original PDF not found in Firebase Storage.")

    return blob.download_as_bytes()


# ============================================================
# PDF generation
# ============================================================

def build_personalized_pdf(template_data):
    """
    IMPORTANT:
    The old version rebuilt the document from extracted text using
    ReportLab. That destroyed images, graphs, tables, borders,
    scanned pages, certificates, marksheets, etc.

    This version NEVER rebuilds the document from text.

    It starts with the ORIGINAL PDF and places only the requested
    editable overlays on top. Therefore all original visual content
    stays intact.
    """
    original_pdf = template_data["original_pdf_bytes"]
    page_configs = template_data.get("page_configs", {})
    header_fields = template_data.get("header_fields", ["NAME", "ROLL NO"])
    header_field_vals = template_data.get("header_field_vals", {})
    replacements = template_data.get("replacements", {})

    reader = PdfReader(io.BytesIO(original_pdf))
    writer = PdfWriter()

    for page_number, page in enumerate(reader.pages, start=1):
        cfg = page_configs.get(str(page_number), {})
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)

        overlay = io.BytesIO()
        c = pdf_canvas.Canvas(overlay, pagesize=(page_width, page_height))

        # --------------------------------------------------------
        # Header / personalization box
        # --------------------------------------------------------
        if cfg.get("enable_header", False):
            pos_x = float(cfg.get("pos_x", 220))
            pos_y = float(cfg.get("pos_y", 15))

            box_width = 190
            box_height = max(28, 16 + len(header_fields) * 14)

            pdf_x = max(5, min(pos_x, page_width - box_width - 5))
            pdf_y = max(5, page_height - pos_y - box_height)

            c.setLineWidth(1)
            c.rect(pdf_x, pdf_y, box_width, box_height, stroke=1, fill=0)

            c.setFont("Helvetica-Bold", 8.5)
            current_y = pdf_y + box_height - 12

            for i, field in enumerate(header_fields):
                value = str(header_field_vals.get(str(i), "")).strip().upper()

                # Prevent very long user input from leaving the page.
                line = f"{str(field).upper()}: {value}"
                if len(line) > 48:
                    line = line[:45] + "..."

                c.drawString(pdf_x + 8, current_y, line)
                current_y -= 13

        # --------------------------------------------------------
        # Text replacements
        # --------------------------------------------------------
        #
        # Only real text-layer PDFs can be automatically located.
        # For scanned/image pages, the page is preserved and no
        # destructive text reconstruction occurs.
        #
        # We cover the detected original text with a white rectangle
        # and draw the replacement over it.
        #
        targets = cfg.get("targets", [])
        for target_index, target in enumerate(targets):
            target = str(target).strip()
            replacement = str(
                replacements.get(f"{page_number}_{target_index}", "")
            ).strip()

            if not target or not replacement:
                continue

            rectangles = find_text_rectangles(page, target)

            for rect in rectangles:
                x = rect["x"]
                y = rect["y"]
                w = rect["w"]
                h = rect["h"]

                # Keep overlay inside page boundaries.
                x = max(0, min(x, page_width - 1))
                y = max(0, min(y, page_height - 1))
                w = max(1, min(w, page_width - x))
                h = max(1, min(h, page_height - y))

                # White cover for the old text.
                c.setFillColorRGB(1, 1, 1)
                c.setStrokeColorRGB(1, 1, 1)
                c.rect(x, y, w, h, stroke=0, fill=1)

                # Replacement text.
                c.setFillColorRGB(0, 0, 0)
                font_size = max(7, min(14, h * 0.70))
                c.setFont("Helvetica", font_size)

                display_value = replacement
                max_chars = max(1, int(w / max(font_size * 0.50, 1)))
                if len(display_value) > max_chars:
                    display_value = display_value[: max_chars - 1] + "…"

                c.drawString(x + 2, y + max(1, (h - font_size) / 2), display_value)

        c.save()
        overlay.seek(0)

        overlay_reader = PdfReader(overlay)
        if overlay_reader.pages:
            page.merge_page(overlay_reader.pages[0])

        writer.add_page(page)

    output = io.BytesIO()
    writer.write(output)
    output.seek(0)
    return output.read()


# ============================================================
# Creator UI
# ============================================================

CREATOR_HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dropy — Document Template Creator</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.min.js"></script>
<style>
* { box-sizing: border-box; }
body {
    margin: 0;
    padding: 20px;
    background: #eef2f5;
    font-family: Arial, sans-serif;
    color: #222;
}
.card {
    max-width: 1180px;
    margin: auto;
    background: white;
    padding: 24px;
    border-radius: 14px;
    box-shadow: 0 5px 22px rgba(0,0,0,.10);
}
h1 { margin-top: 0; }
.subtitle { color: #666; }
.upload-box {
    border: 2px dashed #9bbbe8;
    background: #f5f9ff;
    padding: 25px;
    text-align: center;
    border-radius: 12px;
}
input[type="file"] {
    width: 100%;
    padding: 12px;
}
button {
    border: 0;
    border-radius: 8px;
    padding: 12px 18px;
    font-weight: 700;
    cursor: pointer;
}
.primary { background: #007bff; color: white; }
.green { background: #28a745; color: white; }
.gray { background: #6c757d; color: white; }
.red { background: #dc3545; color: white; }

.section {
    margin-top: 25px;
    padding: 18px;
    border: 1px solid #ddd;
    border-radius: 10px;
}
.field-row {
    display: flex;
    gap: 8px;
    margin-bottom: 8px;
    flex-wrap: wrap;
}
.field-row input {
    padding: 10px;
    border: 1px solid #ccc;
    border-radius: 6px;
    flex: 1;
    min-width: 150px;
}
.page-block {
    margin-top: 20px;
    border: 1px solid #ddd;
    border-radius: 10px;
    padding: 16px;
}
.page-title {
    font-weight: 700;
    margin-bottom: 12px;
}
.page-grid {
    display: grid;
    grid-template-columns: 330px 1fr;
    gap: 18px;
}
.controls {
    background: #f7f8fa;
    padding: 14px;
    border-radius: 8px;
}
.controls label {
    display: block;
    font-weight: 700;
    margin-bottom: 7px;
}
.controls input[type="text"] {
    width: 100%;
    padding: 9px;
    margin-bottom: 10px;
    border: 1px solid #ccc;
    border-radius: 6px;
}
.preview {
    text-align: center;
    overflow: auto;
}
.canvas-wrap {
    position: relative;
    display: inline-block;
    background: white;
    box-shadow: 0 2px 12px rgba(0,0,0,.15);
}
.canvas-wrap canvas {
    display: block;
    max-width: 100%;
    height: auto;
}
.drag-box {
    position: absolute;
    left: 20px;
    top: 20px;
    min-width: 140px;
    padding: 8px;
    border: 2px dashed #007bff;
    background: rgba(255,255,255,.92);
    cursor: move;
    font-size: 10px;
    text-align: left;
    z-index: 10;
    touch-action: none;
}
.notice {
    background: #fff8df;
    border: 1px solid #f0d66b;
    padding: 12px;
    border-radius: 8px;
    color: #665500;
    margin-top: 12px;
}
.success {
    background: #e8fff0;
    border: 1px solid #a8e6bb;
    padding: 18px;
    border-radius: 10px;
    margin-top: 20px;
}
.share {
    width: 100%;
    padding: 10px;
    margin: 8px 0;
}
@media(max-width: 800px) {
    .page-grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<div class="card">

<h1>📄 Dropy Document Template Creator</h1>
<p class="subtitle">
Create a shareable personalization link while preserving the original document exactly.
</p>

<div class="upload-box">
<form action="/upload" method="post" enctype="multipart/form-data">
    <input
        type="file"
        name="file"
        accept=".pdf,.png,.jpg,.jpeg,.webp"
        required
    >
    <br><br>
    <button class="primary" type="submit">Upload & Open Editor</button>
</form>

<div class="notice">
<b>Supports:</b> PDFs, scanned PDFs, images, forms, tables, graphs,
diagrams, timetables, marksheets, certificates and mixed-content pages.
<br>
The original visual page is preserved instead of rebuilding it from extracted text.
</div>
</div>

{% if pdf_b64 %}
<div class="section">
<h2>🏷️ Global Student Fields</h2>
<p>These fields will be shown to the student and can be placed on each page.</p>

<form action="/create" method="post" id="editor-form">
<input type="hidden" name="doc_id" value="{{ doc_id }}">
<input type="hidden" name="page_configs_json" id="page_configs_json">
<input type="hidden" name="header_fields_json" id="header_fields_json">

<div id="fields-container"></div>

<button type="button" class="gray" onclick="addHeaderField()">+ Add Field</button>
</div>

<div class="section">
<h2>📑 Page-by-Page Setup</h2>
<div id="pages-wrapper"></div>

<br>
<button type="button" class="green" onclick="submitConfiguration()">
Generate Shareable Link
</button>
</div>
</form>
{% endif %}

{% if share_url %}
<div class="success">
<h2>✅ Shareable Link Created</h2>
<input class="share" id="share-link" value="{{ share_url }}" readonly onclick="this.select()">
<button class="gray" onclick="copyLink()">Copy Link</button>
<br><br>
<a href="https://api.whatsapp.com/send?text=Fill%20out%20your%20document:%20{{ share_url }}"
   target="_blank">
Share via WhatsApp
</a>
</div>
{% endif %}

</div>

{% if pdf_b64 %}
<script>
const pdfData = "data:application/pdf;base64,{{ pdf_b64 }}";
const totalPages = {{ total_pages }};
const scale = 0.85;

function copyLink() {
    const input = document.getElementById("share-link");
    input.select();
    navigator.clipboard.writeText(input.value);
}

function addHeaderField(label = "NAME", sample = "KHALID KHAN") {
    const container = document.getElementById("fields-container");
    const row = document.createElement("div");
    row.className = "field-row";

    row.innerHTML = `
        <input class="field-label" placeholder="Field Label" value="${escapeHtml(label)}">
        <input class="field-sample" placeholder="Sample Value" value="${escapeHtml(sample)}">
        <button type="button" class="red" onclick="this.parentElement.remove()">X</button>
    `;

    container.appendChild(row);
}

function escapeHtml(value) {
    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function renderPage(pdf, pageNum) {
    pdf.getPage(pageNum).then(page => {
        const canvas = document.getElementById("pdf-render-" + pageNum);
        const ctx = canvas.getContext("2d");
        const viewport = page.getViewport({scale: scale});

        canvas.width = viewport.width;
        canvas.height = viewport.height;

        page.render({
            canvasContext: ctx,
            viewport: viewport
        }).promise.then(() => {
            initDragBox(pageNum);
        });
    });
}

function initDragBox(pageNum) {
    const box = document.getElementById("drag-box-" + pageNum);
    const container = document.getElementById("container-" + pageNum);

    let dragging = false;
    let startX = 0;
    let startY = 0;
    let initialLeft = 0;
    let initialTop = 0;

    function start(x, y) {
        dragging = true;
        startX = x;
        startY = y;
        initialLeft = box.offsetLeft;
        initialTop = box.offsetTop;
    }

    function move(x, y) {
        if (!dragging) return;

        const dx = x - startX;
        const dy = y - startY;

        const left = Math.max(
            0,
            Math.min(
                initialLeft + dx,
                container.clientWidth - box.offsetWidth
            )
        );

        const top = Math.max(
            0,
            Math.min(
                initialTop + dy,
                container.clientHeight - box.offsetHeight
            )
        );

        box.style.left = left + "px";
        box.style.top = top + "px";
    }

    function stop() {
        dragging = false;
    }

    box.addEventListener("mousedown", e => {
        start(e.clientX, e.clientY);
        e.preventDefault();
    });

    document.addEventListener("mousemove", e => move(e.clientX, e.clientY));
    document.addEventListener("mouseup", stop);

    box.addEventListener("touchstart", e => {
        if (e.touches.length === 1) {
            start(e.touches[0].clientX, e.touches[0].clientY);
            e.preventDefault();
        }
    }, {passive:false});

    document.addEventListener("touchmove", e => {
        if (dragging && e.touches.length === 1) {
            move(e.touches[0].clientX, e.touches[0].clientY);
            e.preventDefault();
        }
    }, {passive:false});

    document.addEventListener("touchend", stop);
}

function buildPages() {
    const wrapper = document.getElementById("pages-wrapper");

    for (let i = 1; i <= totalPages; i++) {
        const block = document.createElement("div");
        block.className = "page-block";

        block.innerHTML = `
            <div class="page-title">PAGE ${i} OF ${totalPages}</div>

            <div class="page-grid">
                <div class="controls">

                    <label>
                        <input
                            type="checkbox"
                            id="header_enable_${i}"
                            checked
                        >
                        Enable Student Info Box
                    </label>

                    <label>Text to Replace</label>
                    <input
                        type="text"
                        id="replacements_${i}"
                        placeholder="Example: Student Name"
                    >

                    <small>
                        You may enter multiple targets separated by commas.
                    </small>

                    <div class="notice">
                        For scanned/image pages, the original page remains
                        intact. Automatic text replacement requires a real
                        PDF text layer.
                    </div>
                </div>

                <div class="preview">
                    <div class="canvas-wrap" id="container-${i}">
                        <canvas id="pdf-render-${i}"></canvas>

                        <div
                            class="drag-box"
                            id="drag-box-${i}"
                        >
                            <b>NAME:</b> SAMPLE<br>
                            <b>ROLL NO:</b> 10
                        </div>
                    </div>
                </div>
            </div>
        `;

        wrapper.appendChild(block);
    }

    pdfjsLib.getDocument(pdfData).promise.then(pdf => {
        for (let i = 1; i <= totalPages; i++) {
            renderPage(pdf, i);
        }
    });
}

function updateBoxes() {
    const labels = document.querySelectorAll(".field-label");
    const samples = document.querySelectorAll(".field-sample");

    let html = "";

    labels.forEach((label, index) => {
        const name = label.value.trim() || "FIELD";
        const sample = samples[index] ? samples[index].value.trim() : "";
        html += `<div><b>${escapeHtml(name)}:</b> ${escapeHtml(sample)}</div>`;
    });

    for (let i = 1; i <= totalPages; i++) {
        const box = document.getElementById("drag-box-" + i);
        if (box) box.innerHTML = html;
    }
}

function submitConfiguration() {
    const labels = document.querySelectorAll(".field-label");
    const samples = document.querySelectorAll(".field-sample");

    const fields = [];
    labels.forEach((label, index) => {
        const value = label.value.trim();
        if (value) fields.push(value);
    });

    const configs = {};

    for (let i = 1; i <= totalPages; i++) {
        const box = document.getElementById("drag-box-" + i);
        const raw = document.getElementById("replacements_" + i).value;

        const targets = raw
            .split(",")
            .map(x => x.trim())
            .filter(Boolean);

        const left = parseFloat(box.style.left || "20");
        const top = parseFloat(box.style.top || "20");

        configs[i] = {
            enable_header: document.getElementById("header_enable_" + i).checked,
            pos_x: left / scale,
            pos_y: top / scale,
            targets: targets
        };
    }

    document.getElementById("page_configs_json").value = JSON.stringify(configs);
    document.getElementById("header_fields_json").value = JSON.stringify(fields);

    document.getElementById("editor-form").submit();
}

window.addEventListener("load", () => {
    addHeaderField("NAME", "KHALID KHAN");
    addHeaderField("ROLL NO", "10");

    document
        .getElementById("fields-container")
        .addEventListener("input", updateBoxes);

    buildPages();
});
</script>
{% endif %}
</body>
</html>
"""


# ============================================================
# Student UI
# ============================================================

STUDENT_HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Personalize Your Document — Dropy</title>
<style>
* { box-sizing: border-box; }
body {
    font-family: Arial, sans-serif;
    background: #eef2f5;
    margin: 0;
    padding: 20px;
}
.card {
    max-width: 600px;
    margin: auto;
    background: white;
    padding: 24px;
    border-radius: 14px;
    box-shadow: 0 5px 22px rgba(0,0,0,.10);
}
.field {
    margin-bottom: 15px;
}
.field label {
    display: block;
    font-weight: 700;
    margin-bottom: 6px;
}
.field input {
    width: 100%;
    padding: 11px;
    border: 1px solid #ccc;
    border-radius: 7px;
}
.page-section {
    background: #f7f8fa;
    border: 1px solid #ddd;
    padding: 15px;
    border-radius: 9px;
    margin-top: 15px;
}
button {
    width: 100%;
    padding: 13px;
    border: 0;
    border-radius: 8px;
    background: #28a745;
    color: white;
    font-size: 16px;
    font-weight: 700;
    cursor: pointer;
}
.notice {
    background: #fff8df;
    border: 1px solid #f0d66b;
    padding: 12px;
    border-radius: 8px;
    margin-bottom: 18px;
}
</style>
</head>
<body>

<div class="card">
<h2>📝 Personalize Your Document</h2>

<div class="notice">
Your information will be placed onto the original document.
Images, tables, graphs, diagrams, borders and scanned pages are preserved.
</div>

<form action="/doc/{{ doc_id }}/process" method="post">

{% if has_header and header_fields %}
<div class="page-section">
<h3>Student Details</h3>

{% for field in header_fields %}
<div class="field">
<label>{{ field }}</label>
<input
    type="text"
    name="header_field_{{ loop.index0 }}"
    placeholder="Enter {{ field }}"
    required
>
</div>
{% endfor %}
</div>
{% endif %}

{% for page_num, targets in page_targets.items() %}
{% if targets %}
<div class="page-section">
<h3>Page {{ page_num }}</h3>

{% for target in targets %}
<div class="field">
<label>Replace "{{ target }}" with</label>
<input
    type="text"
    name="rep_{{ page_num }}_{{ loop.index0 }}"
    placeholder="Enter new value"
    required
>
</div>
{% endfor %}
</div>
{% endif %}
{% endfor %}

<br>
<button type="submit">Generate My PDF</button>

</form>
</div>

</body>
</html>
"""


SUCCESS_HTML = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Your PDF is Ready — Dropy</title>
<style>
body {
    font-family: Arial, sans-serif;
    background: #eef2f5;
    padding: 30px;
}
.card {
    max-width: 550px;
    margin: auto;
    background: white;
    padding: 28px;
    border-radius: 14px;
    text-align: center;
    box-shadow: 0 5px 22px rgba(0,0,0,.10);
}
.btn {
    display: block;
    width: 100%;
    padding: 13px;
    margin-top: 14px;
    border-radius: 8px;
    text-decoration: none;
    font-weight: 700;
}
.download {
    background: #28a745;
    color: white;
}
.whatsapp {
    background: #25d366;
    color: white;
}
</style>
</head>
<body>
<div class="card">
<h1>🎉</h1>
<h2>Your Personalized PDF is Ready!</h2>

<p>
The original page content has been preserved.
</p>

<a class="btn download"
   href="/doc/{{ doc_id }}/download/{{ file_token }}">
📥 Download Personalized PDF
</a>

<a class="btn whatsapp"
   href="https://api.whatsapp.com/send?text=Check%20out%20my%20personalized%20Dropy%20document:%20{{ download_url }}"
   target="_blank">
Share via WhatsApp
</a>

</div>
</body>
</html>
"""


# ============================================================
# Routes
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return render_template_string(CREATOR_HTML)


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("file")

    if not file or not file.filename:
        return "Please select a PDF or image.", 400

    if not is_allowed(file.filename):
        return "Unsupported file type. Use PDF, PNG, JPG, JPEG or WEBP.", 400

    try:
        uploaded_bytes = file.read()

        if not uploaded_bytes:
            return "The uploaded file is empty.", 400

        pdf_bytes, content_type = normalize_uploaded_file(
            file.filename,
            uploaded_bytes
        )

        pages_text = extract_page_texts(pdf_bytes)
        page_sizes = get_pdf_page_sizes(pdf_bytes)

    except Exception as e:
        print(f"Upload processing error: {e}")
        return "Unable to read this document. Please upload a valid PDF or image.", 400

    doc_id = str(uuid.uuid4())[:8]

    if not db or not bucket:
        return (
            "Firebase Firestore/Storage is not configured correctly. "
            "Please check FIREBASE_JSON and Firebase Storage settings."
        ), 500

    try:
        original_path = f"templates/{doc_id}/original.pdf"

        original_blob = bucket.blob(original_path)
        original_blob.upload_from_string(
            pdf_bytes,
            content_type="application/pdf"
        )

        doc_data = {
            "storage_path": original_path,
            "pages_text": pages_text,
            "page_sizes": [
                {"width": w, "height": h}
                for w, h in page_sizes
            ],
            "total_pages": len(pages_text),
            "page_configs": {},
            "header_fields": ["NAME", "ROLL NO"],
            "generated_files": {},
            "created_at": datetime.datetime.now(datetime.timezone.utc),
            "version": 2,
            "preserves_original_pages": True
        }

        db.collection("pdf_templates").document(doc_id).set(doc_data)

    except Exception as e:
        print(f"Firebase save error: {e}")
        return "Unable to save the document. Please try again.", 500

    b64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

    return render_template_string(
        CREATOR_HTML,
        pdf_b64=b64_pdf,
        doc_id=doc_id,
        total_pages=len(pages_text)
    )


@app.route("/create", methods=["POST"])
def create_link():
    doc_id = request.form.get("doc_id")
    configs_json = request.form.get("page_configs_json", "{}")
    fields_json = request.form.get("header_fields_json", "[]")

    try:
        page_configs = json.loads(configs_json)
        header_fields = json.loads(fields_json)
    except Exception:
        return "Invalid editor configuration.", 400

    if not db:
        return "Database not connected!", 500

    doc_ref = db.collection("pdf_templates").document(doc_id)
    doc = doc_ref.get()

    if not doc.exists:
        return "Session expired or document not found. Please re-upload.", 400

    doc_ref.update({
        "page_configs": page_configs,
        "header_fields": header_fields
    })

    share_url = f"{request.host_url}doc/{doc_id}"

    return render_template_string(
        CREATOR_HTML,
        share_url=share_url
    )


@app.route("/doc/<doc_id>", methods=["GET"])
def student_view(doc_id):
    if not db:
        return "Database not connected!", 500

    doc_ref = db.collection("pdf_templates").document(doc_id)
    doc = doc_ref.get()

    if not doc.exists:
        return "Document link not found!", 404

    template_data = doc.to_dict()

    page_configs = template_data.get("page_configs", {})
    header_fields = template_data.get(
        "header_fields",
        ["NAME", "ROLL NO"]
    )

    has_header = any(
        cfg.get("enable_header", False)
        for cfg in page_configs.values()
    )

    page_targets = {}

    for page_num, cfg in page_configs.items():
        targets = cfg.get("targets", [])
        if targets:
            page_targets[str(page_num)] = targets

    return render_template_string(
        STUDENT_HTML,
        doc_id=doc_id,
        has_header=has_header,
        header_fields=header_fields,
        page_targets=page_targets
    )


@app.route("/doc/<doc_id>/process", methods=["POST"])
def process_student_form(doc_id):
    if not db:
        return "Database not connected!", 500

    doc_ref = db.collection("pdf_templates").document(doc_id)
    doc = doc_ref.get()

    if not doc.exists:
        return "Document not found!", 404

    template_data = doc.to_dict()

    try:
        original_pdf = download_original_pdf(
            template_data["storage_path"]
        )
    except Exception as e:
        print(f"Original PDF retrieval error: {e}")
        return "Unable to retrieve the original document.", 500

    header_fields = template_data.get(
        "header_fields",
        ["NAME", "ROLL NO"]
    )

    header_field_vals = {}

    for i, field in enumerate(header_fields):
        header_field_vals[str(i)] = request.form.get(
            f"header_field_{i}",
            ""
        )

    replacements = {}

    page_configs = template_data.get(
        "page_configs",
        {}
    )

    for page_num_str, cfg in page_configs.items():
        targets = cfg.get("targets", [])

        for target_index, target in enumerate(targets):
            replacements[
                f"{page_num_str}_{target_index}"
            ] = request.form.get(
                f"rep_{page_num_str}_{target_index}",
                ""
            )

    temp_storage = dict(template_data)

    # Use the original document as the source of truth.
    temp_storage["original_pdf_bytes"] = original_pdf
    temp_storage["header_field_vals"] = header_field_vals
    temp_storage["replacements"] = replacements

    try:
        pdf_bytes = build_personalized_pdf(temp_storage)
    except Exception as e:
        print(f"PDF generation error: {e}")
        return "Unable to generate the personalized PDF.", 500

    if not bucket:
        return "Firebase Storage is not configured correctly.", 500

    file_token = str(uuid.uuid4())[:8]
    generated_path = f"generated/{doc_id}/{file_token}.pdf"

    try:
        generated_blob = bucket.blob(generated_path)

        generated_blob.upload_from_string(
            pdf_bytes,
            content_type="application/pdf"
        )

        generated_files = template_data.get(
            "generated_files",
            {}
        )

        generated_files[file_token] = {
            "storage_path": generated_path,
            "created_at": datetime.datetime.now(
                datetime.timezone.utc
            )
        }

        doc_ref.update({
            "generated_files": generated_files
        })

    except Exception as e:
        print(f"Generated PDF storage error: {e}")
        return "Unable to save the generated PDF.", 500

    download_url = (
        f"{request.host_url}"
        f"doc/{doc_id}/download/{file_token}"
    )

    return render_template_string(
        SUCCESS_HTML,
        doc_id=doc_id,
        file_token=file_token,
        download_url=download_url
    )


@app.route("/doc/<doc_id>/download/<file_token>", methods=["GET"])
def download_pdf(doc_id, file_token):
    if not db:
        return "Database not connected!", 500

    doc_ref = db.collection("pdf_templates").document(doc_id)
    doc = doc_ref.get()

    if not doc.exists:
        return "Document not found!", 404

    template_data = doc.to_dict()
    generated_files = template_data.get(
        "generated_files",
        {}
    )

    if file_token not in generated_files:
        return "Generated file not found.", 404

    file_info = generated_files[file_token]

    if isinstance(file_info, str):
        # Backward compatibility with old Base64 storage.
        try:
            pdf_bytes = base64.b64decode(file_info)
        except Exception:
            return "Stored file is invalid.", 500
    else:
        storage_path = file_info.get("storage_path")

        if not storage_path or not bucket:
            return "Stored file is unavailable.", 404

        try:
            blob = bucket.blob(storage_path)

            if not blob.exists():
                return "Stored file does not exist.", 404

            pdf_bytes = blob.download_as_bytes()

        except Exception as e:
            print(f"Firebase download error: {e}")
            return "Unable to retrieve the generated PDF.", 500

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"personalized_{doc_id}.pdf"
    )


# ============================================================
# Start
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )