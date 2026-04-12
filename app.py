# ============================================================
# PUBLIC — Agendamento pelo paciente
# Substitua todo o bloco /public/<slug> por este
# ============================================================

import unicodedata
from datetime import datetime, timedelta


def slugify_text(value: str) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", str(value))
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9\s-]", "", value)
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-")


def get_public_clinic_by_slug(slug: str):
    """
    Busca clínica pública de forma robusta.
    Resolve problema de slug com/sem acento, espaço, maiúscula etc.
    """
    incoming_slug = slugify_text(slug)

    result = (
        supabase.table("clinics")
        .select("id,name,slug,phone,email,logo_url,address,specialty,description,active")
        .eq("active", True)
        .execute()
    )

    clinics = result.data or []

    clinic = None
    for c in clinics:
        db_slug = slugify_text(c.get("slug", ""))
        if db_slug == incoming_slug:
            clinic = c
            break

    return clinic


def generate_slots(start_time: str, end_time: str, slot_minutes: int):
    slots = []
    start_dt = datetime.strptime(start_time[:5], "%H:%M")
    end_dt = datetime.strptime(end_time[:5], "%H:%M")

    current = start_dt
    while current + timedelta(minutes=slot_minutes) <= end_dt:
        slots.append(current.strftime("%H:%M"))
        current += timedelta(minutes=slot_minutes)

    return slots


@app.route("/public/<path:slug>", methods=["GET"])
def get_clinic_public(slug):
    clinic = get_public_clinic_by_slug(slug)
    if not clinic:
        return jsonify({"error": "Clínica não encontrada"}), 404

    profs_res = (
        supabase.table("professionals")
        .select("id,name,specialty,photo_url,bio,active")
        .eq("clinic_id", clinic["id"])
        .eq("active", True)
        .execute()
    )

    types_res = (
        supabase.table("appointment_types")
        .select("id,name,duration_min,color,active")
        .eq("clinic_id", clinic["id"])
        .eq("active", True)
        .execute()
    )

    clinic["professionals"] = profs_res.data or []
    clinic["appointment_types"] = types_res.data or []

    return jsonify(clinic)


@app.route("/public/<path:slug>/available-slots", methods=["GET"])
def get_available_slots(slug):
    clinic = get_public_clinic_by_slug(slug)
    if not clinic:
        return jsonify({"error": "Clínica não encontrada"}), 404

    date = request.args.get("date")
    professional_id = request.args.get("professional_id")
    appointment_type_id = request.args.get("appointment_type_id")

    if not date:
        return jsonify({"error": "Data é obrigatória"}), 400

    try:
        date_obj = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "Data inválida"}), 400

    weekday = date_obj.weekday()
    # Python: segunda=0 ... domingo=6
    # Se teu banco usa domingo=0, ajusta:
    weekday_db = (weekday + 1) % 7

    schedules_res = (
        supabase.table("schedules")
        .select("*")
        .eq("clinic_id", clinic["id"])
        .eq("weekday", weekday_db)
        .eq("active", True)
        .execute()
    )

    schedules = schedules_res.data or []
    if not schedules:
        return jsonify({"slots": []})

    schedule = schedules[0]
    slot_minutes = int(schedule.get("slot_minutes", 30))

    if appointment_type_id:
        type_res = (
            supabase.table("appointment_types")
            .select("duration_min")
            .eq("id", appointment_type_id)
            .eq("clinic_id", clinic["id"])
            .execute()
        )
        if type_res.data:
            slot_minutes = int(type_res.data[0].get("duration_min", slot_minutes))

    slots = generate_slots(schedule["start_time"], schedule["end_time"], slot_minutes)

    blocked_res = (
        supabase.table("blocked_times")
        .select("*")
        .eq("clinic_id", clinic["id"])
        .eq("date", date)
        .execute()
    )
    blocked = blocked_res.data or []

    appointments_query = (
        supabase.table("appointments")
        .select("time,status,professional_id")
        .eq("clinic_id", clinic["id"])
        .eq("date", date)
    )

    appointments_res = appointments_query.execute()
    appointments = appointments_res.data or []

    taken_slots = set()
    for appt in appointments:
        if appt.get("status") == "cancelado":
            continue
        if professional_id and appt.get("professional_id") != professional_id:
            continue
        if appt.get("time"):
            taken_slots.add(appt["time"][:5])

    blocked_slots = set()
    full_day_block = False

    for b in blocked:
        b_start = b.get("start_time")
        b_end = b.get("end_time")

        if not b_start or not b_end:
            full_day_block = True
            break

        start_block = datetime.strptime(b_start[:5], "%H:%M")
        end_block = datetime.strptime(b_end[:5], "%H:%M")

        for s in slots:
            slot_dt = datetime.strptime(s, "%H:%M")
            if start_block <= slot_dt < end_block:
                blocked_slots.add(s)

    if full_day_block:
        return jsonify({"slots": []})

    available = [s for s in slots if s not in taken_slots and s not in blocked_slots]
    return jsonify({"slots": available})


@app.route("/public/<path:slug>/book", methods=["POST"])
def public_book(slug):
    clinic = get_public_clinic_by_slug(slug)
    if not clinic:
        return jsonify({"error": "Clínica não encontrada"}), 404

    data = request.get_json(silent=True) or {}

    patient_name = (data.get("patient_name") or "").strip()
    patient_phone = (data.get("patient_phone") or "").strip()
    patient_email = (data.get("patient_email") or "").strip()
    date = (data.get("date") or "").strip()
    time = (data.get("time") or "").strip()
    notes = (data.get("notes") or "").strip()
    professional_id = data.get("professional_id")
    appointment_type_id = data.get("appointment_type_id")

    if not patient_name or not patient_phone or not date or not time:
        return jsonify({"error": "Nome, telefone, data e horário são obrigatórios"}), 400

    # Verifica conflito
    conflict_res = (
        supabase.table("appointments")
        .select("id,status")
        .eq("clinic_id", clinic["id"])
        .eq("date", date)
        .eq("time", time)
        .execute()
    )

    conflicts = conflict_res.data or []
    for c in conflicts:
        if c.get("status") != "cancelado":
            return jsonify({"error": "Esse horário já foi reservado"}), 409

    # Busca ou cria paciente
    patient_id = None
    patient_res = (
        supabase.table("patients")
        .select("id")
        .eq("clinic_id", clinic["id"])
        .eq("phone", patient_phone)
        .execute()
    )

    patients = patient_res.data or []
    if patients:
        patient_id = patients[0]["id"]
        supabase.table("patients").update({
            "name": patient_name,
            "email": patient_email or None
        }).eq("id", patient_id).execute()
    else:
        created_patient = (
            supabase.table("patients")
            .insert({
                "clinic_id": clinic["id"],
                "name": patient_name,
                "phone": patient_phone,
                "email": patient_email or None
            })
            .execute()
        )
        if created_patient.data:
            patient_id = created_patient.data[0]["id"]

    # Cria agendamento
    created = (
        supabase.table("appointments")
        .insert({
            "clinic_id": clinic["id"],
            "patient_id": patient_id,
            "professional_id": professional_id,
            "appointment_type_id": appointment_type_id,
            "patient_name": patient_name,
            "patient_phone": patient_phone,
            "patient_email": patient_email or None,
            "date": date,
            "time": time,
            "notes": notes or None,
            "status": "pendente"
        })
        .execute()
    )

    if not created.data:
        return jsonify({"error": "Não foi possível criar o agendamento"}), 500

    appointment = created.data[0]

    # Aqui depois você pode integrar e-mail / WhatsApp
    return jsonify({
        "success": True,
        "appointment": appointment
    }), 201