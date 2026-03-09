from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.db import supabase
from app.twilio_helpers import send_sms

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/voice/incoming")
async def voice_incoming(
    CallSid: str = Form(...),
    From: str = Form(...),
    To: str = Form(...)
):
    supabase.table("leads").upsert(
        {
            "call_sid": CallSid,
            "caller_phone": From,
            "twilio_number": To,
            "owner_phone": settings.OWNER_PHONE,
            "business_name": settings.BUSINESS_NAME,
            "call_status": "incoming",
            "source": "voice",
        },
        on_conflict="call_sid"
    ).execute()

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Dial timeout="20" action="{settings.BASE_URL}/voice/dial-status" method="POST">
        <Number>{settings.OWNER_PHONE}</Number>
    </Dial>
</Response>"""

    return Response(content=twiml, media_type="application/xml")


@app.post("/voice/dial-status")
async def voice_dial_status(
    DialCallStatus: str = Form(None),
    CallSid: str = Form(...),
    From: str = Form(...)
):
    missed_statuses = {"no-answer", "busy", "failed", "canceled"}
    was_missed = DialCallStatus in missed_statuses

    supabase.table("leads").update(
        {
            "call_status": DialCallStatus,
            "was_missed": was_missed,
        }
    ).eq("call_sid", CallSid).execute()

    if was_missed:
        caller_text = (
            f"Hi, this is {settings.BUSINESS_NAME}. "
            "Sorry we missed your call. What do you need help with today?"
        )
        owner_text = f"Missed call from {From}. Intake text sent."

        send_sms(From, caller_text)
        send_sms(settings.OWNER_PHONE, owner_text)

        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">
        Sorry we missed your call to {settings.BUSINESS_NAME}. We have sent you a text message to collect your details.
    </Say>
    <Hangup />
</Response>"""

        return Response(content=twiml, media_type="application/xml")

    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Hangup />
</Response>"""

    return Response(content=twiml, media_type="application/xml")


@app.post("/sms/incoming")
async def sms_incoming(
    From: str = Form(...),
    To: str = Form(...),
    Body: str = Form(...)
):
    text = Body.strip()

    result = (
        supabase.table("leads")
        .select("*")
        .eq("caller_phone", From)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )

    if not result.data:
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response></Response>"""
        return Response(content=twiml, media_type="application/xml")

    lead = result.data[0]
    stage = lead["sms_stage"]

    reply = ""
    updates = {}

    if stage == "new":
        updates["service_needed"] = text
        updates["sms_stage"] = "awaiting_urgency"
        reply = "Thanks — is this urgent or related to storm damage?"

    elif stage == "awaiting_urgency":
        updates["urgency"] = text
        updates["sms_stage"] = "awaiting_location"
        reply = "What city or postal code is the job in?"

    elif stage == "awaiting_location":
        updates["location_text"] = text
        updates["sms_stage"] = "awaiting_name"
        reply = "Thanks — what’s your name?"

    elif stage == "awaiting_name":
        updates["customer_name"] = text
        updates["sms_stage"] = "complete"

        summary = (
            f"New {settings.BUSINESS_NAME} lead\n\n"
            f"Phone: {lead['caller_phone']}\n"
            f"Name: {text}\n"
            f"Service: {lead.get('service_needed') or ''}\n"
            f"Urgency: {lead.get('urgency') or ''}\n"
            f"Location: {lead.get('location_text') or ''}"
        )

        updates["summary_text"] = summary
        updates["summary_sent"] = True
        reply = (
            f"Thanks, {text}. We’ve sent your request to "
            f"{settings.BUSINESS_NAME} and someone will follow up soon."
        )

        if not lead.get("summary_sent"):
            send_sms(settings.OWNER_PHONE, summary)

    elif stage == "complete":
        reply = (
            f"Thanks — we already received your request and sent it to "
            f"{settings.BUSINESS_NAME}."
        )

    else:
        reply = (
            f"Hi, this is {settings.BUSINESS_NAME}. "
            "What do you need help with today?"
        )

    if updates:
        (
            supabase.table("leads")
            .update(updates)
            .eq("id", lead["id"])
            .execute()
        )

    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{reply}</Message>
</Response>"""

    return Response(content=twiml, media_type="application/xml")

@app.get("/leads", response_class=HTMLResponse)
def leads_page(request: Request):
    result = (
        supabase.table("leads")
        .select("*")
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    )

    return templates.TemplateResponse(
        "leads.html",
        {"request": request, "leads": result.data}
    )