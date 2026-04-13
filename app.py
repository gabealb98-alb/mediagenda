"""
MediAgenda v2 — Backend Flask completo
Inclui: profissionais, tipos, pacientes, prontuário, prescrições, WhatsApp Z-API
"""

import os, requests, resend
from flask import Flask, request, jsonify, abort
from flask_cors import CORS
from supabase import create_client, Client
from datetime import datetime, date, timedelta
from functools import wraps
import jwt, bcrypt

app = Flask(__name__)
CORS(app)

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
JWT_SECRET   = os.environ["JWT_SECRET"]
resend.api_key = os.environ.get("RESEND_API_KEY", "")
EMAIL_FROM   = os.environ.get("EMAIL_FROM", "noreply@mediagenda.com.br")
ZAPI_BASE    = "https://api.z-api.io/instances"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ============================================================
# AUTH
# ============================================================
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            return jsonify({"error": "Token necessário"}), 401
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            request.master_id = payload["master_id"]
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expirado"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Token inválido"}), 401
        return f(*args, **kwargs)
    return decorated

def _assert_clinic(clinic_id):
    c = supabase.table("clinics").select("id").eq("id", clinic_id).eq("master_id", request.master_id).execute()
    if not c.data:
        abort(404, "Clínica não encontrada")
    return c.data[0]

# ============================================================
# AUTH ROUTES
# ============================================================
@app.route("/auth/register", methods=["POST"])
def register():
    data = request.json
    if not all(k in data for k in ["name","email","password"]):
        return jsonify({"error": "name, email e password obrigatórios"}), 400
    if supabase.table("masters").select("id").eq("email", data["email"]).execute().data:
        return jsonify({"error": "E-mail já cadastrado"}), 409
    pw = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()
    m = supabase.table("masters").insert({"name": data["name"], "email": data["email"], "password_hash": pw}).execute().data[0]
    token = jwt.encode({"master_id": m["id"], "exp": datetime.utcnow() + timedelta(days=30)}, JWT_SECRET, algorithm="HS256")
    return jsonify({"token": token, "master": {"id": m["id"], "name": m["name"]}})

@app.route("/auth/login", methods=["POST"])
def login():
    data = request.json
    masters = supabase.table("masters").select("*").eq("email", data.get("email","")).execute().data
    if not masters:
        return jsonify({"error": "Credenciais inválidas"}), 401
    m = masters[0]
    if not bcrypt.checkpw(data["password"].encode(), m["password_hash"].encode()):
        return jsonify({"error": "Credenciais inválidas"}), 401
    token = jwt.encode({"master_id": m["id"], "exp": datetime.utcnow() + timedelta(days=30)}, JWT_SECRET, algorithm="HS256")
    return jsonify({"token": token, "master": {"id": m["id"], "name": m["name"]}})

# ============================================================
# CLINICS
# ============================================================
@app.route("/clinics", methods=["GET"])
@token_required
def list_clinics():
    return jsonify(supabase.table("clinics").select("*").eq("master_id", request.master_id).order("name").execute().data)

@app.route("/clinics", methods=["POST"])
@token_required
def create_clinic():
    data = request.json
    if not all(k in data for k in ["name","slug","email"]):
        return jsonify({"error": "name, slug e email obrigatórios"}), 400
    if supabase.table("clinics").select("id").eq("slug", data["slug"]).execute().data:
        return jsonify({"error": "Slug já em uso"}), 409
    c = supabase.table("clinics").insert({
        "master_id": request.master_id, "name": data["name"], "slug": data["slug"],
        "email": data["email"], "phone": data.get("phone"), "address": data.get("address"),
        "specialty": data.get("specialty"), "logo_url": data.get("logo_url"),
        "whatsapp_number": data.get("whatsapp_number"), "zapi_instance": data.get("zapi_instance"),
        "zapi_token": data.get("zapi_token"), "description": data.get("description"), "active": True
    }).execute().data[0]
    return jsonify(c), 201

@app.route("/clinics/<clinic_id>", methods=["PUT"])
@token_required
def update_clinic(clinic_id):
    _assert_clinic(clinic_id)
    allowed = ["name","email","phone","address","specialty","logo_url","active",
               "whatsapp_number","zapi_instance","zapi_token","description"]
    data = {k: v for k, v in request.json.items() if k in allowed}
    return jsonify(supabase.table("clinics").update(data).eq("id", clinic_id).execute().data[0])

