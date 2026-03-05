from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional, List
import re
import httpx
from datetime import datetime

app = FastAPI(title="Euromir Fichajes API", version="2.0")

SUPABASE_URL = "https://yqpnrouipdikduvuxhsa.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlxcG5yb3VpcGRpa2R1dnV4aHNhIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzEwMDUwMjgsImV4cCI6MjA4NjU4MTAyOH0.A08W9dh1x4AYM79tdz60GnE_z1lHJZLPYo7Dht9VrI8"
EVO_URL = "https://euromirbot-evolution-api.wp2z39.easypanel.host"
EVO_KEY = "C4811CA688A3-4C84-99B7-5206E31CD979"
ADMIN_TEL = "34677716161"
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}
HEADERS_GET = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


# ============ MODELS ============

class ProcesarMsg(BaseModel):
    mensaje: str
    empleado_id: int
    empleado_nombre: str
    empleado_telefono: str = ""
    coste_hora: float = 0
    fuera_madrid_hora: float = 0
    fecha: Optional[str] = None

class SeleccionarObra(BaseModel):
    empleado_id: int
    empleado_nombre: str
    empleado_telefono: str = ""
    obra_id: int
    obra_nombre: str
    fuera_madrid: bool = False
    hora_inicio: str
    hora_fin: str
    horas_decimal: float
    coste_hora: float = 0
    fuera_madrid_hora: float = 0
    fecha: Optional[str] = None

class ConfirmarFichajes(BaseModel):
    respuesta: str
    obra_id: Optional[int] = None
    encargado_telefono: str = ""

class CheckIn(BaseModel):
    empleado_id: int
    empleado_nombre: str
    empleado_telefono: str = ""
    obra_id: int
    obra_nombre: str

class CheckOut(BaseModel):
    empleado_id: int
    empleado_nombre: str
    empleado_telefono: str = ""
    hora_entrada: str
    obra_id: int
    obra_nombre: str
    fuera_madrid: bool = False
    coste_hora: float = 0
    fuera_madrid_hora: float = 0


# ============ HELPERS ============

def parse_horas(texto):
    texto = texto.lower().strip()
    # "de 9 a 17", "de 07:00 a 18:00"
    m = re.search(r'de\s+(\d{1,2})[:\.]?(\d{2})?\s+a\s+(\d{1,2})[:\.]?(\d{2})?', texto)
    if m:
        h1, m1 = int(m.group(1)), int(m.group(2) or 0)
        h2, m2 = int(m.group(3)), int(m.group(4) or 0)
        return {"hora_inicio": f"{h1:02d}:{m1:02d}", "hora_fin": f"{h2:02d}:{m2:02d}", "horas": ((h2*60+m2)-(h1*60+m1))/60}
    # "9-17"
    m = re.search(r'(\d{1,2})[:\.]?(\d{2})?\s*[-–]\s*(\d{1,2})[:\.]?(\d{2})?', texto)
    if m:
        h1, m1 = int(m.group(1)), int(m.group(2) or 0)
        h2, m2 = int(m.group(3)), int(m.group(4) or 0)
        return {"hora_inicio": f"{h1:02d}:{m1:02d}", "hora_fin": f"{h2:02d}:{m2:02d}", "horas": ((h2*60+m2)-(h1*60+m1))/60}
    # "8 horas", "8h"
    m = re.search(r'(\d+)\s*h', texto)
    if m:
        return {"horas": int(m.group(1))}
    return {}

def detectar_intencion(texto):
    t = texto.lower().strip()
    if any(w in t for w in ["empiezo", "entrada", "ya estoy", "llego", "inicio jornada", "empezar"]):
        return "check_in"
    if any(w in t for w in ["salgo", "termino", "acabé", "acabo", "salida", "me voy", "fin jornada"]):
        return "check_out"
    if any(w in t for w in ["trabajé", "trabaje", "ficho", "fichar"]) or re.search(r'\d+\s*h', t) or re.search(r'de\s+\d+\s+a\s+\d+', t):
        return "fichar_horas"
    return "desconocido"

async def supabase_get(path):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=HEADERS_GET)
        return r.json() if r.status_code == 200 else []

async def supabase_post(table, data):
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=HEADERS, json=data)
        return r.json() if r.status_code in (200, 201) else {"error": r.text}

async def supabase_patch(table, filtro, data):
    async with httpx.AsyncClient() as c:
        r = await c.patch(f"{SUPABASE_URL}/rest/v1/{table}?{filtro}", headers=HEADERS, json=data)
        return r.json() if r.status_code == 200 else {"error": r.text}

