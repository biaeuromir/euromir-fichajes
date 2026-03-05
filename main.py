from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional, List
import re
import httpx
from datetime import datetime, timedelta

app = FastAPI(title="Euromir Fichajes API", version="4.0")

SUPABASE_URL = "https://yqpnrouipdikduvuxhsa.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlxcG5yb3VpcGRpa2R1dnV4aHNhIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzEwMDUwMjgsImV4cCI6MjA4NjU4MTAyOH0.A08W9dh1x4AYM79tdz60GnE_z1lHJZLPYo7Dht9VrI8"
EVO_URL = "https://euromirbot-evolution-api.wp2z39.easypanel.host"
EVO_KEY = "C4811CA688A3-4C84-99B7-5206E31CD979"
ADMIN_TEL = "34677716161"
H = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "return=representation"}
HG = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}

# Pending fichajes: employee selected hours, waiting for obra selection
pending_fichajes = {}  # {empleado_id: {"hora_inicio", "hora_fin", "horas_netas"}}


class ProcesarMsg(BaseModel):
    mensaje: str
    empleado_id: int
    empleado_nombre: str
    empleado_telefono: str = ""
    coste_hora: float = 0
    fuera_madrid_hora: float = 0
    obra_asignada_id: Optional[int] = None
    fecha: Optional[str] = None

class ConfirmarFichajes(BaseModel):
    respuesta: str
    obra_id: Optional[int] = None
    fecha: Optional[str] = None

class CheckOutReq(BaseModel):
    empleado_id: int
    empleado_nombre: str
    coste_hora: float = 0
    fuera_madrid_hora: float = 0


# ============ DB ============
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

async def get_obras():
    return await db_get("obras?estado=eq.En curso&select=id,nombre,fuera_madrid&limit=50")

async def get_fichajes(emp_id, fecha):
    return await db_get(f"fichajes_tramos?empleado_id=eq.{emp_id}&fecha=eq.{fecha}&select=id,hora_inicio,hora_fin,horas_decimal,obra_nombre,estado&estado=in.(BORRADOR,CONFIRMADO)")

async def get_checkin(emp_id):
    emp = await db_get(f"empleados?id=eq.{emp_id}&select=notas_bia")
    if emp:
        notas = emp[0].get("notas_bia", "") or ""
        m = re.search(r'CHECK-IN: (\d{2}:\d{2}) en (.+) el (\d{4}-\d{2}-\d{2})', notas)
        if m: return {"hora": m.group(1), "obra": m.group(2), "fecha": m.group(3)}
    return None

async def guardar_checkin(emp_id, hora, obra, fecha):
    emp = await db_get(f"empleados?id=eq.{emp_id}&select=notas_bia")
    notas = emp[0].get("notas_bia", "") if emp else ""
    notas = re.sub(r'\nCHECK-IN: .+', '', notas).strip()
    notas = re.sub(r'CHECK-IN: .+', '', notas).strip()
    await db_patch("empleados", f"id=eq.{emp_id}", {"notas_bia": f"{notas}\nCHECK-IN: {hora} en {obra} el {fecha}".strip()})

async def borrar_checkin(emp_id):
    emp = await db_get(f"empleados?id=eq.{emp_id}&select=notas_bia")
    notas = emp[0].get("notas_bia", "") if emp else ""
    notas = re.sub(r'\nCHECK-IN: .+', '', notas).strip()
    notas = re.sub(r'CHECK-IN: .+', '', notas).strip()
    await db_patch("empleados", f"id=eq.{emp_id}", {"notas_bia": notas})


