"""
MediAgenda — Backend Flask
Rotas completas para o sistema de agendamento médico multi-tenant
"""

import os
import resend
from flask import Flask, request, jsonify
from flask_cors import CORS
from supabase import create_client, Client
from datetime import datetime, date, time, timedelta
from functools import wraps
import jwt
import bcrypt

app = Flask(__name__)
CORS(app)

# ============================================================
# CONFIGURAÇÃO
# ============================================================
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]  # service_role key — bypassa RLS
JWT_SECRET   = os.environ["JWT_SECRET"]
resend.api_key = os.environ["RESEND_API_KEY"]
EMAIL_FROM   = os.environ.get("EMAIL_FROM", "noreply@mediagenda.com.br")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ============================================================
# AUTENTICAÇÃO JWT
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


# ============================================================
# AUTH — Login / Registro do Master
# ============================================================

@app.route("/auth/register", methods=["POST"])
def register():
    data = request.json
    required = ["name", "email", "password"]
    if not all(k in data for k in required):
        return jsonify({"error": "Campos obrigatórios: name, email, password"}), 400

    # Verifica se email já existe
    existing = supabase.table("masters").select("id").eq("email", data["email"]).execute()
    if existing.data:
        return jsonify({"error": "E-mail já cadastrado"}), 409

    password_hash = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()
    master = supabase.table("masters").insert({
        "name": data["name"],
        "email": data["email"],
        "password_hash": password_hash
    }).execute().data[0]

    token = jwt.encode(
        {"master_id": master["id"], "exp": datetime.utcnow() + timedelta(days=30)},
        JWT_SECRET, algorithm="HS256"
    )
    return jsonify({"token": token, "master": {"id": master["id"], "name": master["name"]}})


@app.route("/auth/login", methods=["POST"])
def login():
    data = request.json
    master = supabase.table("masters").select("*").eq("email", data.get("email", "")).execute().data
    if not master:
        return jsonify({"error": "Credenciais inválidas"}), 401

    master = master[0]
    if not bcrypt.checkpw(data["password"].encode(), master["password_hash"].encode()):
        return jsonify({"error": "Credenciais inválidas"}), 401

    token = jwt.encode(
        {"master_id": master["id"], "exp": datetime.utcnow() + timedelta(days=30)},
        JWT_SECRET, algorithm="HS256"
    )
    return jsonify({"token": token, "master": {"id": master["id"], "name": master["name"]}})


# ============================================================
# CLÍNICAS (CRUD — painel master)
# ============================================================

@app.route("/clinics", methods=["GET"])
@token_required
def list_clinics():
    clinics = supabase.table("clinics")\
        .select("*")\
        .eq("master_id", request.master_id)\
        .order("name")\
        .execute()
    return jsonify(clinics.data)


@app.route("/clinics", methods=["POST"])
@token_required
def create_clinic():
    data = request.json
    required = ["name", "slug", "email"]
    if not all(k in data for k in required):
        return jsonify({"error": "Campos obrigatórios: name, slug, email"}), 400

    # Verifica slug único
    existing = supabase.table("clinics").select("id").eq("slug", data["slug"]).execute()
    if existing.data:
        return jsonify({"error": "Slug já em uso"}), 409

    clinic = supabase.table("clinics").insert({
        "master_id": request.master_id,
        "name": data["name"],
        "slug": data["slug"],
        "email": data["email"],
        "phone": data.get("phone"),
        "address": data.get("address"),
        "specialty": data.get("specialty"),
        "logo_url": data.get("logo_url"),
        "active": True
    }).execute().data[0]
    return jsonify(clinic), 201


@app.route("/clinics/<clinic_id>", methods=["PUT"])
@token_required
def update_clinic(clinic_id):
    # Garante que a clínica pertence ao master
    clinic = supabase.table("clinics").select("id")\
        .eq("id", clinic_id).eq("master_id", request.master_id).execute()
    if not clinic.data:
        return jsonify({"error": "Clínica não encontrada"}), 404

    data = request.json
    allowed = ["name", "email", "phone", "address", "specialty", "logo_url", "active"]
    update_data = {k: v for k, v in data.items() if k in allowed}

    updated = supabase.table("clinics").update(update_data).eq("id", clinic_id).execute()
    return jsonify(updated.data[0])


@app.route("/clinics/<clinic_id>", methods=["DELETE"])
@token_required
def delete_clinic(clinic_id):
    clinic = supabase.table("clinics").select("id")\
        .eq("id", clinic_id).eq("master_id", request.master_id).execute()
    if not clinic.data:
        return jsonify({"error": "Clínica não encontrada"}), 404

    supabase.table("clinics").delete().eq("id", clinic_id).execute()
    return jsonify({"success": True})