@app.route("/clinics/<clinic_id>", methods=["DELETE"])
@token_required
def delete_clinic(clinic_id):
    _assert_clinic(clinic_id)
    supabase.table("clinics").delete().eq("id", clinic_id).execute()
    return jsonify({"success": True})

# ============================================================
# PROFESSIONALS
# ============================================================
@app.route("/clinics/<clinic_id>/professionals", methods=["GET"])
@token_required
def list_professionals(clinic_id):
    _assert_clinic(clinic_id)
    return jsonify(supabase.table("professionals").select("*").eq("clinic_id", clinic_id).order("name").execute().data)

@app.route("/clinics/<clinic_id>/professionals", methods=["POST"])
@token_required
def create_professional(clinic_id):
    _assert_clinic(clinic_id)
    data = request.json
    p = supabase.table("professionals").insert({
        "clinic_id": clinic_id, "name": data["name"],
        "specialty": data.get("specialty"), "photo_url": data.get("photo_url"),
        "bio": data.get("bio"), "active": True
    }).execute().data[0]
    return jsonify(p), 201

@app.route("/clinics/<clinic_id>/professionals/<prof_id>", methods=["PUT"])
@token_required
def update_professional(clinic_id, prof_id):
    _assert_clinic(clinic_id)
    allowed = ["name","specialty","photo_url","bio","active"]
    data = {k: v for k, v in request.json.items() if k in allowed}
    return jsonify(supabase.table("professionals").update(data).eq("id", prof_id).execute().data[0])

@app.route("/clinics/<clinic_id>/professionals/<prof_id>", methods=["DELETE"])
@token_required
def delete_professional(clinic_id, prof_id):
    _assert_clinic(clinic_id)
    supabase.table("professionals").delete().eq("id", prof_id).execute()
    return jsonify({"success": True})

# ============================================================
# APPOINTMENT TYPES
# ============================================================
@app.route("/clinics/<clinic_id>/appointment-types", methods=["GET"])
@token_required
def list_appt_types(clinic_id):
    _assert_clinic(clinic_id)
    return jsonify(supabase.table("appointment_types").select("*").eq("clinic_id", clinic_id).execute().data)

@app.route("/clinics/<clinic_id>/appointment-types", methods=["POST"])
@token_required
def create_appt_type(clinic_id):
    _assert_clinic(clinic_id)
    data = request.json
    t = supabase.table("appointment_types").insert({
        "clinic_id": clinic_id, "name": data["name"],
        "duration_min": data.get("duration_min", 30),
        "color": data.get("color", "#1D9E75"), "active": True
    }).execute().data[0]
    return jsonify(t), 201

@app.route("/clinics/<clinic_id>/appointment-types/<type_id>", methods=["PUT"])
@token_required
def update_appt_type(clinic_id, type_id):
    _assert_clinic(clinic_id)
    allowed = ["name","duration_min","color","active"]
    data = {k: v for k, v in request.json.items() if k in allowed}
    return jsonify(supabase.table("appointment_types").update(data).eq("id", type_id).execute().data[0])

@app.route("/clinics/<clinic_id>/appointment-types/<type_id>", methods=["DELETE"])
@token_required
def delete_appt_type(clinic_id, type_id):
    _assert_clinic(clinic_id)
    supabase.table("appointment_types").delete().eq("id", type_id).execute()
    return jsonify({"success": True})

# ============================================================
# SCHEDULES
# ============================================================
@app.route("/clinics/<clinic_id>/schedules", methods=["GET"])
@token_required
def list_schedules(clinic_id):
    _assert_clinic(clinic_id)
    return jsonify(supabase.table("schedules").select("*").eq("clinic_id", clinic_id).order("weekday").execute().data)

@app.route("/clinics/<clinic_id>/schedules", methods=["POST"])
@token_required
def create_schedule(clinic_id):
    _assert_clinic(clinic_id)
    data = request.json
    supabase.table("schedules").delete().eq("clinic_id", clinic_id).eq("weekday", data["weekday"]).execute()
    s = supabase.table("schedules").insert({
        "clinic_id": clinic_id, "weekday": data["weekday"],
        "start_time": data["start_time"], "end_time": data["end_time"],
        "slot_minutes": data.get("slot_minutes", 30), "active": True
    }).execute().data[0]
    return jsonify(s), 201

@app.route("/clinics/<clinic_id>/schedules/<sid>", methods=["DELETE"])
@token_required
def delete_schedule(clinic_id, sid):
    _assert_clinic(clinic_id)
    supabase.table("schedules").delete().eq("id", sid).execute()
    return jsonify({"success": True})

