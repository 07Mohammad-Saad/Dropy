from datetime import datetime, timedelta
import os
from flask import Flask, flash, redirect, render_template, request, url_for
import firebase_admin
from firebase_admin import credentials, firestore, storage
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "your_secret_key_here"  # Change this to a secure random key

# Initialize Firebase Admin SDK using your dropy.json key file
cred = credentials.Certificate("dropy.json")
firebase_admin.initialize_app(
    cred, {"storageBucket": "dropy-xyz.appspot.com"}  # Replace with your actual Firebase Storage bucket name if different
)

db = firestore.client()
bucket = storage.bucket()

# Set expiration days (e.g., 5 days)
EXPIRATION_DAYS = 5


@app.route("/")
def index():
  return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload_file():
  if "pdf_file" not in request.files:
    flash("No file part")
    return redirect(request.url)

  file = request.files["pdf_file"]
  if file.filename == "":
    flash("No selected file")
    return redirect(url_for("index"))

  if file and file.filename.endswith(".pdf"):
    filename = secure_filename(file.filename)
    unique_filename = (
        f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{filename}"
    )

    # 1. Upload file to Firebase Storage
    blob = bucket.blob(f"pdfs/{unique_filename}")
    blob.upload_from_file(file, content_type="application/pdf")

    blob.make_public()
    file_url = blob.public_url

    # 2. Save metadata and expiration date to Firestore
    expires_at = datetime.utcnow() + timedelta(days=EXPIRATION_DAYS)

    doc_ref = db.collection("files").document(unique_filename)
    doc_ref.set({
        "filename": filename,
        "url": file_url,
        "created_at": datetime.utcnow(),
        "expires_at": expires_at,
    })

    # 3. Create a pre-filled WhatsApp message string
    whatsapp_message = (
        f"Hey! Check out my generated PDF file via Dropy: {file_url}"
    )

    flash(f"File uploaded successfully! It will expire in {EXPIRATION_DAYS} days.")
    return render_template(
        "success.html", file_url=file_url, whatsapp_message=whatsapp_message
    )

  flash("Only PDF files are allowed.")
  return redirect(url_for("index"))


if __name__ == "__main__":
  app.run(debug=True)