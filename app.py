from flask import Flask, request, render_template_string, send_file, make_response
from pypdf import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, PageBreak
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
import uuid
import io
import re
import json
import base64
import os

app = Flask(__name__)

TEMPLATES = {}

CREATOR_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Interactive Multi-Page PDF Editor</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/2.16.105/pdf.min.js"></script>
    <style>
        body { font-family: Arial, sans-serif; background: #eef2f5; margin: 0; padding: 15px; color: #333; text-align: center; box-sizing: border-box; }
        .card { background: white; max-width: 1080px; margin: 0 auto; padding: 25px; border-radius: 10px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); box-sizing: border-box; }
        input[type="text"], input[type="file"] { width: 90%; padding: 10px; margin: 8px 0; border: 1px solid #ccc; border-radius: 5px; box-sizing: border-box; }
        button { background-color: #007bff; color: white; padding: 12px 24px; border: none; border-radius: 5px; font-weight: bold; cursor: pointer; font-size: 15px; }
        button:hover { background-color: #0056b3; }
        
        .global-box-builder { background: #eef6ff; border: 1px solid #b6d4fe; border-radius: 8px; padding: 20px; margin-bottom: 25px; text-align: left; }
        .field-row { display: flex; gap: 10px; margin-bottom: 8px; align-items: center; flex-wrap: wrap; }
        
        .page-editor-block { 
            background: #ffffff; 
            border: 2px solid #dcdfe6; 
            border-radius: 8px; 
            margin: 25px 0; 
            padding: 20px; 
            text-align: left; 
        }
        .page-header { background: #007bff; color: white; padding: 8px 15px; border-radius: 5px; font-weight: bold; margin-bottom: 15px; display: inline-block; }
        
        .page-editor-grid {
            display: flex;
            gap: 20px;
            align-items: flex-start;
            flex-wrap: wrap;
        }
        .left-controls {
            flex: 1;
            min-width: 250px;
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            padding: 15px;
            border-radius: 8px;
            box-sizing: border-box;
        }
        .right-preview {
            flex: 1.2;
            min-width: 280px;
            text-align: center;
            overflow-x: auto;
        }

        .canvas-container { position: relative; display: inline-block; border: 2px solid #555; box-shadow: 0 4px 10px rgba(0,0,0,0.15); background: white; max-width: 100%; }
        canvas { max-width: 100%; height: auto !important; }
        .draggable-box {
            position: absolute; width: 200px; min-height: 50px; border: 2px dashed #007bff;
            background: rgba(255, 255, 255, 0.95); cursor: move; user-select: none; padding: 8px;
            font-size: 11px; font-weight: bold; color: #111; text-align: left; border-radius: 4px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.2);
            z-index: 10;
            touch-action: none;
        }
        .config-group { margin-bottom: 15px; }
        .success { background-color: #e6ffed; border: 1px solid #b7eb8f; padding: 20px; margin-top: 20px; border-radius: 6px; text-align: center; word-break: break-all; }
        
        /* WhatsApp Share Styling */
        .whatsapp-btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            background-color: #25d366;
            color: white;
            padding: 12px 24px;
            border-radius: 5px;
            font-weight: bold;
            font-size: 16px;
            text-decoration: none;
            margin-top: 15px;
            box-shadow: 0 4px 10px rgba(37, 211, 102, 0.3);
            transition: background-color 0.2s;
        }
        .whatsapp-btn:hover {
            background-color: #1ebe5d;
        }
        .share-row {
            display: flex;
            gap: 10px;
            justify-content: center;
            align-items: center;
            margin-top: 10px;
            flex-wrap: wrap;
        }
    </style>
</head>
<body>
    <div class="card">
        <h2>📄 1. Upload Original PDF Document</h2>
        <form action="/upload" method="post" enctype="multipart/form-data">
            <input type="file" name="file" accept=".pdf" required><br><br>
            <button type="submit">Upload & Open Editor</button>
        </form>

        {% if pdf_b64 %}
        <hr style="margin: 30px 0;">
        <h2>📌 2. Configure Global Header Fields</h2>
        <p style="color: #666; font-size: 0.95em;">Define information fields (Name, Roll No, Class, Year, etc.) for the info box:</p>

        <form action="/create" method="post" id="editor-form" onsubmit="prepareFormData()">
            <input type="hidden" name="doc_id" value="{{ doc_id }}">
            <input type="hidden" name="page_configs_json" id="page_configs_json">
            <input type="hidden" name="header_fields_json" id="header_fields_json">

            <div class="global-box-builder">
                <h3>🏷️ Global Info Box Fields</h3>
                <div id="fields-container"></div>
                <button type="button" onclick="addHeaderField('NAME', 'KHALID KHAN')" style="background-color: #17a2b8; padding: 8px 14px; font-size: 13px; width: auto;">+ Add Field</button>
            </div>

            <h2>📌 3. Page-By-Page Independent Setup</h2>
            <div id="pages-wrapper"></div>

            <br>
            <button type="button" onclick="submitConfiguration()" style="background-color: #28a745; font-size: 17px; width: 85%; max-width: 400px;">Generate Shareable Link</button>
        </form>
        {% endif %}

        {% if share_url %}
        <div class="success">
            <h3>✅ Shareable Link Created!</h3>
            <p>Send this link to your classmate to personalize & download:</p>
            <div class="share-row">
                <input type="text" id="share-link-input" value="{{ share_url }}" readonly onclick="this.select()" style="text-align: center; font-weight: bold; color: #007bff; flex: 1; min-width: 250px; background: #fff; margin: 0;">
                <button type="button" onclick="copyShareLink()" style="background-color: #6c757d; padding: 10px 16px; width: auto; font-size: 14px;">Copy Link</button>
            </div>
            <br>
            <a href="https://api.whatsapp.com/send?text=Hey!%20Fill%20out%20your%20practical%20file%20using%20this%20link:%20{{ share_url }}" target="_blank" class="whatsapp-btn">
                <svg width="20" height="20" fill="currentColor" viewBox="0 0 24 24"><path d="M.057 24l1.687-6.163c-1.041-1.804-1.588-3.849-1.587-5.946.003-6.556 5.338-11.891 11.893-11.891 3.181.001 6.167 1.24 8.413 3.488 2.245 2.248 3.481 5.236 3.48 8.414-.003 6.557-5.338 11.892-11.893 11.892-1.99-.001-3.951-.5-5.688-1.448l-6.305 1.654zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884-.001 2.225.651 3.891 1.746 5.634l-.999 3.648 3.742-.981zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.768.967-.941 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372s-1.04 1.016-1.04 2.479 1.065 2.876 1.213 3.074c.149.198 2.095 3.2 5.076 4.487.709.306 1.263.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z"/></svg>
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

    window.onload = function() {
        if (document.getElementById('fields-container')) {
            addHeaderField("NAME", "KHALID KHAN");
            addHeaderField("ROLL NO", "10");
        }
    };

    function copyShareLink() {
        const input = document.getElementById('share-link-input');
        input.select();
        input.setSelectionRange(0, 99999);
        navigator.clipboard.writeText(input.value);
        alert("Link copied to clipboard!");
    }

    function addHeaderField(labelVal = "", sampleVal = "") {
        const container = document.getElementById('fields-container');
        const row = document.createElement('div');
        row.className = 'field-row';
        row.innerHTML = `
            <input type="text" class="field-label" placeholder="Field Label (e.g. CLASS)" value="${labelVal}" style="flex: 1; min-width: 120px;" oninput="updatePreviewBox()">
            <input type="text" class="field-sample" placeholder="Sample Value (e.g. TY BTECH)" value="${sampleVal}" style="flex: 1.5; min-width: 140px;" oninput="updatePreviewBox()">
            <button type="button" onclick="this.parentElement.remove(); updatePreviewBox();" style="background-color: #dc3545; padding: 8px 12px; font-size: 12px; width: auto;">X</button>
        `;
        container.appendChild(row);
        updatePreviewBox();
    }

    function updatePreviewBox() {
        const labels = document.querySelectorAll('.field-label');
        const samples = document.querySelectorAll('.field-sample');
        
        for (let i = 1; i <= totalPages; i++) {
            const box = document.getElementById(`drag-box-content-${i}`);
            if (!box) continue;
            
            let htmlText = "";
            labels.forEach((lbl, idx) => {
                const lName = lbl.value.trim() || "FIELD";
                const sVal = samples[idx] ? samples[idx].value.trim() : "";
                htmlText += `<div><b>${lName}:</b> ${sVal}</div>`;
            });
            box.innerHTML = htmlText;
        }
    }

    pdfjsLib.getDocument(pdfData).promise.then(pdf => {
        const wrapper = document.getElementById('pages-wrapper');

        for (let i = 1; i <= totalPages; i++) {
            const pageBlock = document.createElement('div');
            pageBlock.className = 'page-editor-block';
            pageBlock.id = `page-block-${i}`;

            let applyCheckboxHtml = '';
            let page1AutoSync = '';

            if (i === 1) {
                page1AutoSync = 'oninput="autoSyncPageOne(this.value)"';
                applyCheckboxHtml = '<br><small style="color: #007bff; font-weight: bold;">⚡ Page 1 automatically cascades down to all pages.</small>';
            } else {
                applyCheckboxHtml = `
                    <div style="margin-top: 6px;">
                        <label style="font-size: 0.85em; color: #0056b3; cursor: pointer;">
                            <input type="checkbox" id="apply_below_${i}" onclick="applyTextToBelow(${i})"> 
                            <b>Apply this text to all pages below this one</b>
                        </label>
                    </div>
                `;
            }

            pageBlock.innerHTML = `
                <div class="page-header">PAGE ${i} OF ${totalPages}</div>
                
                <div class="page-editor-grid">
                    <div class="left-controls">
                        <h4>⚙️ Page ${i} Settings</h4>
                        
                        <div class="config-group">
                            <label>
                                <input type="checkbox" id="header_enable_${i}" checked onchange="togglePageHeader(${i})">
                                <b> Enable Header Box on Page ${i}</b>
                            </label>
                        </div>

                        <div class="config-group">
                            <label><b>Target Words to Replace on Page ${i}:</b></label><br>
                            <input type="text" id="replacements_${i}" placeholder="e.g. sharma_ji_700" value="" ${page1AutoSync}>
                            ${applyCheckboxHtml}
                        </div>
                    </div>

                    <div class="right-preview">
                        <p style="color: #666; font-size: 0.85em; margin: 0 0 5px 0;"><b>Drag Page ${i} header box to position:</b></p>
                        <div class="canvas-container" id="container-${i}">
                            <canvas id="pdf-render-${i}"></canvas>
                            <div id="drag-box-${i}" class="draggable-box" style="top: 15px; left: 220px;">
                                <div id="drag-box-content-${i}"></div>
                            </div>
                        </div>
                    </div>
                </div>
            `;

            wrapper.appendChild(pageBlock);
            renderPageCanvas(pdf, i);
        }
        updatePreviewBox();
    });

    function autoSyncPageOne(val) {
        for (let i = 2; i <= totalPages; i++) {
            const input = document.getElementById(`replacements_${i}`);
            if (input) input.value = val;
        }
    }

    function applyTextToBelow(pageNum) {
        const checkbox = document.getElementById(`apply_below_${pageNum}`);
        if (checkbox.checked) {
            const currentVal = document.getElementById(`replacements_${pageNum}`).value;
            for (let i = pageNum + 1; i <= totalPages; i++) {
                const input = document.getElementById(`replacements_${i}`);
                if (input) input.value = currentVal;
            }
        }
    }

    function renderPageCanvas(pdf, pageNum) {
        pdf.getPage(pageNum).then(page => {
            const canvas = document.getElementById(`pdf-render-${pageNum}`);
            const ctx = canvas.getContext('2d');
            const viewport = page.getViewport({ scale: scale });

            canvas.height = viewport.height;
            canvas.width = viewport.width;

            page.render({ canvasContext: ctx, viewport: viewport });
            initDragBox(pageNum);
        });
    }

    function initDragBox(pageNum) {
        const dragBox = document.getElementById(`drag-box-${pageNum}`);
        const container = document.getElementById(`container-${pageNum}`);
        let isDragging = false, startX, startY, initialLeft, initialTop;

        function startDrag(clientX, clientY) {
            isDragging = true;
            startX = clientX;
            startY = clientY;
            initialLeft = dragBox.offsetLeft;
            initialTop = dragBox.offsetTop;
            dragBox.style.zIndex = 1000;
        }

        function onDrag(clientX, clientY) {
            if (!isDragging) return;
            const dx = clientX - startX;
            const dy = clientY - startY;
            let left = Math.max(0, Math.min(initialLeft + dx, container.clientWidth - dragBox.clientWidth));
            let top = Math.max(0, Math.min(initialTop + dy, container.clientHeight - dragBox.clientHeight));
            dragBox.style.left = left + 'px';
            dragBox.style.top = top + 'px';
        }

        function stopDrag() {
            isDragging = false;
            dragBox.style.zIndex = 10;
        }

        // Mouse events
        dragBox.addEventListener('mousedown', (e) => {
            startDrag(e.clientX, e.clientY);
            e.preventDefault();
        });
        document.addEventListener('mousemove', (e) => onDrag(e.clientX, e.clientY));
        document.addEventListener('mouseup', stopDrag);

        // Touch events for full mobile support
        dragBox.addEventListener('touchstart', (e) => {
            if (e.touches.length === 1) {
                startDrag(e.touches[0].clientX, e.touches[0].clientY);
                e.preventDefault();
            }
        }, { passive: false });

        document.addEventListener('touchmove', (e) => {
            if (isDragging && e.touches.length === 1) {
                onDrag(e.touches[0].clientX, e.touches[0].clientY);
                e.preventDefault();
            }
        }, { passive: false });

        document.addEventListener('touchend', stopDrag);
    }

    function togglePageHeader(pageNum) {
        const checked = document.getElementById(`header_enable_${pageNum}`).checked;
        document.getElementById(`drag-box-${pageNum}`).style.display = checked ? 'block' : 'none';
    }

    function submitConfiguration() {
        const labels = document.querySelectorAll('.field-label');
        const headerFields = [];
        labels.forEach(lbl => {
            const val = lbl.value.trim();
            if (val) headerFields.push(val);
        });
        document.getElementById('header_fields_json').value = JSON.stringify(headerFields);

        const configs = {};
        for (let i = 1; i <= totalPages; i++) {
            const enableHeader = document.getElementById(`header_enable_${i}`).checked;
            const dragBox = document.getElementById(`drag-box-${i}`);
            const replacementsRaw = document.getElementById(`replacements_${i}`).value;

            const posX = parseFloat(dragBox.style.left || 220) / scale;
            const posY = parseFloat(dragBox.style.top || 15) / scale;

            const targets = replacementsRaw.split(',').map(s => s.trim()).filter(Boolean);

            configs[i] = {
                enable_header: enableHeader,
                pos_x: posX,
                pos_y: posY,
                targets: targets
            };
        }

        document.getElementById('page_configs_json').value = JSON.stringify(configs);
        document.getElementById('editor-form').submit();
    }
</script>
{% endif %}
</body>
</html>
"""

STUDENT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Personalize Your Document</title>
    <script>
        function handleStudentInput(element, pageNum, targetIdx) {
            const val = element.value;
            if (pageNum === 1 && targetIdx === 0) {
                const allFirstInputs = document.querySelectorAll('.target-input-idx-0');
                allFirstInputs.forEach(input => {
                    input.value = val;
                });
            }
        }
    </script>
    <style>
        body { font-family: Arial, sans-serif; max-width: 550px; margin: 20px auto; padding: 15px; color: #333; box-sizing: border-box; }
        .card { border: 1px solid #ccc; padding: 20px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); background: #fff; box-sizing: border-box; }
        input[type="text"] { width: 100%; padding: 10px; margin: 6px 0 15px 0; box-sizing: border-box; border: 1px solid #ccc; border-radius: 4px; }
        button { background-color: #28a745; color: white; padding: 12px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; width: 100%; font-size: 16px; }
        button:hover { background-color: #218838; }
        .header-box { background-color: #eef6ff; padding: 15px; border-radius: 6px; border: 1px solid #b6d4fe; margin-bottom: 20px; }
        .page-section { background-color: #f8f9fa; border: 1px solid #dee2e6; border-radius: 6px; padding: 15px; margin-bottom: 15px; }
        .field-group { margin-bottom: 10px; }
    </style>
</head>
<body>
    <div class="card">
        <h2>📝 Personalize Your PDF</h2>
        <form action="/doc/{{ doc_id }}/process" method="post">
            
            {% if has_header and header_fields %}
            <div class="header-box">
                <h3>📌 Student Details</h3>
                {% for field in header_fields %}
                <div class="field-group">
                    <label><b>{{ field }}:</b></label>
                    <input type="text" name="header_field_{{ loop.index0 }}" placeholder="Enter {{ field | lower }}" required>
                </div>
                {% endfor %}
            </div>
            {% endif %}

            {% for page_num, targets in page_targets.items() %}
                {% if targets %}
                <div class="page-section">
                    <h4>✏️ Text Replacements for Page {{ page_num }}</h4>
                    {% for target in targets %}
                    <div class="field-group">
                        <label>Replace <b>"{{ target }}"</b> with:</label>
                        <input type="text" name="rep_{{ page_num }}_{{ loop.index0 }}" placeholder="Enter new text" 
                               class="{% if loop.index0 == 0 %}target-input-idx-0{% endif %}"
                               oninput="handleStudentInput(this, {{ page_num }}, {{ loop.index0 }})" required>
                    </div>
                    {% endfor %}
                </div>
                {% endif %}
            {% endfor %}
            
            <button type="submit">Generate My PDF</button>
        </form>
    </div>
</body>
</html>
"""

SUCCESS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Your PDF is Ready!</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 550px; margin: 30px auto; padding: 15px; color: #333; text-align: center; box-sizing: border-box; }
        .card { border: 1px solid #ccc; padding: 25px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.1); background: #fff; box-sizing: border-box; }
        .success-icon { font-size: 50px; margin-bottom: 10px; }
        .btn { display: block; width: 100%; padding: 12px; border: none; border-radius: 5px; font-weight: bold; font-size: 16px; text-decoration: none; box-sizing: border-box; margin-top: 15px; cursor: pointer; text-align: center; }
        .btn-download { background-color: #28a745; color: white; }
        .btn-download:hover { background-color: #218838; }
        .whatsapp-btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
            background-color: #25d366;
            color: white;
            box-shadow: 0 4px 10px rgba(37, 211, 102, 0.3);
            transition: background-color 0.2s;
        }
        .whatsapp-btn:hover {
            background-color: #1ebe5d;
        }
    </style>
</head>
<body>
    <div class="card">
        <div class="success-icon">🎉</div>
        <h2>Your Personalized PDF is Ready!</h2>
        <p style="color: #666; font-size: 0.95em;">You can download your customized file below or share this exact custom PDF with your friends via WhatsApp!</p>

        <a href="/doc/{{ doc_id }}/download/{{ file_token }}" class="btn btn-download">📥 Download Personalized PDF</a>

        <a href="https://api.whatsapp.com/send?text=Hey!%20Check%20out%20my%20personalized%20practical%20file%20generated%20via%20Dropy:%20{{ download_url }}" target="_blank" class="btn whatsapp-btn">
            <svg width="20" height="20" fill="currentColor" viewBox="0 0 24 24"><path d="M.057 24l1.687-6.163c-1.041-1.804-1.588-3.849-1.587-5.946.003-6.556 5.338-11.891 11.893-11.891 3.181.001 6.167 1.24 8.413 3.488 2.245 2.248 3.481 5.236 3.48 8.414-.003 6.557-5.338 11.892-11.893 11.892-1.99-.001-3.951-.5-5.688-1.448l-6.305 1.654zm6.597-3.807c1.676.995 3.276 1.591 5.392 1.592 5.448 0 9.886-4.434 9.889-9.885.002-5.462-4.415-9.89-9.881-9.892-5.452 0-9.887 4.434-9.889 9.884-.001 2.225.651 3.891 1.746 5.634l-.999 3.648 3.742-.981zm11.387-5.464c-.074-.124-.272-.198-.57-.347-.297-.149-1.758-.868-2.031-.967-.272-.099-.47-.149-.669.149-.198.297-.768.967-.941 1.165-.173.198-.347.223-.644.074-.297-.149-1.255-.462-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.297-.347.446-.521.151-.172.2-.296.3-.495.099-.198.05-.372-.025-.521-.075-.148-.669-1.611-.916-2.206-.242-.579-.487-.501-.669-.51l-.57-.01c-.198 0-.52.074-.792.372s-1.04 1.016-1.04 2.479 1.065 2.876 1.213 3.074c.149.198 2.095 3.2 5.076 4.487.709.306 1.263.489 1.694.626.712.226 1.36.194 1.872.118.571-.085 1.758-.719 2.006-1.413.248-.695.248-1.29.173-1.414z"/></svg>
            Share PDF via WhatsApp
        </a>
    </div>
</body>
</html>
"""

def clean_text(text):
    return re.sub(r'[^\x20-\x7E\n\x0c]', '', text)

@app.route("/", methods=["GET"])
def home():
    return render_template_string(CREATOR_HTML)

@app.route("/upload", methods=["POST"])
def upload():
    file = request.files["file"]
    file_bytes = file.read()
    
    reader = PdfReader(io.BytesIO(file_bytes))
    pages_text = [clean_text(page.extract_text() or "") for page in reader.pages]

    doc_id = str(uuid.uuid4())[:8]
    TEMPLATES[doc_id] = {
        "pdf_bytes": file_bytes,
        "pages_text": pages_text,
        "total_pages": len(pages_text),
        "generated_files": {}
    }
    
    b64_pdf = base64.b64encode(file_bytes).decode('utf-8')
    return render_template_string(CREATOR_HTML, pdf_b64=b64_pdf, doc_id=doc_id, total_pages=len(pages_text))

@app.route("/create", methods=["POST"])
def create_link():
    doc_id = request.form["doc_id"]
    configs_json = request.form.get("page_configs_json", "{}")
    fields_json = request.form.get("header_fields_json", "[]")
    
    try:
        page_configs = json.loads(configs_json)
        header_fields = json.loads(fields_json)
    except Exception:
        page_configs = {}
        header_fields = ["NAME", "ROLL NO"]
    
    if doc_id in TEMPLATES:
        TEMPLATES[doc_id]["page_configs"] = page_configs
        TEMPLATES[doc_id]["header_fields"] = header_fields
        share_url = f"{request.host_url}doc/{doc_id}"
        return render_template_string(CREATOR_HTML, share_url=share_url)
    
    return "Session expired. Please re-upload.", 400

@app.route("/doc/<doc_id>", methods=["GET"])
def student_view(doc_id):
    if doc_id not in TEMPLATES:
        return "Document link not found or expired!", 404
    
    template_data = TEMPLATES[doc_id]
    page_configs = template_data.get("page_configs", {})
    header_fields = template_data.get("header_fields", ["NAME", "ROLL NO"])
    
    has_header = any(cfg.get("enable_header", False) for cfg in page_configs.values())
    
    page_targets = {}
    for page_num, cfg in page_configs.items():
        targets = cfg.get("targets", [])
        if targets:
            page_targets[page_num] = targets

    return render_template_string(
        STUDENT_HTML, 
        doc_id=doc_id, 
        has_header=has_header,
        header_fields=header_fields,
        page_targets=page_targets
    )

class NumberedCanvas(pdf_canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations()
            super().showPage()
        super().save()

    def draw_page_decorations(self):
        page_num_str = str(self._pageNumber)
        cfg = self.page_configs.get(page_num_str, {})
        
        if cfg.get("enable_header", False):
            pos_x = cfg.get("pos_x", 220)
            pos_y = cfg.get("pos_y", 15)
            
            pdf_x = max(30, min(pos_x, 400))
            fields = self.header_fields
            field_vals = self.header_field_vals
            
            box_height = 16 + (len(fields) * 14)
            pdf_y = max(30, 792 - pos_y - box_height)

            self.rect(pdf_x, pdf_y, 190, box_height, stroke=1, fill=0)
            self.setFont("Helvetica-Bold", 8.5)
            
            current_y = pdf_y + box_height - 12
            for i, field in enumerate(fields):
                val = field_vals.get(i, "").upper()
                self.drawString(pdf_x + 8, current_y, f"{field.upper()}: {val}")
                current_y -= 13

def build_personalized_pdf(template_data):
    page_configs = template_data.get("page_configs", {})
    header_fields = template_data.get("header_fields", ["NAME", "ROLL NO"])
    header_field_vals = template_data.get("header_field_vals", {})
    pages_text = template_data["pages_text"].copy()

    for page_num_str, cfg in page_configs.items():
        page_idx = int(page_num_str) - 1
        if 0 <= page_idx < len(pages_text):
            targets = cfg.get("targets", [])
            for t_idx, target in enumerate(targets):
                replacement_value = template_data.get("replacements", {}).get(f"{page_num_str}_{t_idx}", "")
                if target and replacement_value:
                    pages_text[page_idx] = re.sub(re.escape(target), replacement_value, pages_text[page_idx], flags=re.IGNORECASE)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=50, bottomMargin=36)
    story = []

    code_style = ParagraphStyle(
        'CodeStyle',
        fontName='Courier',
        fontSize=10,
        leading=13,
        textColor=colors.black,
        spaceAfter=0
    )

    is_multi_page = len(pages_text) > 1

    for p_idx, page_text in enumerate(pages_text):
        lines = page_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').split('\n')
        for line in lines:
            formatted_line = line.replace(' ', '&nbsp;') if line.strip() else '&nbsp;'
            story.append(Paragraph(formatted_line, code_style))

        if is_multi_page and p_idx < len(pages_text) - 1:
            story.append(PageBreak())

    NumberedCanvas.page_configs = page_configs
    NumberedCanvas.header_fields = header_fields
    NumberedCanvas.header_field_vals = header_field_vals

    doc.build(story, canvasmaker=NumberedCanvas)
    buffer.seek(0)
    return buffer.read()

@app.route("/doc/<doc_id>/process", methods=["POST"])
def process_student_form(doc_id):
    if doc_id not in TEMPLATES:
        return "Document not found!", 404
        
    template_data = TEMPLATES[doc_id]
    header_fields = template_data.get("header_fields", ["NAME", "ROLL NO"])
    
    header_field_vals = {}
    for i, field in enumerate(header_fields):
        header_field_vals[i] = request.form.get(f"header_field_{i}", "")
        
    replacements = {}
    page_configs = template_data.get("page_configs", {})
    for page_num_str, cfg in page_configs.items():
        targets = cfg.get("targets", [])
        for t_idx, target in enumerate(targets):
            replacements[f"{page_num_str}_{t_idx}"] = request.form.get(f"rep_{page_num_str}_{t_idx}", "")

    file_token = str(uuid.uuid4())[:8]
    
    temp_storage = template_data.copy()
    temp_storage["header_field_vals"] = header_field_vals
    temp_storage["replacements"] = replacements
    
    pdf_bytes = build_personalized_pdf(temp_storage)
    
    if "generated_files" not in TEMPLATES[doc_id]:
        TEMPLATES[doc_id]["generated_files"] = {}
    TEMPLATES[doc_id]["generated_files"][file_token] = pdf_bytes

    download_url = f"{request.host_url}doc/{doc_id}/download/{file_token}"
    return render_template_string(
        SUCCESS_HTML,
        doc_id=doc_id,
        file_token=file_token,
        download_url=download_url
    )

@app.route("/doc/<doc_id>/download/<file_token>", methods=["GET"])
def download_personalized_file(doc_id, file_token):
    if doc_id not in TEMPLATES or file_token not in TEMPLATES[doc_id].get("generated_files", {}):
        return "File not found or expired!", 404
    
    pdf_bytes = TEMPLATES[doc_id]["generated_files"][file_token]
    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name='personalized_practical.pdf'
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)