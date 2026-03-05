from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import re
import httpx
from datetime import datetime, timedelta

app = FastAPI(title="Euromir Fichajes API", version="1.0")

# Config
SUPABASE_URL = "https://yqpnrouipdikduvuxhsa.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlxcG5yb3VpcGRpa2R1dnV4aHNhIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzEwMDUwMjgsImV4cCI6MjA4NjU4MTAyOH0.A08W9dh1x4AYM79tdz60GnE_z1lHJZLPYo7Dht9VrI8"
HEADERS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}

# Models
class ProcesarFichajeRequest(BaseModel):
    mensaje: str
    empleado_id: int
    empleado_nombre: str
    empleado_telefono: str = ""
    coste_hora: float = 0
    fuera_madrid_hora: float = 0
    obra_asignada_id: Optional[int] = None
    fecha: Optional[str] = None  # YYYY-MM-DD, default hoy

class ProcesarFichajeResponse(BaseModel):
    accion: str  # registrar_fichaje, pedir_obra, pedir_horas, check_in, check_out, duplicado, error
    mensaje: str  # Mensaje para enviar al empleado
    datos: Optional[dict] = None  # Datos estructurados del fichaje
    obras_disponibles: Optional[List[dict]] = None  # Lista de obras para elegir

class ConfirmarRequest(BaseModel):
    respuesta: str  # "todos ok", "rechazar 2", etc.
    fichajes_ids: List[int]
    encargado_telefono: str

# Helpers
def parse_horas(texto: str) -> dict:
    """Parsea horas de un mensaje de texto"""
    texto = texto.lower().strip()
    result = {"hora_inicio": None, "hora_fin": None, "horas": None}
    
    # "de 9 a 17", "de 7 a 15", "de 09:00 a 18:00"
    m = re.search(r'de\s+(\d{1,2})[:\.]?(\d{2})?\s+a\s+(\d{1,2})[:\.]?(\d{2})?', texto)
    if m:
        h1 = int(m.group(1))
        m1 = int(m.group(2) or 0)
        h2 = int(m.group(3))
        m2 = int(m.group(4) or 0)
        result["hora_inicio"] = f"{h1:02d}:{m1:02d}"
        result["hora_fin"] = f"{h2:02d}:{m2:02d}"
        total_min = (h2 * 60 + m2) - (h1 * 60 + m1)
        result["horas"] = total_min / 60
        return result
    
    # "9-17", "7-15", "09:00-18:00"  
    m = re.search(r'(\d{1,2})[:\.]?(\d{2})?\s*[-–]\s*(\d{1,2})[:\.]?(\d{2})?', texto)
    if m:
        h1 = int(m.group(1))
        m1 = int(m.group(2) or 0)
        h2 = int(m.group(3))
        m2 = int(m.group(4) or 0)
        result["hora_inicio"] = f"{h1:02d}:{m1:02d}"
        result["hora_fin"] = f"{h2:02d}:{m2:02d}"
        total_min = (h2 * 60 + m2) - (h1 * 60 + m1)
        result["horas"] = total_min / 60
        return result
    
    # "8 horas", "8h"
    m = re.search(r'(\d+)\s*h', texto)
    if m:
        result["horas"] = int(m.group(1))
        return result
    
    return result

def parse_obra(texto: str, obras: list) -> Optional[dict]:
    """Identifica la obra del mensaje"""
    texto_lower = texto.lower().strip()
    
    # Por número: "1", "2", etc
    m = re.search(r'^(\d+)$', texto_lower)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(obras):
            return obras[idx]
    
    # Por nombre parcial
    for obra in obras:
        nombre = obra.get("nombre", "").lower()
        if nombre and (nombre in texto_lower or texto_lower in nombre):
            return obra
        # Match por palabras clave
        palabras = nombre.split()
        matches = sum(1 for p in palabras if p in texto_lower)
        if matches >= 2:
            return obra
    
    return None

def detectar_intencion(texto: str) -> str:
    """Detecta qué quiere hacer el empleado"""
    texto_lower = texto.lower().strip()
    
    # Check-in
    if any(w in texto_lower for w in ["empiezo", "entrada", "ya estoy", "llego", "empezar", "inicio jornada"]):
        return "check_in"
    
    # Check-out
    if any(w in texto_lower for w in ["salgo", "termino", "acabé", "acabo", "salida", "me voy", "fin jornada"]):
        return "check_out"
    
    # Fichar horas
    if any(w in texto_lower for w in ["trabajé", "trabaje", "ficho", "fichar", "horas", "de 9", "de 7", "de 8"]):
        return "fichar_horas"
    
    # Tiene números de horas
    if re.search(r'\d+\s*h', texto_lower) or re.search(r'de\s+\d+\s+a\s+\d+', texto_lower):
        return "fichar_horas"
    
    return "desconocido"

async def get_obras_activas() -> list:
    """Obtiene obras activas de Supabase"""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/obras?estado=eq.En curso&select=id,nombre,fuera_madrid&limit=50",
            headers=HEADERS
        )
        return r.json() if r.status_code == 200 else []