# ============================================================
# BLOCKED TIMES
# ============================================================
@app.route("/clinics/<clinic_id>/blocked", methods=["GET"])
@token_required
def list_blocked(clinic_id):
    _assert_clinic(clinic_id)
    return jsonify(supabase.table("blocked_times").select("*").eq("clinic_id", clinic_id).order("date").execute().data)

@app.route("/clinics/<clinic_id>/blocked", methods=["POST"])
@token_required
def create_blocked(clinic_id):
    _assert_clinic(clinic_id)
    data = request.json
    b = supabase.table("blocked_times").insert({
        "clinic_id": clinic_id, "date": data["date"],
        "start_time": data.get("start_time"), "end_time": data.get("end_time"),
        "reason": data.get("reason")
    }).execute().data[0]
    return jsonify(b), 201

@app.route("/clinics/<clinic_id>/blocked/<bid>", methods=["DELETE"])
@token_required
def delete_blocked(clinic_id, bid):
    _assert_clinic(clinic_id)
    supabase.table("blocked_times").delete().eq("id", bid).execute()
    return jsonify({"success": True})

# ============================================================
# PATIENTS
# ============================================================
@app.route("/clinics/<clinic_id>/patients", methods=["GET"])
@token_required
def list_patients(clinic_id):
    _assert_clinic(clinic_id)
    q = supabase.table("patients").select("*").eq("clinic_id", clinic_id)
    search = request.args.get("search")
    if search:
        q = q.ilike("name", f"%{search}%")
    return jsonify(q.order("name").execute().data)

@app.route("/clinics/<clinic_id>/patients/<patient_id>", methods=["GET"])
@token_required
def get_patient(clinic_id, patient_id):
    _assert_clinic(clinic_id)
    p = supabase.table("patients").select("*").eq("id", patient_id).eq("clinic_id", clinic_id).execute()
    if not p.data:
        return jsonify({"error": "Paciente não encontrado"}), 404
    return jsonify(p.data[0])

@app.route("/clinics/<clinic_id>/patients/<patient_id>", methods=["PUT"])
@token_required
def update_patient(clinic_id, patient_id):
    _assert_clinic(clinic_id)
    allowed = ["name","phone","email","cpf","birth_date","gender","address","notes"]
    data = {k: v for k, v in request.json.items() if k in allowed}
    return jsonify(supabase.table("patients").update(data).eq("id", patient_id).execute().data[0])

# ============================================================
# MEDICAL RECORDS (Prontuário)
# ============================================================
@app.route("/clinics/<clinic_id>/patients/<patient_id>/records", methods=["GET"])
@token_required
def list_records(clinic_id, patient_id):
    _assert_clinic(clinic_id)
    records = supabase.table("medical_records").select(
        "*, prescriptions(*), professionals(name,specialty)"
    ).eq("clinic_id", clinic_id).eq("patient_id", patient_id).order("date", desc=True).execute()
    return jsonify(records.data)

@app.route("/clinics/<clinic_id>/patients/<patient_id>/records", methods=["POST"])
@token_required
def create_record(clinic_id, patient_id):
    _assert_clinic(clinic_id)
    data = request.json
    rec = supabase.table("medical_records").insert({
        "clinic_id":       clinic_id,
        "patient_id":      patient_id,
        "appointment_id":  data.get("appointment_id"),
        "professional_id": data.get("professional_id"),
        "date":            data.get("date", str(date.today())),
        "complaint":       data.get("complaint"),
        "diagnosis":       data.get("diagnosis"),
        "treatment":       data.get("treatment"),
        "observations":    data.get("observations"),
        "weight":          data.get("weight"),
        "height":          data.get("height"),
        "blood_pressure":  data.get("blood_pressure"),
        "temperature":     data.get("temperature"),
    }).execute().data[0]

    # Salva prescrições
    prescriptions = data.get("prescriptions", [])
    if prescriptions:
        for p in prescriptions:
            p["medical_record_id"] = rec["id"]
        supabase.table("prescriptions").insert(prescriptions).execute()

    return jsonify(rec), 201