# ============ PARSING ============
def parse_horas(texto):
    t = texto.lower().strip()
    
    # "de 8 a 15:30", "de 9 a 17"
    m = re.search(r'de\s+(\d{1,2})[:\.]?(\d{2})?\s+a\s+(?:las\s+)?(\d{1,2})[:\.]?(\d{2})?', t)
    if m:
        h1, m1, h2, m2 = int(m.group(1)), int(m.group(2) or 0), int(m.group(3)), int(m.group(4) or 0)
        return {"hora_inicio": f"{h1:02d}:{m1:02d}", "hora_fin": f"{h2:02d}:{m2:02d}", "horas": ((h2*60+m2)-(h1*60+m1))/60}
    
    # "a las 8 y salgo a las 15:30", "empiezo a las 8 salgo 15:30"
    m = re.search(r'(?:a las|las)\s+(\d{1,2})[:\.]?(\d{2})?\s*(?:y|,)?\s*(?:salgo|termino|acabo|hasta|salida)\s*(?:a las|las)?\s*(\d{1,2})[:\.]?(\d{2})?', t)
    if m:
        h1, m1, h2, m2 = int(m.group(1)), int(m.group(2) or 0), int(m.group(3)), int(m.group(4) or 0)
        return {"hora_inicio": f"{h1:02d}:{m1:02d}", "hora_fin": f"{h2:02d}:{m2:02d}", "horas": ((h2*60+m2)-(h1*60+m1))/60}
    
    # "empiezo 8 salgo 15:30" (sin "a las")
    m = re.search(r'(?:empiezo|entrada|llego)\s*(?:a las)?\s*(\d{1,2})[:\.]?(\d{2})?.*?(?:salgo|termino|hasta)\s*(?:a las)?\s*(\d{1,2})[:\.]?(\d{2})?', t)
    if m:
        h1, m1, h2, m2 = int(m.group(1)), int(m.group(2) or 0), int(m.group(3)), int(m.group(4) or 0)
        return {"hora_inicio": f"{h1:02d}:{m1:02d}", "hora_fin": f"{h2:02d}:{m2:02d}", "horas": ((h2*60+m2)-(h1*60+m1))/60}
    
    # "9-17", "8:00-15:30"
    m = re.search(r'(\d{1,2})[:\.]?(\d{2})?\s*[-–]\s*(\d{1,2})[:\.]?(\d{2})?', t)
    if m:
        h1, m1, h2, m2 = int(m.group(1)), int(m.group(2) or 0), int(m.group(3)), int(m.group(4) or 0)
        return {"hora_inicio": f"{h1:02d}:{m1:02d}", "hora_fin": f"{h2:02d}:{m2:02d}", "horas": ((h2*60+m2)-(h1*60+m1))/60}
    
    # "8 horas", "8h"
    m = re.search(r'(\d+)\s*h', t)
    if m: return {"horas": int(m.group(1))}
    
    return {}

def detectar_intencion(texto):
    t = texto.lower().strip()
    if any(w in t for w in ["empiezo", "entrada", "ya estoy", "llego", "inicio jornada"]): return "check_in"
    if any(w in t for w in ["salgo", "termino", "acabé", "acabo", "salida", "me voy"]): return "check_out"
    if any(w in t for w in ["trabajé", "trabaje", "ficho", "fichar"]) or re.search(r'\d+\s*h', t) or re.search(r'de\s+\d+\s+a\s+\d+', t): return "fichar_horas"
    return "desconocido"

def formato_obras(obras):
    return "\n".join([f"{i+1}. *{o['nombre']}* {'🌍' if o.get('fuera_madrid') else '🏗️'}" for i, o in enumerate(obras)])

def hay_solape(fichajes, hi, hf):
    for f in fichajes:
        fi, ff = str(f.get("hora_inicio",""))[:5], str(f.get("hora_fin",""))[:5]
        if fi and ff and hi < ff and hf > fi: return f
    return None