async def enviar_whatsapp(telefono, texto):
    async with httpx.AsyncClient() as c:
        await c.post(f"{EVO_URL}/message/sendText/EuromirBia",
            headers={"apikey": EVO_KEY, "Content-Type": "application/json"},
            json={"number": f"{telefono}@s.whatsapp.net", "text": texto})

async def get_obras_activas():
    return await supabase_get("obras?estado=eq.En curso&select=id,nombre,fuera_madrid&limit=50")

async def get_fichajes_dia(empleado_id, fecha):
    return await supabase_get(f"fichajes_tramos?empleado_id=eq.{empleado_id}&fecha=eq.{fecha}&select=id,hora_inicio,hora_fin,horas_decimal,obra_nombre,estado")

def formato_obras(obras):
    lineas = []
    for i, o in enumerate(obras):
        emoji = "🌍" if o.get("fuera_madrid") else "🏗️"
        lineas.append(f"{i+1}. *{o['nombre']}* {emoji}")
    return "\n".join(lineas)


# ============ ENDPOINTS ============

@app.post("/procesar-fichaje")
async def procesar_fichaje(req: ProcesarMsg):
    fecha = req.fecha or datetime.now().strftime("%Y-%m-%d")
    intencion = detectar_intencion(req.mensaje)

    if intencion == "check_in":
        obras = await get_obras_activas()
        return {"accion": "check_in", "mensaje": f"¿En qué obra empiezas? 🏗️\n\n{formato_obras(obras)}\n\nDime número o nombre 😊", "obras": obras}

    if intencion == "check_out":
        return {"accion": "check_out", "mensaje": "check_out", "datos": {"hora_salida": datetime.now().strftime("%H:%M")}}

    if intencion == "fichar_horas":
        horas = parse_horas(req.mensaje)
        if not horas.get("horas") and not horas.get("hora_inicio"):
            return {"accion": "pedir_horas", "mensaje": "¿Cuántas horas? Dime 'de 9 a 17' o '8 horas' 🕐"}

        h = horas.get("horas", 0)
        netas = h - 1 if h > 6 else h
        horas["horas_netas"] = netas

        fichajes = await get_fichajes_dia(req.empleado_id, fecha)
        if fichajes:
            return {"accion": "duplicado", "mensaje": f"Ya tienes fichaje de hoy. ¿Añadir otro tramo? 🤔", "datos": horas}

        obras = await get_obras_activas()
        return {"accion": "pedir_obra", "mensaje": f"OK, {netas}h netas. ¿En qué obra? 🏗️\n\n{formato_obras(obras)}\n\nDime número o nombre 😊", "datos": horas, "obras": obras}

    return {"accion": "desconocido", "mensaje": "No entendí. Dime tus horas (ej: 'de 9 a 17') o 'empiezo' para entrada 😊"}


@app.post("/seleccionar-obra")
async def seleccionar_obra(req: SeleccionarObra):
    fecha = req.fecha or datetime.now().strftime("%Y-%m-%d")
    tarifa = req.fuera_madrid_hora if req.fuera_madrid else req.coste_hora
    total = req.horas_decimal * tarifa

    datos = {
        "fecha": fecha, "empleado_id": req.empleado_id, "empleado_nombre": req.empleado_nombre,
        "obra_id": req.obra_id, "obra_nombre": req.obra_nombre,
        "hora_inicio": req.hora_inicio, "hora_fin": req.hora_fin, "horas_decimal": req.horas_decimal,
        "coste_hora": tarifa, "coste_total": total, "estado": "BORRADOR",
        "tipo_dia": "LABORABLE", "fuera_madrid": req.fuera_madrid
    }

    result = await supabase_post("fichajes_tramos", datos)
    if "error" in result:
        return {"success": False, "mensaje": "Error al registrar fichaje", "error": str(result)}

    return {"success": True, "mensaje": f"✅ Registrado como borrador: *{req.empleado_nombre}* {req.hora_inicio}-{req.hora_fin} ({req.horas_decimal}h) en *{req.obra_nombre}*. Tu encargado lo revisará.", "fichaje": result}