# ============================================================
# HORÁRIOS (schedules — configuração por dia da semana)
# ============================================================

@app.route("/clinics/<clinic_id>/schedules", methods=["GET"])
@token_required
def list_schedules(clinic_id):
    _assert_clinic_owner(clinic_id)
    schedules = supabase.table("schedules").select("*")\
        .eq("clinic_id", clinic_id).order("weekday").execute()
    return jsonify(schedules.data)


@app.route("/clinics/<clinic_id>/schedules", methods=["POST"])
@token_required
def create_schedule(clinic_id):
    _assert_clinic_owner(clinic_id)
    data = request.json

    # Remove schedules existentes para o mesmo weekday (substitui)
    supabase.table("schedules").delete()\
        .eq("clinic_id", clinic_id).eq("weekday", data["weekday"]).execute()

    schedule = supabase.table("schedules").insert({
        "clinic_id": clinic_id,
        "weekday": data["weekday"],
        "start_time": data["start_time"],
        "end_time": data["end_time"],
        "slot_minutes": data.get("slot_minutes", 30),
        "active": data.get("active", True)
    }).execute().data[0]
    return jsonify(schedule), 201


@app.route("/clinics/<clinic_id>/schedules/<schedule_id>", methods=["DELETE"])
@token_required
def delete_schedule(clinic_id, schedule_id):
    _assert_clinic_owner(clinic_id)
    supabase.table("schedules").delete().eq("id", schedule_id).execute()
    return jsonify({"success": True})


# ============================================================
# BLOQUEIOS (blocked_times — feriados, folgas, etc.)
# ============================================================

@app.route("/clinics/<clinic_id>/blocked", methods=["GET"])
@token_required
def list_blocked(clinic_id):
    _assert_clinic_owner(clinic_id)
    blocked = supabase.table("blocked_times").select("*")\
        .eq("clinic_id", clinic_id).order("date").execute()
    return jsonify(blocked.data)


@app.route("/clinics/<clinic_id>/blocked", methods=["POST"])
@token_required
def create_blocked(clinic_id):
    _assert_clinic_owner(clinic_id)
    data = request.json
    blocked = supabase.table("blocked_times").insert({
        "clinic_id": clinic_id,
        "date": data["date"],
        "start_time": data.get("start_time"),
        "end_time": data.get("end_time"),
        "reason": data.get("reason")
    }).execute().data[0]
    return jsonify(blocked), 201


@app.route("/clinics/<clinic_id>/blocked/<blocked_id>", methods=["DELETE"])
@token_required
def delete_blocked(clinic_id, blocked_id):
    _assert_clinic_owner(clinic_id)
    supabase.table("blocked_times").delete().eq("id", blocked_id).execute()
    return jsonify({"success": True})


# ============================================================
# AGENDAMENTOS — Dashboard do médico
# ============================================================

@app.route("/clinics/<clinic_id>/appointments", methods=["GET"])
@token_required
def list_appointments(clinic_id):
    _assert_clinic_owner(clinic_id)
    query = supabase.table("appointments").select("*").eq("clinic_id", clinic_id)

    # Filtros opcionais via query string
    if request.args.get("date"):
        query = query.eq("date", request.args["date"])
    if request.args.get("status"):
        query = query.eq("status", request.args["status"])

    start = request.args.get("start")
    end   = request.args.get("end")
    if start:
        query = query.gte("date", start)
    if end:
        query = query.lte("date", end)

    appointments = query.order("date").order("time").execute()
    return jsonify(appointments.data)


@app.route("/clinics/<clinic_id>/appointments/<appointment_id>/status", methods=["PATCH"])
@token_required
def update_appointment_status(clinic_id, appointment_id):
    _assert_clinic_owner(clinic_id)
    data = request.json
    valid_statuses = ["pendente", "confirmado", "cancelado", "concluido"]
    if data.get("status") not in valid_statuses:
        return jsonify({"error": f"Status inválido. Use: {valid_statuses}"}), 400

    updated = supabase.table("appointments").update({"status": data["status"]})\
        .eq("id", appointment_id).execute()
    return jsonify(updated.data[0])


# ============================================================
# PÁGINA PÚBLICA — Agendamento pelo paciente (sem auth)
# ============================================================

@app.route("/public/<slug>", methods=["GET"])
def get_clinic_public(slug):
    """Retorna dados públicos da clínica para montar a página"""
    clinic = supabase.table("clinics").select(
        "id, name, slug, phone, email, logo_url, address, specialty"
    ).eq("slug", slug).eq("active", True).execute()

    if not clinic.data:
        return jsonify({"error": "Clínica não encontrada"}), 404

    return jsonify(clinic.data[0])