async def get_fichajes_dia(empleado_id: int, fecha: str) -> list:
    """Obtiene fichajes de un empleado para un día"""
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{SUPABASE_URL}/rest/v1/fichajes_tramos?empleado_id=eq.{empleado_id}&fecha=eq.{fecha}&select=id,hora_inicio,hora_fin,horas_decimal,obra_nombre,estado",
            headers=HEADERS
        )
        return r.json() if r.status_code == 200 else []

async def registrar_borrador(datos: dict) -> dict:
    """Registra fichaje borrador en Supabase"""
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{SUPABASE_URL}/rest/v1/fichajes_tramos",
            headers={**HEADERS, "Prefer": "return=representation"},
            json=datos
        )
        return r.json() if r.status_code in (200, 201) else {"error": r.text}


# Endpoints
@app.post("/procesar-fichaje", response_model=ProcesarFichajeResponse)
async def procesar_fichaje(req: ProcesarFichajeRequest):
    fecha = req.fecha or datetime.now().strftime("%Y-%m-%d")
    intencion = detectar_intencion(req.mensaje)
    
    if intencion == "check_in":
        obras = await get_obras_activas()
        return ProcesarFichajeResponse(
            accion="check_in",
            mensaje="¿En qué obra empiezas? 🏗️",
            obras_disponibles=[{"id": o["id"], "nombre": o["nombre"], "fuera_madrid": o.get("fuera_madrid", False)} for o in obras]
        )
    
    if intencion == "check_out":
        return ProcesarFichajeResponse(
            accion="check_out",
            mensaje="check_out",
            datos={"hora_salida": datetime.now().strftime("%H:%M")}
        )
    
    if intencion == "fichar_horas":
        # Parsear horas
        horas = parse_horas(req.mensaje)
        
        if not horas.get("horas") and not horas.get("hora_inicio"):
            return ProcesarFichajeResponse(
                accion="pedir_horas",
                mensaje="¿Cuántas horas has trabajado? Puedes decirme 'de 9 a 17' o '8 horas' 🕐"
            )
        
        # Descontar comida si >6h
        h = horas.get("horas", 0)
        if horas.get("hora_inicio") and horas.get("hora_fin") and h > 6:
            h -= 1
            horas["horas_netas"] = h
        else:
            horas["horas_netas"] = h
        
        # Verificar duplicados
        fichajes_hoy = await get_fichajes_dia(req.empleado_id, fecha)
        if fichajes_hoy:
            existentes = ", ".join([f"{f.get('hora_inicio','')[:5]}-{f.get('hora_fin','')[:5]} en {f.get('obra_nombre','')}" for f in fichajes_hoy])
            return ProcesarFichajeResponse(
                accion="duplicado",
                mensaje=f"Ya tienes fichaje de hoy: {existentes}. ¿Quieres añadir otro tramo? 🤔",
                datos={"fichajes_existentes": fichajes_hoy, **horas}
            )
        
        # Pedir obra
        obras = await get_obras_activas()
        return ProcesarFichajeResponse(
            accion="pedir_obra",
            mensaje=f"OK, {horas['horas_netas']}h netas. ¿En qué obra? 🏗️",
            datos=horas,
            obras_disponibles=[{"id": o["id"], "nombre": o["nombre"], "fuera_madrid": o.get("fuera_madrid", False)} for o in obras]
        )
    
    return ProcesarFichajeResponse(
        accion="desconocido",
        mensaje="No entendí. Puedes decirme tus horas (ej: 'de 9 a 17') o 'empiezo' para fichar entrada 😊"
    )


@app.post("/registrar-fichaje")
async def registrar_fichaje_endpoint(
    empleado_id: int,
    empleado_nombre: str,
    obra_id: int,
    obra_nombre: str,
    hora_inicio: str,
    hora_fin: str,
    horas_decimal: float,
    fecha: Optional[str] = None,
    fuera_madrid: bool = False,
    coste_hora: float = 0,
):
    fecha = fecha or datetime.now().strftime("%Y-%m-%d")
    coste_total = horas_decimal * coste_hora
    tipo_dia = "LABORABLE"  # TODO: check calendar
    
    datos = {
        "fecha": fecha,
        "empleado_id": empleado_id,
        "empleado_nombre": empleado_nombre,
        "obra_id": obra_id,
        "obra_nombre": obra_nombre,
        "hora_inicio": hora_inicio,
        "hora_fin": hora_fin,
        "horas_decimal": horas_decimal,
        "coste_hora": coste_hora,
        "coste_total": coste_total,
        "estado": "BORRADOR",
        "tipo_dia": tipo_dia,
        "fuera_madrid": fuera_madrid
    }
    
    result = await registrar_borrador(datos)
    return {"success": True, "fichaje": result, "mensaje": f"Registrado como borrador: {empleado_nombre} {hora_inicio}-{hora_fin} ({horas_decimal}h) en {obra_nombre} ✅"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "euromir-fichajes", "version": "1.0"}