@app.post("/confirmar-fichajes")
async def confirmar_fichajes(req: ConfirmarFichajes):
    resp = req.respuesta.lower().strip()
    fecha = datetime.now().strftime("%Y-%m-%d")

    # Leer borradores de hoy
    filtro = f"estado=eq.BORRADOR&fecha=eq.{fecha}&select=id,empleado_id,empleado_nombre,obra_id,obra_nombre,hora_inicio,hora_fin,horas_decimal,coste_hora,coste_total,fuera_madrid"
    if req.obra_id:
        filtro += f"&obra_id=eq.{req.obra_id}"

    borradores = await supabase_get(f"fichajes_tramos?{filtro}")
    if not borradores:
        return {"success": True, "mensaje": "No hay fichajes pendientes", "confirmados": 0}

    confirmados = []
    rechazados = []

    if "todos" in resp or "ok" in resp or resp in ("si", "sí", "✅", "confirmo"):
        confirmados = borradores
    elif "rechaz" in resp or resp in ("no", "❌"):
        # Parsear número: "rechazar 2"
        m = re.search(r'(\d+)', resp)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(borradores):
                rechazados = [borradores[idx]]
                confirmados = [b for i, b in enumerate(borradores) if i != idx]
            else:
                confirmados = borradores
        else:
            rechazados = borradores
    else:
        confirmados = borradores

    # PATCH confirmados
    for f in confirmados:
        await supabase_patch("fichajes_tramos", f"id=eq.{f['id']}", {"estado": "CONFIRMADO"})
        # Notificar empleado
        emp = await supabase_get(f"empleados?id=eq.{f['empleado_id']}&select=telefono")
        if emp and emp[0].get("telefono"):
            await enviar_whatsapp(emp[0]["telefono"],
                f"✅ *{f.get('empleado_nombre', '')}*, tu fichaje ha sido confirmado: {str(f.get('hora_inicio',''))[:5]} a {str(f.get('hora_fin',''))[:5]} ({f.get('horas_decimal',0)}h)")

    # PATCH rechazados
    for f in rechazados:
        await supabase_patch("fichajes_tramos", f"id=eq.{f['id']}", {"estado": "RECHAZADO"})
        emp = await supabase_get(f"empleados?id=eq.{f['empleado_id']}&select=telefono")
        if emp and emp[0].get("telefono"):
            await enviar_whatsapp(emp[0]["telefono"],
                f"❌ Tu fichaje de hoy ha sido rechazado. Contacta con tu encargado.")

    # Resumen al admin
    nombres = "\n".join([f"• *{f.get('empleado_nombre','')}* {str(f.get('hora_inicio',''))[:5]}-{str(f.get('hora_fin',''))[:5]} ({f.get('horas_decimal',0)}h)" for f in confirmados])
    await enviar_whatsapp(ADMIN_TEL, f"📊 *Fichajes confirmados*:\n\n{nombres}\n\n✅ Total: {len(confirmados)}")

    return {"success": True, "confirmados": len(confirmados), "rechazados": len(rechazados), "mensaje": f"✅ {len(confirmados)} confirmados, {len(rechazados)} rechazados"}


@app.post("/check-in")
async def check_in(req: CheckIn):
    hora = datetime.now().strftime("%H:%M")
    # Guardar en notas del empleado
    await supabase_patch("empleados", f"id=eq.{req.empleado_id}",
        {"notas_bia": f"CHECK-IN: {hora} en {req.obra_nombre} el {datetime.now().strftime('%Y-%m-%d')}"})

    return {"success": True, "mensaje": f"Anotado, entrada a las {hora} en *{req.obra_nombre}* 🏗️ Avísame cuando salgas 👍", "hora_entrada": hora}


@app.post("/check-out")
async def check_out(req: CheckOut):
    hora_salida = datetime.now().strftime("%H:%M")
    h1, m1 = map(int, req.hora_entrada.split(":"))
    h2, m2 = map(int, hora_salida.split(":"))
    total_min = (h2 * 60 + m2) - (h1 * 60 + m1)
    horas = total_min / 60
    netas = horas - 1 if horas > 6 else horas
    tarifa = req.fuera_madrid_hora if req.fuera_madrid else req.coste_hora
    coste = netas * tarifa

    datos = {
        "fecha": datetime.now().strftime("%Y-%m-%d"),
        "empleado_id": req.empleado_id, "empleado_nombre": req.empleado_nombre,
        "obra_id": req.obra_id, "obra_nombre": req.obra_nombre,
        "hora_inicio": req.hora_entrada, "hora_fin": hora_salida,
        "horas_decimal": round(netas, 1), "coste_hora": tarifa, "coste_total": round(coste, 2),
        "estado": "BORRADOR", "tipo_dia": "LABORABLE", "fuera_madrid": req.fuera_madrid
    }
    result = await supabase_post("fichajes_tramos", datos)

    # Limpiar check-in de notas
    await supabase_patch("empleados", f"id=eq.{req.empleado_id}", {"notas_bia": ""})

    return {"success": True, "mensaje": f"✅ {round(netas,1)}h netas ({req.hora_entrada}-{hora_salida}). Registrado como borrador en *{req.obra_nombre}*", "fichaje": result}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "euromir-fichajes", "version": "2.0"}