@app.route("/public/<slug>/available-slots", methods=["GET"])
def get_available_slots(slug):
    """
    Retorna slots livres para uma data específica.
    Query param: ?date=2025-06-15
    """
    date_str = request.args.get("date")
    if not date_str:
        return jsonify({"error": "Parâmetro 'date' obrigatório"}), 400

    try:
        target_date = date.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "Formato de data inválido. Use YYYY-MM-DD"}), 400

    # Busca a clínica
    clinic = supabase.table("clinics").select("id").eq("slug", slug).eq("active", True).execute()
    if not clinic.data:
        return jsonify({"error": "Clínica não encontrada"}), 404
    clinic_id = clinic.data[0]["id"]

    # Dia da semana (0=seg no Python, mas nosso schema usa 0=dom)
    weekday_py = target_date.weekday()  # 0=seg ... 6=dom
    weekday_db = (weekday_py + 1) % 7   # converte para 0=dom

    # Busca schedule do dia
    schedule = supabase.table("schedules").select("*")\
        .eq("clinic_id", clinic_id)\
        .eq("weekday", weekday_db)\
        .eq("active", True)\
        .execute()

    if not schedule.data:
        return jsonify({"slots": [], "message": "Sem atendimento neste dia"})

    sched = schedule.data[0]

    # Verifica se o dia inteiro está bloqueado
    day_blocked = supabase.table("blocked_times").select("*")\
        .eq("clinic_id", clinic_id)\
        .eq("date", date_str)\
        .is_("start_time", "null")\
        .execute()

    if day_blocked.data:
        return jsonify({"slots": [], "message": "Dia bloqueado"})

    # Gera todos os slots do dia
    slots = _generate_slots(
        sched["start_time"], sched["end_time"], sched["slot_minutes"]
    )

    # Busca agendamentos existentes no dia
    existing = supabase.table("appointments").select("time, slot_minutes")\
        .eq("clinic_id", clinic_id)\
        .eq("date", date_str)\
        .neq("status", "cancelado")\
        .execute()
    occupied_times = {a["time"][:5] for a in existing.data}

    # Busca bloqueios parciais do dia
    partial_blocks = supabase.table("blocked_times").select("start_time, end_time")\
        .eq("clinic_id", clinic_id)\
        .eq("date", date_str)\
        .not_.is_("start_time", "null")\
        .execute()

    # Remove slots ocupados ou bloqueados
    free_slots = []
    for slot in slots:
        if slot in occupied_times:
            continue
        if _is_in_blocked_range(slot, partial_blocks.data):
            continue
        # Não mostra slots no passado para hoje
        if target_date == date.today():
            now = datetime.now().strftime("%H:%M")
            if slot <= now:
                continue
        free_slots.append(slot)

    return jsonify({"slots": free_slots})


@app.route("/public/<slug>/book", methods=["POST"])
def book_appointment(slug):
    """Cria um agendamento — chamado pela página pública"""
    data = request.json
    required = ["patient_name", "patient_phone", "date", "time"]
    if not all(k in data for k in required):
        return jsonify({"error": "Campos obrigatórios: patient_name, patient_phone, date, time"}), 400

    # Busca clínica
    clinic = supabase.table("clinics").select("*")\
        .eq("slug", slug).eq("active", True).execute()
    if not clinic.data:
        return jsonify({"error": "Clínica não encontrada"}), 404
    clinic = clinic.data[0]

    # Verifica se o slot ainda está livre
    conflict = supabase.table("appointments").select("id")\
        .eq("clinic_id", clinic["id"])\
        .eq("date", data["date"])\
        .eq("time", data["time"])\
        .neq("status", "cancelado")\
        .execute()
    if conflict.data:
        return jsonify({"error": "Horário não disponível. Por favor, escolha outro."}), 409

    # Busca slot_minutes do schedule
    target_date = date.fromisoformat(data["date"])
    weekday_db  = (target_date.weekday() + 1) % 7
    schedule = supabase.table("schedules").select("slot_minutes")\
        .eq("clinic_id", clinic["id"]).eq("weekday", weekday_db).execute()
    slot_minutes = schedule.data[0]["slot_minutes"] if schedule.data else 30

    # Cria o agendamento
    appointment = supabase.table("appointments").insert({
        "clinic_id":     clinic["id"],
        "patient_name":  data["patient_name"],
        "patient_phone": data["patient_phone"],
        "patient_email": data.get("patient_email", ""),
        "date":          data["date"],
        "time":          data["time"],
        "slot_minutes":  slot_minutes,
        "notes":         data.get("notes", ""),
        "status":        "pendente"
    }).execute().data[0]

    # Envia e-mails de confirmação
    _send_confirmation_emails(appointment, clinic)

    return jsonify({
        "success": True,
        "appointment_id": appointment["id"],
        "message": "Agendamento confirmado! Você receberá um e-mail de confirmação."
    }), 201


