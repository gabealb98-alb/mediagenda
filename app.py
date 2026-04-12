from flask import Flask, request, jsonify
from supabase import create_client
import os
import re
import unicodedata
from datetime import datetime, timedelta

# ============================================================
# CONFIG
# ============================================================

app = Flask(__name__)

SUPABASE_URL = os.getenv("https://cpohqxktwuuyvzoqtqsm.supabase.co")
SUPABASE_KEY = os.getenv("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNwb2hxeGt0d3V1eXZ6b3F0cXNtIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTYxMjA4OCwiZXhwIjoyMDkxMTg4MDg4fQ.eu3YVCCAejSYAvKw77NEa9iJcSHki4eIkEXs07_8t94")

supabase = create_client(https://cpohqxktwuuyvzoqtqsm.supabase.co, eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNwb2hxeGt0d3V1eXZ6b3F0cXNtIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NTYxMjA4OCwiZXhwIjoyMDkxMTg4MDg4fQ.eu3YVCCAejSYAvKw77NEa9iJcSHki4eIkEXs07_8t94)

# ============================================================
# FUNÇÕES AUXILIARES
# ============================================================

def slugify_text(value: str):
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", str(value))
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9\s-]", "", value)
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-")

def get_clinic_by_slug(slug):
    incoming_slug = slugify_text(slug)

    res = supabase.table("clinics").select("*").eq("active", True).execute()
    clinics = res.data or []

    for c in clinics:
        if slugify_text(c.get("slug")) == incoming_slug:
            return c

    return None

def generate_slots(start_time, end_time, minutes):
    slots = []
    start = datetime.strptime(start_time[:5], "%H:%M")
    end = datetime.strptime(end_time[:5], "%H:%M")

    current = start
    while current + timedelta(minutes=minutes) <= end:
        slots.append(current.strftime("%H:%M"))
        current += timedelta(minutes=minutes)

    return slots

# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():
    return {"status": "ok", "version": "2.0"}

# ============================================================
# PUBLIC - CLINIC
# ============================================================

@app.route("/public/<path:slug>", methods=["GET"])
def get_clinic_public(slug):
    clinic = get_clinic_by_slug(slug)

    if not clinic:
        return jsonify({"error": "Clínica não encontrada"}), 404

    profs = supabase.table("professionals") \
        .select("*") \
        .eq("clinic_id", clinic["id"]) \
        .eq("active", True) \
        .execute()

    types = supabase.table("appointment_types") \
        .select("*") \
        .eq("clinic_id", clinic["id"]) \
        .eq("active", True) \
        .execute()

    clinic["professionals"] = profs.data or []
    clinic["appointment_types"] = types.data or []

    return jsonify(clinic)

# ============================================================
# AVAILABLE SLOTS
# ============================================================

@app.route("/public/<path:slug>/available-slots", methods=["GET"])
def available_slots(slug):
    clinic = get_clinic_by_slug(slug)

    if not clinic:
        return jsonify({"error": "Clínica não encontrada"}), 404

    date = request.args.get("date")

    if not date:
        return jsonify({"error": "Data obrigatória"}), 400

    date_obj = datetime.strptime(date, "%Y-%m-%d")
    weekday = (date_obj.weekday() + 1) % 7

    schedules = supabase.table("schedules") \
        .select("*") \
        .eq("clinic_id", clinic["id"]) \
        .eq("weekday", weekday) \
        .eq("active", True) \
        .execute()

    if not schedules.data:
        return jsonify({"slots": []})

    sch = schedules.data[0]

    slots = generate_slots(
        sch["start_time"],
        sch["end_time"],
        sch.get("slot_minutes", 30)
    )

    appointments = supabase.table("appointments") \
        .select("time,status") \
        .eq("clinic_id", clinic["id"]) \
        .eq("date", date) \
        .execute()

    taken = set()
    for a in appointments.data or []:
        if a["status"] != "cancelado":
            taken.add(a["time"][:5])

    free = [s for s in slots if s not in taken]

    return jsonify({"slots": free})

# ============================================================
# BOOK APPOINTMENT
# ============================================================

@app.route("/public/<path:slug>/book", methods=["POST"])
def book(slug):
    clinic = get_clinic_by_slug(slug)

    if not clinic:
        return jsonify({"error": "Clínica não encontrada"}), 404

    data = request.json

    name = data.get("patient_name")
    phone = data.get("patient_phone")
    date = data.get("date")
    time = data.get("time")

    if not name or not phone or not date or not time:
        return jsonify({"error": "Dados obrigatórios"}), 400

    conflict = supabase.table("appointments") \
        .select("*") \
        .eq("clinic_id", clinic["id"]) \
        .eq("date", date) \
        .eq("time", time) \
        .execute()

    if conflict.data:
        return jsonify({"error": "Horário ocupado"}), 409

    created = supabase.table("appointments").insert({
        "clinic_id": clinic["id"],
        "patient_name": name,
        "patient_phone": phone,
        "date": date,
        "time": time,
        "status": "pendente"
    }).execute()

    return jsonify({
        "success": True,
        "appointment": created.data[0]
    })

# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    app.run(debug=True)