# ============ MAIN ENDPOINT ============
@app.post("/procesar-fichaje")
async def procesar_fichaje(req: ProcesarMsg):
    fecha = req.fecha or datetime.now().strftime("%Y-%m-%d")
    hoy = datetime.now().strftime("%Y-%m-%d")
    checkin = await get_checkin(req.empleado_id)
    obras = await get_obras()
    
    # ===== OBRA SELECTION? (employee responds with number/name after seeing list) =====
    if req.empleado_id in pending_fichajes:
        pending = pending_fichajes[req.empleado_id]
        msg = req.mensaje.strip()
        obra = None
        
        # By number
        m = re.search(r'^(\d+)$', msg)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(obras): obra = obras[idx]
        
        # By name
        if not obra:
            for o in obras:
                if o["nombre"].lower() in msg.lower() or msg.lower() in o["nombre"].lower():
                    obra = o; break
                palabras = o["nombre"].lower().split()
                if sum(1 for p in palabras if p in msg.lower()) >= 2:
                    obra = o; break
        
        if obra:
            tarifa = req.fuera_madrid_hora if obra.get("fuera_madrid") else req.coste_hora
            netas = pending["horas_netas"]
            datos = {
                "fecha": fecha, "empleado_id": req.empleado_id, "empleado_nombre": req.empleado_nombre,
                "obra_id": obra["id"], "obra_nombre": obra["nombre"],
                "hora_inicio": pending["hora_inicio"], "hora_fin": pending["hora_fin"],
                "horas_decimal": netas, "coste_hora": tarifa, "coste_total": round(netas * tarifa, 2),
                "estado": "BORRADOR", "tipo_dia": "LABORABLE", "fuera_madrid": obra.get("fuera_madrid", False)
            }
            result = await db_post("fichajes_tramos", datos)
            del pending_fichajes[req.empleado_id]
            if isinstance(result, dict) and "error" in result:
                return {"accion": "error", "mensaje": f"Error al registrar: {result.get('error','')}"}
            return {"accion": "registrado", "mensaje": f"✅ Borrador: *{req.empleado_nombre}* {pending['hora_inicio']}-{pending['hora_fin']} ({netas}h) en *{obra['nombre']}*{'🌍' if obra.get('fuera_madrid') else '🏗️'}. Tu encargado lo revisará 👍"}
    
    # ===== CHECK-IN =====
    intencion = detectar_intencion(req.mensaje)
    
    # Also detect "empiezo a las 8 y salgo a las 15" as fichar_horas (not just check_in)
    horas = parse_horas(req.mensaje)
    if horas.get("hora_inicio") and horas.get("hora_fin"):
        intencion = "fichar_horas"  # Override: if both times given, it's a full fichaje
    
    if intencion == "check_in":
        if checkin:
            return {"accion": "error", "mensaje": f"⚠️ Ya tienes jornada abierta desde {checkin['hora']} en *{checkin['obra']}*. Dime 'salgo' primero."}
        return {"accion": "pedir_obra_checkin", "mensaje": f"¿En qué obra empiezas? 🏗️\n\n{formato_obras(obras)}\n\nDime número o nombre 😊", "obras": obras}
    
    if intencion == "check_out":
        if not checkin:
            return {"accion": "error", "mensaje": "🤔 No tienes jornada abierta. Dime tus horas directamente."}
        return {"accion": "check_out", "datos": {"hora_entrada": checkin["hora"], "obra": checkin["obra"], "fecha_checkin": checkin["fecha"]}}
    
    if intencion == "fichar_horas":
        if checkin:
            return {"accion": "error", "mensaje": f"⚠️ Tienes jornada abierta desde {checkin['hora']} en *{checkin['obra']}*. Dime 'salgo' primero."}
        
        if not horas.get("horas") and not horas.get("hora_inicio"):
            return {"accion": "pedir_horas", "mensaje": "¿Cuántas horas? Dime 'de 9 a 17' o '8 horas' 🕐"}
        
        h = horas.get("horas", 0)
        netas = round(h - 1 if h > 6 else h, 1)
        
        fichajes = await get_fichajes(req.empleado_id, fecha)
        if fichajes:
            return {"accion": "duplicado", "mensaje": f"Ya tienes fichaje de {'hoy' if fecha==hoy else fecha}. ¿Añadir otro tramo? 🤔"}
        
        if horas.get("hora_inicio") and horas.get("hora_fin") and fichajes:
            solape = hay_solape(fichajes, horas["hora_inicio"], horas["hora_fin"])
            if solape: return {"accion": "error", "mensaje": f"❌ Se solapa con {str(solape.get('hora_inicio',''))[:5]}-{str(solape.get('hora_fin',''))[:5]}"}
        
        # Save pending and ask for obra
        if horas.get("hora_inicio") and horas.get("hora_fin"):
            pending_fichajes[req.empleado_id] = {"hora_inicio": horas["hora_inicio"], "hora_fin": horas["hora_fin"], "horas_netas": netas}
        
        return {"accion": "pedir_obra", "mensaje": f"OK, {netas}h netas. ¿En qué obra? 🏗️\n\n{formato_obras(obras)}\n\nDime número o nombre 😊", "datos": horas, "obras": obras}
    
    return {"accion": "desconocido", "mensaje": "No entendí. Dime 'empiezo' para entrada, 'salgo' para salida, o tus horas (ej: 'de 9 a 17') 😊"}


