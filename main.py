from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional, List
import re
import httpx
from datetime import datetime, timedelta

app = FastAPI(title="Euromir Fichajes API", version="3.0")

SUPABASE_URL = "https://yqpnrouipdikduvuxhsa.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlxcG5yb3VpcGRpa2R1dnV4aHNhIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzEwMDUwMjgsImV4cCI6MjA4NjU4MTAyOH0.A08W9dh1x4AYM79tdz60GnE_z1lHJZLPYo7Dht9VrI8"
EVO_URL = "https://euromirbot-evolution-api.wp2z39.easypanel.host"
EVO_KEY = "C4811CA688A3-4C84-99B7-5206E31CD979"
ADMIN_TEL = "34677716161"
H = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}
HG = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}


# ============ MODELS ============

class ProcesarMsg(BaseModel):
    mensaje: str
    empleado_id: int
    empleado_nombre: str
    empleado_telefono: str = ""
    coste_hora: float = 0
    fuera_madrid_hora: float = 0
    obra_asignada_id: Optional[int] = None
    fecha: Optional[str] = None

class SeleccionarObra(BaseModel):
    empleado_id: int
    empleado_nombre: str
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
    fecha: Optional[str] = None

class CheckInReq(BaseModel):
    empleado_id: int
    empleado_nombre: str
    obra_id: int
    obra_nombre: str
    hora: Optional[str] = None

class CheckOutReq(BaseModel):
    empleado_id: int
    empleado_nombre: str
    coste_hora: float = 0
    fuera_madrid_hora: float = 0


# ============ DB HELPERS ============

async def db_get(path):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{SUPABASE_URL}/rest/v1/{path}", headers=HG)
        return r.json() if r.status_code == 200 else []

async def db_post(table, data):
    async with httpx.AsyncClient() as c:
        r = await c.post(f"{SUPABASE_URL}/rest/v1/{table}", headers=H, json=data)
        return r.json() if r.status_code in (200, 201) else {"error": r.text}

async def db_patch(table, filtro, data):
    async with httpx.AsyncClient() as c:
        r = await c.patch(f"{SUPABASE_URL}/rest/v1/{table}?{filtro}", headers=H, json=data)
        return r.json() if r.status_code == 200 else {"error": r.text}

async def whatsapp(tel, texto):
    async with httpx.AsyncClient() as c:
        await c.post(f"{EVO_URL}/message/sendText/EuromirBia",
            headers={"apikey": EVO_KEY, "Content-Type": "application/json"},
            json={"number": f"{tel}@s.whatsapp.net", "text": texto})


# ============ BUSINESS LOGIC ============

async def get_obras():
    return await db_get("obras?estado=eq.En curso&select=id,nombre,fuera_madrid&limit=50")

async def get_fichajes(emp_id, fecha):
    return await db_get(f"fichajes_tramos?empleado_id=eq.{emp_id}&fecha=eq.{fecha}&select=id,hora_inicio,hora_fin,horas_decimal,obra_nombre,estado&estado=in.(BORRADOR,CONFIRMADO)")

async def get_checkin_abierto(emp_id):
    """Busca si el empleado tiene un check-in sin cerrar"""
    emp = await db_get(f"empleados?id=eq.{emp_id}&select=notas_bia")
    if emp:
        notas = emp[0].get("notas_bia", "") or ""
        m = re.search(r'CHECK-IN: (\d{2}:\d{2}) en (.+) el (\d{4}-\d{2}-\d{2})', notas)
        if m:
            return {"hora": m.group(1), "obra": m.group(2), "fecha": m.group(3)}
    return None

async def guardar_checkin(emp_id, hora, obra_nombre, fecha):
    """Guarda check-in en notas_bia del empleado"""
    emp = await db_get(f"empleados?id=eq.{emp_id}&select=notas_bia")
    notas_actuales = emp[0].get("notas_bia", "") if emp else ""
    # Preservar notas existentes (quitar check-in anterior si hay)
    notas_sin_checkin = re.sub(r'CHECK-IN: .+', '', notas_actuales).strip()
    nuevas_notas = f"{notas_sin_checkin}\nCHECK-IN: {hora} en {obra_nombre} el {fecha}".strip()
    await db_patch("empleados", f"id=eq.{emp_id}", {"notas_bia": nuevas_notas})