@app.route("/clinics/<clinic_id>/records/<record_id>", methods=["PUT"])
@token_required
def update_record(clinic_id, record_id):
    _assert_clinic(clinic_id)
    data = request.json
    allowed = ["complaint","diagnosis","treatment","observations","weight","height","blood_pressure","temperature","date"]
    rec_data = {k: v for k, v in data.items() if k in allowed}
    rec_data["updated_at"] = datetime.utcnow().isoformat()
    rec = supabase.table("medical_records").update(rec_data).eq("id", record_id).execute().data[0]

    # Atualiza prescrições: apaga as antigas e insere as novas
    if "prescriptions" in data:
        supabase.table("prescriptions").delete().eq("medical_record_id", record_id).execute()
        if data["prescriptions"]:
            for p in data["prescriptions"]:
                p["medical_record_id"] = record_id
            supabase.table("prescriptions").insert(data["prescriptions"]).execute()

    return jsonify(rec)

@app.route("/clinics/<clinic_id>/records/<record_id>", methods=["DELETE"])
@token_required
def delete_record(clinic_id, record_id):
    _assert_clinic(clinic_id)
    supabase.table("medical_records").delete().eq("id", record_id).execute()
    return jsonify({"success": True})

# ============================================================
# APPOINTMENTS
# ============================================================
@app.route("/clinics/<clinic_id>/appointments", methods=["GET"])
@token_required
def list_appointments(clinic_id):
    _assert_clinic(clinic_id)
    q = supabase.table("appointments").select(
        "*, professionals(name,specialty), appointment_types(name,color), patients(name,phone)"
    ).eq("clinic_id", clinic_id)
    if request.args.get("date"):   q = q.eq("date", request.args["date"])
    if request.args.get("status"): q = q.eq("status", request.args["status"])
    if request.args.get("start"):  q = q.gte("date", request.args["start"])
    if request.args.get("end"):    q = q.lte("date", request.args["end"])
    return jsonify(q.order("date").order("time").execute().data)

@app.route("/clinics/<clinic_id>/appointments/<aid>/status", methods=["PATCH"])
@token_required
def update_appt_status(clinic_id, aid):
    _assert_clinic(clinic_id)
    status = request.json.get("status")
    if status not in ["pendente","confirmado","cancelado","concluido"]:
        return jsonify({"error": "Status inválido"}), 400
    return jsonify(supabase.table("appointments").update({"status": status}).eq("id", aid).execute().data[0])

# ============================================================
# PUBLIC — Agendamento pelo paciente
# ============================================================
@app.route("/public/<slug>", methods=["GET"])
def get_clinic_public(slug):
    c = supabase.table("clinics").select(
        "id,name,slug,phone,email,logo_url,address,specialty,description"
    ).eq("slug", slug).eq("active", True).execute()
    if not c.data: return jsonify({"error": "Clínica não encontrada"}), 404
    clinic = c.data[0]

    profs = supabase.table("professionals").select("id,name,specialty,photo_url,bio").eq("clinic_id", clinic["id"]).eq("active", True).execute()
    types = supabase.table("appointment_types").select("id,name,duration_min,color").eq("clinic_id", clinic["id"]).eq("active", True).execute()

    clinic["professionals"] = profs.data
    clinic["appointment_types"] = types.data
    return jsonify(clinic)

@app.route("/public/<slug>/available-slots", methods=["GET"])
def get_available_slots(slug):
    date_str = request.args.get("date")
    prof_id  = request.args.get("professional_id")
    type_id  = request.args.get("appointment_type_id")
    if not date_str: return jsonify({"error": "date obrigatório"}), 400

    try: target_date = date.fromisoformat(date_str)
    except: return jsonify({"error": "Formato inválido. Use YYYY-MM-DD"}), 400

    clinic = supabase.table("clinics").select("id").eq("slug", slug).eq("active", True).execute()
    if not clinic.data: return jsonify({"error": "Clínica não encontrada"}), 404
    clinic_id = clinic.data[0]["id"]

    weekday_db = (target_date.weekday() + 1) % 7
    schedule = supabase.table("schedules").select("*").eq("clinic_id", clinic_id).eq("weekday", weekday_db).eq("active", True).execute()
    if not schedule.data: return jsonify({"slots": [], "message": "Sem atendimento neste dia"})

    # Duração do slot: usa tipo se informado, senão usa schedule
    slot_min = schedule.data[0]["slot_minutes"]
    if type_id:
        t = supabase.table("appointment_types").select("duration_min").eq("id", type_id).execute()
        if t.data: slot_min = t.data[0]["duration_min"]

    day_blocked = supabase.table("blocked_times").select("id").eq("clinic_id", clinic_id).eq("date", date_str).is_("start_time", "null").execute()
    if day_blocked.data: return jsonify({"slots": [], "message": "Dia bloqueado"})

    sched = schedule.data[0]
    slots = _generate_slots(sched["start_time"], sched["end_time"], slot_min)

    q = supabase.table("appointments").select("time,slot_minutes").eq("clinic_id", clinic_id).eq("date", date_str).neq("status", "cancelado")
    if prof_id: q = q.eq("professional_id", prof_id)
    existing = q.execute()
    occupied = {a["time"][:5] for a in existing.data}

    partial = supabase.table("blocked_times").select("start_time,end_time").eq("clinic_id", clinic_id).eq("date", date_str).not_.is_("start_time", "null").execute()

    free = []
    for slot in slots:
        if slot in occupied: continue
        if _is_blocked(slot, partial.data): continue
        if target_date == date.today() and slot <= datetime.now().strftime("%H:%M"): continue
        free.append(slot)

    return jsonify({"slots": free})

