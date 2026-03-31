from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from text_extractor import organize_with_llm   # no longer imports extract()
from schedule_planner import generate_schedule
import json
import os

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///userdata.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

UPLOAD_FOLDER = "uploads"

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)


# -----------------------
# DATABASE
# -----------------------

class StudyData(db.Model):

    userid = db.Column(db.Integer, primary_key=True)

    # JSON from text_extractor → { Exam_dates, Subjects, study_days }
    extracted_json = db.Column(db.Text)

    # JSON from Status.html  → same shape with topic % values filled in
    topic_status = db.Column(db.Text)

    # JSON from schedule_planner → { DD-MM-YYYY: { Subject: { Topic: hours } } }
    schedule_json = db.Column(db.Text)


# -----------------------
# PAGE ROUTES
# -----------------------

@app.route("/")
def upload_page():
    return render_template("Upload-page.html")


@app.route("/status")
def status_page():

    userid = 1
    user = StudyData.query.filter_by(userid=userid).first()

    if not user:
        return "Upload files first", 404

    data       = json.loads(user.extracted_json)
    exam_dates = data.get("Exam_dates", {})

    return render_template(
        "Status.html",
        data=data,
        exam_dates=exam_dates
    )


@app.route("/schedule_page")
def schedule_page():
    return render_template("Schedule.html")


# -----------------------
# FILE UPLOAD
# -----------------------

@app.route("/upload", methods=["POST"])
def upload_files():
    try:

        userid = int(request.form.get("userid") or 1)

        files = request.files.getlist("files")
        if not files:
            return jsonify({"error": "No files uploaded"}), 400

        # Save all files and collect their paths.
        # Upload-page.js sends syllabus file(s) first, datesheet last.
        file_paths = []
        for file in files:
            if not file.filename:
                continue
            path = os.path.join(UPLOAD_FOLDER, file.filename)
            file.save(path)
            file_paths.append(path)

        if not file_paths:
            return jsonify({"error": "No valid files received"}), 400

        # Pass paths directly — organize_with_llm handles all file types
        # (images as base64 to vision model, PDFs/DOCX as extracted text)
        final_json = organize_with_llm(file_paths)

        existing = StudyData.query.filter_by(userid=userid).first()
        if existing:
            existing.extracted_json = json.dumps(final_json)
        else:
            db.session.add(StudyData(
                userid=userid,
                extracted_json=json.dumps(final_json)
            ))

        db.session.commit()
        return jsonify(final_json)

    except Exception as e:
        print("UPLOAD ERROR:", e)
        return jsonify({"error": str(e)}), 500


# -----------------------
# SAVE STATUS
# -----------------------

@app.route("/submit_status/<int:userid>", methods=["POST"])
def submit_status(userid):
    """
    Receives the completed Status.html form:
    {
      "Exam_dates": { ... },
      "Subjects":   { "Subject": { "Topic": "0"–"100" } },
      "study_days": { "DD-MM-YYYY": "hours_string" }
    }
    """
    user = StudyData.query.filter_by(userid=userid).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    user.topic_status = json.dumps(request.json)
    db.session.commit()
    return jsonify({"message": "saved"})


# -----------------------
# GENERATE SCHEDULE
# -----------------------

@app.route("/generate_schedule/<int:userid>", methods=["POST"])
def generate(userid):

    user = StudyData.query.filter_by(userid=userid).first()
    if not user:
        return jsonify({"error": "User not found"}), 404

    if not user.topic_status:
        return jsonify({"error": "Topic status not submitted yet"}), 400

    topic_data = json.loads(user.topic_status)
    schedule   = generate_schedule(topic_data)

    user.schedule_json = json.dumps(schedule)
    db.session.commit()
    return jsonify(schedule)


# -----------------------
# GET FINAL SCHEDULE
# -----------------------

@app.route("/schedule/<int:userid>")
def schedule(userid):

    user = StudyData.query.filter_by(userid=userid).first()
    if not user or not user.schedule_json:
        return jsonify({"error": "Schedule not found"}), 404

    return jsonify(json.loads(user.schedule_json))


# -----------------------

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)