async def borrar_checkin(emp_id):
    """Borra el check-in de las notas_bia"""
    emp = await db_get(f"empleados?id=eq.{emp_id}&select=notas_bia")
    notas = emp[0].get("notas_bia", "") if emp else ""
    notas_limpias = re.sub(r'\nCHECK-IN: .+', '', notas).strip()
    notas_limpias = re.sub(r'CHECK-IN: .+', '', notas_limpias).strip()
    await db_patch("empleados", f"id=eq.{emp_id}", {"notas_bia": notas_limpias})

def hay_solape(fichajes, h_inicio, h_fin):
    """Verifica si hay solape con fichajes existentes"""
    for f in fichajes:
        fi = str(f.get("hora_inicio", ""))[:5]
        ff = str(f.get("hora_fin", ""))[:5]
        if fi and ff and h_inicio < ff and h_fin > fi:
            return f
    return None

def parse_horas(texto):
    t = texto.lower().strip()
    m = re.search(r'de\s+(\d{1,2})[:\.]?(\d{2})?\s+a\s+(\d{1,2})[:\.]?(\d{2})?', t)
    if m:
        h1, m1 = int(m.group(1)), int(m.group(2) or 0)
        h2, m2 = int(m.group(3)), int(m.group(4) or 0)
        return {"hora_inicio": f"{h1:02d}:{m1:02d}", "hora_fin": f"{h2:02d}:{m2:02d}", "horas": ((h2*60+m2)-(h1*60+m1))/60}
    m = re.search(r'(\d{1,2})[:\.]?(\d{2})?\s*[-–]\s*(\d{1,2})[:\.]?(\d{2})?', t)
    if m:
        h1, m1 = int(m.group(1)), int(m.group(2) or 0)
        h2, m2 = int(m.group(3)), int(m.group(4) or 0)
        return {"hora_inicio": f"{h1:02d}:{m1:02d}", "hora_fin": f"{h2:02d}:{m2:02d}", "horas": ((h2*60+m2)-(h1*60+m1))/60}
    m = re.search(r'(\d+)\s*h', t)
    if m:
        return {"horas": int(m.group(1))}
    return {}

def detectar_intencion(texto):
    t = texto.lower().strip()
    if any(w in t for w in ["empiezo", "entrada", "ya estoy", "llego", "inicio jornada", "empezar", "estoy en"]):
        return "check_in"
    if any(w in t for w in ["salgo", "termino", "acabé", "acabo", "salida", "me voy", "fin jornada", "ya terminé"]):
        return "check_out"
    if any(w in t for w in ["trabajé", "trabaje", "ficho", "fichar"]) or re.search(r'\d+\s*h', t) or re.search(r'de\s+\d+\s+a\s+\d+', t):
        return "fichar_horas"
    return "desconocido"

def formato_obras(obras):
    return "\n".join([f"{i+1}. *{o['nombre']}* {'🌍' if o.get('fuera_madrid') else '🏗️'}" for i, o in enumerate(obras)])


# ============ ENDPOINTS ============