@app.post("/check-out")
async def check_out(req: CheckOutReq):
    checkin = await get_checkin(req.empleado_id)
    if not checkin: return {"success": False, "mensaje": "🤔 No tienes jornada abierta"}
    hora_salida = datetime.now().strftime("%H:%M")
    h1, m1 = map(int, checkin["hora"].split(":"))
    h2, m2 = map(int, hora_salida.split(":"))
    horas = ((h2*60+m2)-(h1*60+m1))/60
    netas = round(horas-1 if horas>6 else horas, 1)
    obras = await db_get(f"obras?nombre=ilike.*{checkin['obra'].split()[0]}*&select=id,nombre,fuera_madrid&limit=1")
    obra = obras[0] if obras else {}
    fuera = obra.get("fuera_madrid", False)
    tarifa = req.fuera_madrid_hora if fuera else req.coste_hora
    datos = {"fecha": checkin["fecha"], "empleado_id": req.empleado_id, "empleado_nombre": req.empleado_nombre,
        "obra_id": obra.get("id"), "obra_nombre": checkin["obra"],
        "hora_inicio": checkin["hora"], "hora_fin": hora_salida, "horas_decimal": netas,
        "coste_hora": tarifa, "coste_total": round(netas*tarifa,2), "estado": "BORRADOR", "tipo_dia": "LABORABLE", "fuera_madrid": fuera}
    await db_post("fichajes_tramos", datos)
    await borrar_checkin(req.empleado_id)
    return {"success": True, "mensaje": f"✅ {netas}h netas ({checkin['hora']}-{hora_salida}). Borrador en *{checkin['obra']}* 👍"}


@app.post("/confirmar-fichajes")
async def confirmar_fichajes(req: ConfirmarFichajes):
    resp = req.respuesta.lower().strip()
    fecha = req.fecha or datetime.now().strftime("%Y-%m-%d")
    filtro = f"estado=eq.BORRADOR&fecha=eq.{fecha}&select=id,empleado_id,empleado_nombre,obra_id,obra_nombre,hora_inicio,hora_fin,horas_decimal,coste_hora,fuera_madrid"
    if req.obra_id: filtro += f"&obra_id=eq.{req.obra_id}"
    borradores = await db_get(f"fichajes_tramos?{filtro}")
    if not borradores: return {"success": True, "mensaje": "No hay fichajes pendientes", "confirmados": 0}
    confirmados, rechazados = [], []
    if any(w in resp for w in ["todos","ok","confirmo"]) or resp in ("si","sí","✅"): confirmados = borradores
    elif "rechaz" in resp or resp in ("no","❌"):
        m = re.search(r'(\d+)', resp)
        if m:
            idx = int(m.group(1))-1
            if 0<=idx<len(borradores): rechazados=[borradores[idx]]; confirmados=[b for i,b in enumerate(borradores) if i!=idx]
        else: rechazados = borradores
    else: confirmados = borradores
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
    checkin = await get_checkin(empleado_id)
    fichajes = await get_fichajes(empleado_id, datetime.now().strftime("%Y-%m-%d"))
    return {"checkin_abierto": checkin, "fichajes_hoy": fichajes, "tiene_jornada_abierta": checkin is not None,
        "total_horas_hoy": sum(f.get("horas_decimal",0) for f in fichajes),
        "tiene_pending": empleado_id in pending_fichajes}

@app.get("/health")
async def health():
    return {"status": "ok", "service": "euromir-fichajes", "version": "4.0"}