@app.route("/public/<slug>/book", methods=["POST"])
def book_appointment(slug):
    data = request.json
    if not all(k in data for k in ["patient_name","patient_phone","date","time"]):
        return jsonify({"error": "patient_name, patient_phone, date e time obrigatórios"}), 400

    clinic = supabase.table("clinics").select("*").eq("slug", slug).eq("active", True).execute()
    if not clinic.data: return jsonify({"error": "Clínica não encontrada"}), 404
    clinic = clinic.data[0]

    # Verifica conflito
    q = supabase.table("appointments").select("id").eq("clinic_id", clinic["id"]).eq("date", data["date"]).eq("time", data["time"]).neq("status", "cancelado")
    if data.get("professional_id"): q = q.eq("professional_id", data["professional_id"])
    if q.execute().data: return jsonify({"error": "Horário não disponível"}), 409

    # Cria/busca paciente
    patient = _get_or_create_patient(clinic["id"], data)

    # Slot minutes
    slot_min = 30
    if data.get("appointment_type_id"):
        t = supabase.table("appointment_types").select("duration_min").eq("id", data["appointment_type_id"]).execute()
        if t.data: slot_min = t.data[0]["duration_min"]

    appt = supabase.table("appointments").insert({
        "clinic_id":            clinic["id"],
        "patient_id":           patient["id"],
        "patient_name":         data["patient_name"],
        "patient_phone":        data["patient_phone"],
        "patient_email":        data.get("patient_email",""),
        "professional_id":      data.get("professional_id"),
        "appointment_type_id":  data.get("appointment_type_id"),
        "date":                 data["date"],
        "time":                 data["time"],
        "slot_minutes":         slot_min,
        "notes":                data.get("notes",""),
        "status":               "pendente"
    }).execute().data[0]

    _send_confirmation_emails(appt, clinic)
    _send_whatsapp_confirmation(appt, clinic)

    return jsonify({"success": True, "appointment_id": appt["id"],
                    "message": "Agendamento confirmado!"}), 201

# ============================================================
# CRON — Lembretes automáticos (chamar diariamente via cron)
# ============================================================
@app.route("/cron/reminders", methods=["POST"])
def send_reminders():
    secret = request.headers.get("X-Cron-Secret","")
    if secret != os.environ.get("CRON_SECRET",""):
        return jsonify({"error": "Não autorizado"}), 401

    tomorrow = str(date.today() + timedelta(days=1))
    today    = str(date.today())

    # Lembretes 24h
    appts_24h = supabase.table("appointments").select(
        "*, clinics(*)"
    ).eq("date", tomorrow).eq("reminder_24h_sent", False).neq("status", "cancelado").execute()

    for appt in appts_24h.data:
        clinic = appt.get("clinics") or {}
        _send_whatsapp_reminder(appt, clinic, "24h")
        supabase.table("appointments").update({"reminder_24h_sent": True}).eq("id", appt["id"]).execute()

    # Lembretes no dia
    appts_day = supabase.table("appointments").select(
        "*, clinics(*)"
    ).eq("date", today).eq("reminder_day_sent", False).neq("status", "cancelado").execute()

    for appt in appts_day.data:
        clinic = appt.get("clinics") or {}
        _send_whatsapp_reminder(appt, clinic, "hoje")
        supabase.table("appointments").update({"reminder_day_sent": True}).eq("id", appt["id"]).execute()

    return jsonify({"ok": True, "24h": len(appts_24h.data), "hoje": len(appts_day.data)})