@app.post("/procesar-fichaje")
async def procesar_fichaje(req: ProcesarMsg):
    fecha = req.fecha or datetime.now().strftime("%Y-%m-%d")
    hoy = datetime.now().strftime("%Y-%m-%d")
    intencion = detectar_intencion(req.mensaje)

    # VERIFICAR CHECK-IN ABIERTO
    checkin = await get_checkin_abierto(req.empleado_id)

    # ===== CHECK-IN =====
    if intencion == "check_in":
        if checkin:
            return {"accion": "error", "mensaje": f"⚠️ Ya tienes una jornada abierta desde las {checkin['hora']} en *{checkin['obra']}*. Primero dime 'salgo' para cerrarla."}

        obras = await get_obras()
        horas = parse_horas(req.mensaje)
        return {
            "accion": "pedir_obra_checkin",
            "mensaje": f"¿En qué obra? 🏗️\n\n{formato_obras(obras)}\n\nDime número o nombre 😊",
            "obras": obras,
            "hora_declarada_fin": horas.get("hora_fin")  # Si dijo "de 9 a 18", guardamos el fin previsto
        }

    # ===== CHECK-OUT =====
    if intencion == "check_out":
        if not checkin:
            return {"accion": "error", "mensaje": "🤔 No tienes ninguna jornada abierta. Dime tus horas directamente (ej: 'de 9 a 17')"}

        return {
            "accion": "check_out_confirmar",
            "mensaje": "check_out",
            "datos": {"hora_entrada": checkin["hora"], "obra": checkin["obra"], "fecha_checkin": checkin["fecha"]}
        }

    # ===== FICHAR HORAS COMPLETAS =====
    if intencion == "fichar_horas":
        if checkin:
            return {"accion": "error", "mensaje": f"⚠️ Tienes jornada abierta desde las {checkin['hora']} en *{checkin['obra']}*. Dime 'salgo' para cerrarla antes de fichar horas nuevas."}

        horas = parse_horas(req.mensaje)
        if not horas.get("horas") and not horas.get("hora_inicio"):
            return {"accion": "pedir_horas", "mensaje": "¿Cuántas horas? Dime 'de 9 a 17' o '8 horas' 🕐"}

        # Comida
        h = horas.get("horas", 0)
        netas = h - 1 if h > 6 else h
        horas["horas_netas"] = round(netas, 1)

        # Verificar duplicados
        fichajes = await get_fichajes(req.empleado_id, fecha)
        if fichajes:
            existentes = ", ".join([f"{str(f.get('hora_inicio',''))[:5]}-{str(f.get('hora_fin',''))[:5]} ({f.get('obra_nombre','')})" for f in fichajes])
            return {"accion": "duplicado", "mensaje": f"Ya tienes fichaje de {'hoy' if fecha == hoy else fecha}: {existentes}. ¿Añadir otro tramo? 🤔", "datos": horas}

        # Verificar solape
        if horas.get("hora_inicio") and horas.get("hora_fin") and fichajes:
            solape = hay_solape(fichajes, horas["hora_inicio"], horas["hora_fin"])
            if solape:
                return {"accion": "error", "mensaje": f"❌ Se solapa con tu fichaje de {str(solape.get('hora_inicio',''))[:5]}-{str(solape.get('hora_fin',''))[:5]} en {solape.get('obra_nombre','')}"}

        obras = await get_obras()
        return {
            "accion": "pedir_obra",
            "mensaje": f"OK, {netas}h netas. ¿En qué obra? 🏗️\n\n{formato_obras(obras)}\n\nDime número o nombre 😊",
            "datos": horas,
            "obras": obras
        }

    return {"accion": "desconocido", "mensaje": "No entendí. Dime 'empiezo' para entrada, 'salgo' para salida, o tus horas (ej: 'de 9 a 17') 😊"}


@app.post("/check-in")
async def check_in(req: CheckInReq):
    # Verificar que no hay check-in abierto
    checkin = await get_checkin_abierto(req.empleado_id)
    if checkin:
        return {"success": False, "mensaje": f"⚠️ Ya tienes jornada abierta desde {checkin['hora']} en *{checkin['obra']}*"}

    hora = req.hora or datetime.now().strftime("%H:%M")
    fecha = datetime.now().strftime("%Y-%m-%d")
    await guardar_checkin(req.empleado_id, hora, req.obra_nombre, fecha)

    return {"success": True, "mensaje": f"✅ Entrada a las {hora} en *{req.obra_nombre}* 🏗️ Avísame cuando salgas 👍", "hora": hora}


@app.post("/check-out")
async def check_out(req: CheckOutReq):
    checkin = await get_checkin_abierto(req.empleado_id)
    if not checkin:
        return {"success": False, "mensaje": "🤔 No tienes jornada abierta"}

    hora_salida = datetime.now().strftime("%H:%M")
    h1, m1 = map(int, checkin["hora"].split(":"))
    h2, m2 = map(int, hora_salida.split(":"))
    total_min = (h2 * 60 + m2) - (h1 * 60 + m1)
    horas = total_min / 60
    netas = round(horas - 1 if horas > 6 else horas, 1)

    # Buscar obra para saber fuera_madrid y tarifa
    obra_nombre = checkin["obra"]
    obras = await db_get(f"obras?nombre=ilike.*{obra_nombre.split()[0]}*&select=id,nombre,fuera_madrid&limit=1")
    obra = obras[0] if obras else {}
    fuera = obra.get("fuera_madrid", False)
    tarifa = req.fuera_madrid_hora if fuera else req.coste_hora
    coste = round(netas * tarifa, 2)

    datos = {
        "fecha": checkin["fecha"],
        "empleado_id": req.empleado_id, "empleado_nombre": req.empleado_nombre,
        "obra_id": obra.get("id"), "obra_nombre": obra_nombre,
        "hora_inicio": checkin["hora"], "hora_fin": hora_salida,
        "horas_decimal": netas, "coste_hora": tarifa, "coste_total": coste,
        "estado": "BORRADOR", "tipo_dia": "LABORABLE", "fuera_madrid": fuera
    }
    result = await db_post("fichajes_tramos", datos)
    await borrar_checkin(req.empleado_id)

    return {"success": True, "mensaje": f"✅ {netas}h netas ({checkin['hora']}-{hora_salida}{',-1h comida' if horas>6 else ''}). Borrador en *{obra_nombre}*. Tu encargado lo revisará 👍", "fichaje": result}