# ============================================================
# HELPERS
# ============================================================

def _assert_clinic_owner(clinic_id):
    """Garante que a clínica pertence ao master autenticado"""
    clinic = supabase.table("clinics").select("id")\
        .eq("id", clinic_id).eq("master_id", request.master_id).execute()
    if not clinic.data:
        from flask import abort
        abort(404, "Clínica não encontrada")


def _generate_slots(start_str: str, end_str: str, slot_minutes: int) -> list[str]:
    """Gera lista de horários HH:MM entre start e end com intervalo slot_minutes"""
    h, m = map(int, start_str[:5].split(":"))
    eh, em = map(int, end_str[:5].split(":"))
    current = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
    end_dt  = datetime.now().replace(hour=eh, minute=em, second=0, microsecond=0)
    slots = []
    while current < end_dt:
        slots.append(current.strftime("%H:%M"))
        current += timedelta(minutes=slot_minutes)
    return slots


def _is_in_blocked_range(slot: str, blocks: list) -> bool:
    """Verifica se um slot cai dentro de algum bloqueio parcial"""
    for block in blocks:
        if block["start_time"] and block["end_time"]:
            if block["start_time"][:5] <= slot < block["end_time"][:5]:
                return True
    return False


def _send_confirmation_emails(appointment: dict, clinic: dict):
    """Envia e-mail de confirmação para paciente e médico"""
    try:
        data_fmt = datetime.strptime(appointment["date"], "%Y-%m-%d").strftime("%d/%m/%Y")
        hora_fmt = appointment["time"][:5]

        # E-mail para o médico
        resend.Emails.send({
            "from": EMAIL_FROM,
            "to": clinic["email"],
            "subject": f"Novo agendamento — {appointment['patient_name']}",
            "html": f"""
            <div style="font-family:sans-serif;max-width:520px;margin:0 auto">
              <h2 style="color:#1D9E75">Novo agendamento recebido</h2>
              <table style="border-collapse:collapse;width:100%">
                <tr><td style="padding:8px;border-bottom:1px solid #eee"><strong>Paciente</strong></td>
                    <td style="padding:8px;border-bottom:1px solid #eee">{appointment['patient_name']}</td></tr>
                <tr><td style="padding:8px;border-bottom:1px solid #eee"><strong>Telefone</strong></td>
                    <td style="padding:8px;border-bottom:1px solid #eee">{appointment['patient_phone']}</td></tr>
                <tr><td style="padding:8px;border-bottom:1px solid #eee"><strong>Data</strong></td>
                    <td style="padding:8px;border-bottom:1px solid #eee">{data_fmt}</td></tr>
                <tr><td style="padding:8px;border-bottom:1px solid #eee"><strong>Horário</strong></td>
                    <td style="padding:8px;border-bottom:1px solid #eee">{hora_fmt}</td></tr>
                <tr><td style="padding:8px"><strong>Observação</strong></td>
                    <td style="padding:8px">{appointment.get('notes') or '—'}</td></tr>
              </table>
            </div>
            """
        })

        # E-mail para o paciente (só se tiver e-mail)
        if appointment.get("patient_email"):
            resend.Emails.send({
                "from": EMAIL_FROM,
                "to": appointment["patient_email"],
                "subject": f"Consulta confirmada — {clinic['name']}",
                "html": f"""
                <div style="font-family:sans-serif;max-width:520px;margin:0 auto">
                  <h2 style="color:#1D9E75">Sua consulta está confirmada!</h2>
                  <p>Olá, <strong>{appointment['patient_name']}</strong>.</p>
                  <p>Seu agendamento com <strong>{clinic['name']}</strong> foi registrado:</p>
                  <table style="border-collapse:collapse;width:100%">
                    <tr><td style="padding:8px;border-bottom:1px solid #eee"><strong>Data</strong></td>
                        <td style="padding:8px;border-bottom:1px solid #eee">{data_fmt}</td></tr>
                    <tr><td style="padding:8px"><strong>Horário</strong></td>
                        <td style="padding:8px">{hora_fmt}</td></tr>
                  </table>
                  <p style="margin-top:16px;color:#666">
                    Dúvidas? Entre em contato: {clinic.get('phone', clinic['email'])}
                  </p>
                </div>
                """
            })
    except Exception as e:
        # Não falha o agendamento se o e-mail falhar — loga e segue
        print(f"[ERRO EMAIL] {e}")


# ============================================================
# HEALTHCHECK
# ============================================================
@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