# ============================================================
# HELPERS
# ============================================================
def _get_or_create_patient(clinic_id, data):
    existing = supabase.table("patients").select("*").eq("clinic_id", clinic_id).eq("phone", data["patient_phone"]).execute()
    if existing.data:
        return existing.data[0]
    return supabase.table("patients").insert({
        "clinic_id": clinic_id,
        "name":  data["patient_name"],
        "phone": data["patient_phone"],
        "email": data.get("patient_email","")
    }).execute().data[0]

def _generate_slots(start_str, end_str, slot_min):
    h, m = map(int, start_str[:5].split(":"))
    eh, em = map(int, end_str[:5].split(":"))
    cur = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
    end = datetime.now().replace(hour=eh, minute=em, second=0, microsecond=0)
    slots = []
    while cur < end:
        slots.append(cur.strftime("%H:%M"))
        cur += timedelta(minutes=slot_min)
    return slots

def _is_blocked(slot, blocks):
    for b in blocks:
        if b.get("start_time") and b.get("end_time"):
            if b["start_time"][:5] <= slot < b["end_time"][:5]:
                return True
    return False

def _send_confirmation_emails(appt, clinic):
    try:
        data_fmt = datetime.strptime(appt["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
        hora     = appt["time"][:5]
        if appt.get("patient_email"):
            resend.Emails.send({
                "from": EMAIL_FROM, "to": appt["patient_email"],
                "subject": f"Consulta confirmada — {clinic['name']}",
                "html": f"<h2>Consulta confirmada!</h2><p>Olá {appt['patient_name']}, seu agendamento em <b>{clinic['name']}</b> está confirmado para <b>{data_fmt} às {hora}</b>.</p>"
            })
        resend.Emails.send({
            "from": EMAIL_FROM, "to": clinic["email"],
            "subject": f"Novo agendamento — {appt['patient_name']}",
            "html": f"<h2>Novo agendamento</h2><p><b>Paciente:</b> {appt['patient_name']}<br><b>Tel:</b> {appt['patient_phone']}<br><b>Data:</b> {data_fmt} às {hora}</p>"
        })
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")

def _send_whatsapp(phone, message, zapi_instance, zapi_token):
    if not zapi_instance or not zapi_token: return
    phone_clean = "".join(filter(str.isdigit, phone))
    if not phone_clean.startswith("55"): phone_clean = "55" + phone_clean
    try:
        requests.post(
            f"{ZAPI_BASE}/{zapi_instance}/token/{zapi_token}/send-text",
            json={"phone": phone_clean, "message": message},
            timeout=10
        )
    except Exception as e:
        print(f"[WHATSAPP ERROR] {e}")

def _send_whatsapp_confirmation(appt, clinic):
    if not clinic.get("zapi_instance"): return
    data_fmt = datetime.strptime(appt["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
    hora     = appt["time"][:5]

    # Mensagem para o paciente
    msg_paciente = (
        f"Olá {appt['patient_name']}! 👋\n\n"
        f"Seu agendamento foi confirmado:\n"
        f"📅 *{data_fmt}* às *{hora}*\n"
        f"🏥 {clinic['name']}\n\n"
        f"Responda *1* para confirmar presença ou *2* para cancelar."
    )
    _send_whatsapp(appt["patient_phone"], msg_paciente, clinic["zapi_instance"], clinic["zapi_token"])

    # Mensagem para a clínica
    if clinic.get("whatsapp_number"):
        msg_clinica = (
            f"📋 *Novo agendamento*\n"
            f"👤 Paciente: {appt['patient_name']}\n"
            f"📱 Tel: {appt['patient_phone']}\n"
            f"📅 Data: {data_fmt} às {hora}"
        )
        _send_whatsapp(clinic["whatsapp_number"], msg_clinica, clinic["zapi_instance"], clinic["zapi_token"])

def _send_whatsapp_reminder(appt, clinic, tipo):
    if not clinic.get("zapi_instance"): return
    data_fmt = datetime.strptime(appt["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
    hora     = appt["time"][:5]
    if tipo == "24h":
        msg = (f"Olá {appt['patient_name']}! 😊\n\nLembrete: você tem consulta *amanhã* ({data_fmt}) às *{hora}* em {clinic.get('name','')}.\n\nConfirme com *1* ou cancele com *2*.")
    else:
        msg = (f"Bom dia {appt['patient_name']}! 🌅\n\nSua consulta é *hoje* às *{hora}* em {clinic.get('name','')}. Te esperamos!")
    _send_whatsapp(appt["patient_phone"], msg, clinic["zapi_instance"], clinic["zapi_token"])

@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": "2.0"})

if __name__ == "__main__":
    app.run(debug=True, port=5000)