@app.post("/seleccionar-obra")
async def seleccionar_obra(req: SeleccionarObra):
    fecha = req.fecha or datetime.now().strftime("%Y-%m-%d")
    tarifa = req.fuera_madrid_hora if req.fuera_madrid else req.coste_hora
    total = round(req.horas_decimal * tarifa, 2)

    datos = {
        "fecha": fecha, "empleado_id": req.empleado_id, "empleado_nombre": req.empleado_nombre,
        "obra_id": req.obra_id, "obra_nombre": req.obra_nombre,
        "hora_inicio": req.hora_inicio, "hora_fin": req.hora_fin, "horas_decimal": req.horas_decimal,
        "coste_hora": tarifa, "coste_total": total, "estado": "BORRADOR",
        "tipo_dia": "LABORABLE", "fuera_madrid": req.fuera_madrid
    }
    result = await db_post("fichajes_tramos", datos)
    if isinstance(result, dict) and "error" in result:
        return {"success": False, "mensaje": "Error al registrar", "error": str(result)}

    return {"success": True, "mensaje": f"✅ Borrador: *{req.empleado_nombre}* {req.hora_inicio}-{req.hora_fin} ({req.horas_decimal}h) en *{req.obra_nombre}*. Tu encargado lo revisará 👍"}


@app.post("/confirmar-fichajes")
async def confirmar_fichajes(req: ConfirmarFichajes):
    resp = req.respuesta.lower().strip()
    fecha = req.fecha or datetime.now().strftime("%Y-%m-%d")

    filtro = f"estado=eq.BORRADOR&fecha=eq.{fecha}&select=id,empleado_id,empleado_nombre,obra_id,obra_nombre,hora_inicio,hora_fin,horas_decimal,coste_hora,fuera_madrid"
    if req.obra_id:
        filtro += f"&obra_id=eq.{req.obra_id}"

    borradores = await db_get(f"fichajes_tramos?{filtro}")
    if not borradores:
        return {"success": True, "mensaje": "No hay fichajes pendientes", "confirmados": 0}

    confirmados, rechazados = [], []

    if any(w in resp for w in ["todos", "ok", "confirmo"]) or resp in ("si", "sí", "✅"):
        confirmados = borradores
    elif "rechaz" in resp or resp in ("no", "❌"):
        m = re.search(r'(\d+)', resp)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(borradores):
                rechazados = [borradores[idx]]
                confirmados = [b for i, b in enumerate(borradores) if i != idx]
        else:
            rechazados = borradores
    else:
        confirmados = borradores

    for f in confirmados:
        await db_patch("fichajes_tramos", f"id=eq.{f['id']}", {"estado": "CONFIRMADO"})
        emp = await db_get(f"empleados?id=eq.{f['empleado_id']}&select=telefono")
        if emp and emp[0].get("telefono"):
            await whatsapp(emp[0]["telefono"], f"✅ *{f.get('empleado_nombre','')}*, fichaje confirmado: {str(f.get('hora_inicio',''))[:5]}-{str(f.get('hora_fin',''))[:5]} ({f.get('horas_decimal',0)}h)")

    for f in rechazados:
        await db_patch("fichajes_tramos", f"id=eq.{f['id']}", {"estado": "RECHAZADO"})

    nombres = "\n".join([f"• *{f.get('empleado_nombre','')}* {str(f.get('hora_inicio',''))[:5]}-{str(f.get('hora_fin',''))[:5]} ({f.get('horas_decimal',0)}h)" for f in confirmados])
    await whatsapp(ADMIN_TEL, f"📊 *Fichajes confirmados*:\n\n{nombres}\n\n✅ Total: {len(confirmados)}")

    return {"success": True, "confirmados": len(confirmados), "rechazados": len(rechazados)}


@app.get("/estado-empleado/{empleado_id}")
async def estado_empleado(empleado_id: int):
    """Consulta el estado de fichaje de un empleado"""
    checkin = await get_checkin_abierto(empleado_id)
    hoy = datetime.now().strftime("%Y-%m-%d")
    fichajes = await get_fichajes(empleado_id, hoy)

    return {
        "checkin_abierto": checkin,
        "fichajes_hoy": fichajes,
        "tiene_jornada_abierta": checkin is not None,
        "total_horas_hoy": sum(f.get("horas_decimal", 0) for f in fichajes)
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "euromir-fichajes", "version": "3.0"}
