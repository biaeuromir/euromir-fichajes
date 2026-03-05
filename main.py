from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional
import re, httpx
from datetime import datetime

app = FastAPI(title="Euromir Fichajes API", version="5.0")

SUPA = "https://yqpnrouipdikduvuxhsa.supabase.co"
SK = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InlxcG5yb3VpcGRpa2R1dnV4aHNhIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzEwMDUwMjgsImV4cCI6MjA4NjU4MTAyOH0.A08W9dh1x4AYM79tdz60GnE_z1lHJZLPYo7Dht9VrI8"
EVO = "https://euromirbot-evolution-api.wp2z39.easypanel.host"
EK = "C4811CA688A3-4C84-99B7-5206E31CD979"
N8N = "https://euromir-n8n.wp2z39.easypanel.host"
ADMIN = "34677716161"
H = {"apikey":SK,"Authorization":f"Bearer {SK}","Content-Type":"application/json","Prefer":"return=representation"}
HG = {"apikey":SK,"Authorization":f"Bearer {SK}"}

pending = {}  # {emp_id: {hora_inicio, hora_fin, horas_netas}}

class Msg(BaseModel):
    mensaje:str; empleado_id:int; empleado_nombre:str; empleado_telefono:str=""; coste_hora:float=0; fuera_madrid_hora:float=0; fecha:Optional[str]=None

class Confirmar(BaseModel):
    respuesta:str; obra_id:Optional[int]=None; fecha:Optional[str]=None

class CheckOutReq(BaseModel):
    empleado_id:int; empleado_nombre:str; coste_hora:float=0; fuera_madrid_hora:float=0

async def db_get(p):
    async with httpx.AsyncClient() as c:
        r=await c.get(f"{SUPA}/rest/v1/{p}",headers=HG); return r.json() if r.status_code==200 else []
async def db_post(t,d):
    async with httpx.AsyncClient() as c:
        r=await c.post(f"{SUPA}/rest/v1/{t}",headers=H,json=d); return r.json() if r.status_code in(200,201) else {"error":r.text}
async def db_patch(t,f,d):
    async with httpx.AsyncClient() as c:
        r=await c.patch(f"{SUPA}/rest/v1/{t}?{f}",headers=H,json=d); return r.json() if r.status_code==200 else {"error":r.text}
async def wa(tel,txt):
    async with httpx.AsyncClient() as c:
        await c.post(f"{EVO}/message/sendText/EuromirBia",headers={"apikey":EK,"Content-Type":"application/json"},json={"number":f"{tel}@s.whatsapp.net","text":txt})
async def call_wf17(data):
    """Calls WF-17 to write to Google Sheets"""
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            await c.post(f"{N8N}/webhook/escribir-fichaje-sheets",headers={"Content-Type":"application/json"},json=data)
    except: pass  # If WF-17 fails, don't block

async def get_obras():
    return await db_get("obras?estado=eq.En curso&select=id,nombre,fuera_madrid&limit=50")
async def get_fichajes(eid,fecha):
    return await db_get(f"fichajes_tramos?empleado_id=eq.{eid}&fecha=eq.{fecha}&select=id,hora_inicio,hora_fin,horas_decimal,obra_nombre,estado&estado=in.(BORRADOR,CONFIRMADO)")
async def get_checkin(eid):
    emp=await db_get(f"empleados?id=eq.{eid}&select=notas_bia")
    if emp:
        m=re.search(r'CHECK-IN: (\d{2}:\d{2}) en (.+) el (\d{4}-\d{2}-\d{2})',emp[0].get("notas_bia","") or "")
        if m: return {"hora":m.group(1),"obra":m.group(2),"fecha":m.group(3)}
    return None
async def guardar_checkin(eid,hora,obra,fecha):
    emp=await db_get(f"empleados?id=eq.{eid}&select=notas_bia")
    notas=emp[0].get("notas_bia","") if emp else ""
    notas=re.sub(r'\n?CHECK-IN: .+','',notas).strip()
    await db_patch("empleados",f"id=eq.{eid}",{"notas_bia":f"{notas}\nCHECK-IN: {hora} en {obra} el {fecha}".strip()})
async def borrar_checkin(eid):
    emp=await db_get(f"empleados?id=eq.{eid}&select=notas_bia")
    notas=emp[0].get("notas_bia","") if emp else ""
    await db_patch("empleados",f"id=eq.{eid}",{"notas_bia":re.sub(r'\n?CHECK-IN: .+','',notas).strip()})

async def notificar_encargado(obra_id, emp_nombre, hi, hf, netas):
    """Notifica al encargado de la obra sobre un nuevo fichaje"""
    obra_info = await db_get(f"obras?id=eq.{obra_id}&select=encargado_id")
    if obra_info and obra_info[0].get("encargado_id"):
        enc = await db_get(f"empleados?id=eq.{obra_info[0]['encargado_id']}&select=telefono")
        if enc and enc[0].get("telefono"):
            await wa(enc[0]["telefono"], f"📋 Fichaje nuevo:\n👤 *{emp_nombre}*\n🕐 {hi}-{hf} ({netas}h)\n\nPara confirmar: *confirmo {emp_nombre.split()[0]}*")
    await wa(ADMIN, f"📋 Nuevo: *{emp_nombre}* {hi}-{hf} ({netas}h)")

def parse_horas(t):
    t=t.lower().strip()
    m=re.search(r'de\s+(\d{1,2})[:\.]?(\d{2})?\s+a\s+(?:las\s+)?(\d{1,2})[:\.]?(\d{2})?',t)
    if m: h1,m1,h2,m2=int(m.group(1)),int(m.group(2)or 0),int(m.group(3)),int(m.group(4)or 0); return{"hora_inicio":f"{h1:02d}:{m1:02d}","hora_fin":f"{h2:02d}:{m2:02d}","horas":((h2*60+m2)-(h1*60+m1))/60}
    m=re.search(r'(?:a las|las)\s+(\d{1,2})[:\.]?(\d{2})?\s*(?:y|,)?\s*(?:salgo|termino|acabo|hasta)\s*(?:a las|las)?\s*(\d{1,2})[:\.]?(\d{2})?',t)
    if m: h1,m1,h2,m2=int(m.group(1)),int(m.group(2)or 0),int(m.group(3)),int(m.group(4)or 0); return{"hora_inicio":f"{h1:02d}:{m1:02d}","hora_fin":f"{h2:02d}:{m2:02d}","horas":((h2*60+m2)-(h1*60+m1))/60}
    m=re.search(r'(?:empiezo|entrada|llego)\s*(?:a las)?\s*(\d{1,2})[:\.]?(\d{2})?.*?(?:salgo|termino|hasta)\s*(?:a las)?\s*(\d{1,2})[:\.]?(\d{2})?',t)
    if m: h1,m1,h2,m2=int(m.group(1)),int(m.group(2)or 0),int(m.group(3)),int(m.group(4)or 0); return{"hora_inicio":f"{h1:02d}:{m1:02d}","hora_fin":f"{h2:02d}:{m2:02d}","horas":((h2*60+m2)-(h1*60+m1))/60}
    m=re.search(r'(\d{1,2})[:\.]?(\d{2})?\s*[-–]\s*(\d{1,2})[:\.]?(\d{2})?',t)
    if m: h1,m1,h2,m2=int(m.group(1)),int(m.group(2)or 0),int(m.group(3)),int(m.group(4)or 0); return{"hora_inicio":f"{h1:02d}:{m1:02d}","hora_fin":f"{h2:02d}:{m2:02d}","horas":((h2*60+m2)-(h1*60+m1))/60}
    m=re.search(r'(\d+)\s*h',t)
    if m: return{"horas":int(m.group(1))}
    return{}

def detectar(t):
    t=t.lower().strip()
    if any(w in t for w in["empiezo","entrada","ya estoy","llego","inicio jornada"]):return"check_in"
    if any(w in t for w in["salgo","termino","acabé","acabo","salida","me voy"]):return"check_out"
    if any(w in t for w in["trabajé","trabaje","ficho","fichar"])or re.search(r'\d+\s*h',t)or re.search(r'de\s+\d+\s+a\s+\d+',t):return"fichar"
    return"otro"

def fmt_obras(obras):
    return"\n".join([f"{i+1}. *{o['nombre']}* {'🌍'if o.get('fuera_madrid')else'🏗️'}"for i,o in enumerate(obras)])


@app.post("/procesar-fichaje")
async def procesar_fichaje(req:Msg):
    fecha=req.fecha or datetime.now().strftime("%Y-%m-%d")
    checkin=await get_checkin(req.empleado_id)
    obras=await get_obras()
    horas=parse_horas(req.mensaje)

    # OBRA SELECTION
    if req.empleado_id in pending:
        p=pending[req.empleado_id]
        obra=None
        m=re.search(r'^(\d+)$',req.mensaje.strip())
        if m:
            idx=int(m.group(1))-1
            if 0<=idx<len(obras):obra=obras[idx]
        if not obra:
            for o in obras:
                if o["nombre"].lower()in req.mensaje.lower()or req.mensaje.lower()in o["nombre"].lower():obra=o;break
        if obra:
            tarifa=req.fuera_madrid_hora if obra.get("fuera_madrid")else req.coste_hora
            n=p["horas_netas"]
            datos={"fecha":fecha,"empleado_id":req.empleado_id,"empleado_nombre":req.empleado_nombre,"obra_id":obra["id"],"obra_nombre":obra["nombre"],"hora_inicio":p["hora_inicio"],"hora_fin":p["hora_fin"],"horas_decimal":n,"coste_hora":tarifa,"coste_total":round(n*tarifa,2),"estado":"BORRADOR","tipo_dia":"LABORABLE","fuera_madrid":obra.get("fuera_madrid",False)}
            r=await db_post("fichajes_tramos",datos)
            del pending[req.empleado_id]
            if isinstance(r,dict)and"error"in r:return{"accion":"error","mensaje":"Error al registrar"}
            # NOTIFY ENCARGADO + ADMIN
            await notificar_encargado(obra["id"],req.empleado_nombre,p["hora_inicio"],p["hora_fin"],n)
            return{"accion":"registrado","mensaje":f"✅ Borrador: *{req.empleado_nombre}* {p['hora_inicio']}-{p['hora_fin']} ({n}h) en *{obra['nombre']}*. Tu encargado lo revisará 👍"}

    # Override: if both times given, it's fichar (not check_in)
    intent=detectar(req.mensaje)
    if horas.get("hora_inicio")and horas.get("hora_fin"):intent="fichar"

    if intent=="check_in":
        if checkin:return{"accion":"error","mensaje":f"⚠️ Ya tienes jornada abierta desde {checkin['hora']} en *{checkin['obra']}*. Dime 'salgo' primero."}
        return{"accion":"pedir_obra_checkin","mensaje":f"¿En qué obra empiezas? 🏗️\n\n{fmt_obras(obras)}\n\nDime número o nombre 😊","obras":obras}

    if intent=="check_out":
        if not checkin:return{"accion":"error","mensaje":"🤔 No tienes jornada abierta. Dime tus horas directamente."}
        return{"accion":"check_out","datos":{"hora_entrada":checkin["hora"],"obra":checkin["obra"],"fecha_checkin":checkin["fecha"]}}

    if intent=="fichar":
        if checkin:return{"accion":"error","mensaje":f"⚠️ Jornada abierta desde {checkin['hora']} en *{checkin['obra']}*. Dime 'salgo' primero."}
        if not horas.get("horas")and not horas.get("hora_inicio"):return{"accion":"pedir_horas","mensaje":"¿Cuántas horas? Dime 'de 9 a 17' o '8h' 🕐"}
        h=horas.get("horas",0)
        n=round(h-1 if h>6 else h,1)
        fichajes=await get_fichajes(req.empleado_id,fecha)
        if fichajes:return{"accion":"duplicado","mensaje":"Ya tienes fichaje de hoy. ¿Añadir otro tramo? 🤔"}
        if horas.get("hora_inicio")and horas.get("hora_fin"):
            pending[req.empleado_id]={"hora_inicio":horas["hora_inicio"],"hora_fin":horas["hora_fin"],"horas_netas":n}
        return{"accion":"pedir_obra","mensaje":f"OK, {n}h netas. ¿En qué obra? 🏗️\n\n{fmt_obras(obras)}\n\nDime número o nombre 😊","datos":horas,"obras":obras}

    return{"accion":"otro","mensaje":"No entendí. Dime 'empiezo', 'salgo', o tus horas (ej: 'de 9 a 17') 😊"}


@app.post("/check-out")
async def check_out(req:CheckOutReq):
    ci=await get_checkin(req.empleado_id)
    if not ci:return{"success":False,"mensaje":"🤔 No tienes jornada abierta"}
    hs=datetime.now().strftime("%H:%M")
    h1,m1=map(int,ci["hora"].split(":"));h2,m2=map(int,hs.split(":"))
    h=((h2*60+m2)-(h1*60+m1))/60;n=round(h-1 if h>6 else h,1)
    obras=await db_get(f"obras?nombre=ilike.*{ci['obra'].split()[0]}*&select=id,nombre,fuera_madrid&limit=1")
    obra=obras[0]if obras else{}
    fuera=obra.get("fuera_madrid",False);tarifa=req.fuera_madrid_hora if fuera else req.coste_hora
    datos={"fecha":ci["fecha"],"empleado_id":req.empleado_id,"empleado_nombre":req.empleado_nombre,"obra_id":obra.get("id"),"obra_nombre":ci["obra"],"hora_inicio":ci["hora"],"hora_fin":hs,"horas_decimal":n,"coste_hora":tarifa,"coste_total":round(n*tarifa,2),"estado":"BORRADOR","tipo_dia":"LABORABLE","fuera_madrid":fuera}
    await db_post("fichajes_tramos",datos)
    await borrar_checkin(req.empleado_id)
    if obra.get("id"):await notificar_encargado(obra["id"],req.empleado_nombre,ci["hora"],hs,n)
    return{"success":True,"mensaje":f"✅ {n}h netas ({ci['hora']}-{hs}). Borrador en *{ci['obra']}* 👍"}


@app.post("/confirmar-fichajes")
async def confirmar_fichajes(req:Confirmar):
    resp=req.respuesta.lower().strip();fecha=req.fecha or datetime.now().strftime("%Y-%m-%d")
    filtro=f"estado=eq.BORRADOR&fecha=eq.{fecha}&select=id,empleado_id,empleado_nombre,obra_id,obra_nombre,hora_inicio,hora_fin,horas_decimal,coste_hora,coste_total,fuera_madrid"
    if req.obra_id:filtro+=f"&obra_id=eq.{req.obra_id}"
    borradores=await db_get(f"fichajes_tramos?{filtro}")
    if not borradores:return{"success":True,"mensaje":"No hay fichajes pendientes","confirmados":0}
    conf,rech=[],[]
    if any(w in resp for w in["todos","ok","confirmo"])or resp in("si","sí","✅"):conf=borradores
    elif"rechaz"in resp:
        m=re.search(r'(\d+)',resp)
        if m:idx=int(m.group(1))-1;rech=[borradores[idx]]if 0<=idx<len(borradores)else[];conf=[b for i,b in enumerate(borradores)if i!=idx]
        else:rech=borradores
    else:conf=borradores
    for f in conf:
        await db_patch("fichajes_tramos",f"id=eq.{f['id']}",{"estado":"CONFIRMADO"})
        # Call WF-17 to write to Sheets
        await call_wf17(f)
        emp=await db_get(f"empleados?id=eq.{f['empleado_id']}&select=telefono")
        if emp and emp[0].get("telefono"):
            await wa(emp[0]["telefono"],f"✅ *{f.get('empleado_nombre','')}*, fichaje confirmado: {str(f.get('hora_inicio',''))[:5]}-{str(f.get('hora_fin',''))[:5]} ({f.get('horas_decimal',0)}h)")
    for f in rech:await db_patch("fichajes_tramos",f"id=eq.{f['id']}",{"estado":"RECHAZADO"})
    nombres="\n".join([f"• *{f.get('empleado_nombre','')}* {str(f.get('hora_inicio',''))[:5]}-{str(f.get('hora_fin',''))[:5]}"for f in conf])
    await wa(ADMIN,f"📊 Confirmados:\n{nombres}\n✅ Total: {len(conf)}")
    return{"success":True,"confirmados":len(conf),"rechazados":len(rech)}


@app.get("/estado/{eid}")
async def estado(eid:int):
    ci=await get_checkin(eid);f=await get_fichajes(eid,datetime.now().strftime("%Y-%m-%d"))
    return{"checkin":ci,"fichajes":f,"horas_hoy":sum(x.get("horas_decimal",0)for x in f),"pending":eid in pending}



class ModificarFichaje(BaseModel):
    empleado_nombre: Optional[str] = None
    empleado_id: Optional[int] = None
    fecha: Optional[str] = None
    nueva_hora_inicio: Optional[str] = None
    nueva_hora_fin: Optional[str] = None
    nuevas_horas: Optional[float] = None
    fichaje_id: Optional[int] = None
    motivo: str = "Corrección"

@app.post("/modificar-fichaje")
async def modificar_fichaje(req: ModificarFichaje):
    # Find the fichaje
    fichaje = None
    
    if req.fichaje_id:
        result = await db_get(f"fichajes_tramos?id=eq.{req.fichaje_id}&select=*")
        if result: fichaje = result[0]
    
    if not fichaje and req.empleado_id and req.fecha:
        result = await db_get(f"fichajes_tramos?empleado_id=eq.{req.empleado_id}&fecha=eq.{req.fecha}&select=*&order=id.desc&limit=1")
        if result: fichaje = result[0]
    
    if not fichaje and req.empleado_nombre and req.fecha:
        result = await db_get(f"fichajes_tramos?empleado_nombre=ilike.*{req.empleado_nombre.split()[0]}*&fecha=eq.{req.fecha or datetime.now().strftime('%Y-%m-%d')}&select=*&order=id.desc&limit=1")
        if result: fichaje = result[0]
    
    if not fichaje:
        return {"success": False, "mensaje": "❌ No encontré el fichaje para modificar"}
    
    # Calculate new values
    hi = req.nueva_hora_inicio or str(fichaje.get("hora_inicio",""))[:5]
    hf = req.nueva_hora_fin or str(fichaje.get("hora_fin",""))[:5]
    
    if req.nuevas_horas:
        netas = req.nuevas_horas
    else:
        h1, m1 = map(int, hi.split(":"))
        h2, m2 = map(int, hf.split(":"))
        total = ((h2*60+m2)-(h1*60+m1))/60
        netas = round(total - 1 if total > 6 else total, 1)
    
    tarifa = fichaje.get("coste_hora", 0)
    coste = round(netas * tarifa, 2)
    
    # Update
    update = {
        "hora_inicio": hi,
        "hora_fin": hf,
        "horas_decimal": netas,
        "coste_total": coste
    }
    await db_patch("fichajes_tramos", f"id=eq.{fichaje['id']}", update)
    
    emp_nombre = fichaje.get("empleado_nombre", "Empleado")
    
    # Notify employee
    emp = await db_get(f"empleados?id=eq.{fichaje['empleado_id']}&select=telefono")
    if emp and emp[0].get("telefono"):
        await wa(emp[0]["telefono"], f"⚠️ *{emp_nombre}*, tu fichaje ha sido corregido:\n🕐 {hi}-{hf} ({netas}h)\n📝 Motivo: {req.motivo}")
    
    # Notify admin
    await wa(ADMIN, f"📝 Fichaje corregido: *{emp_nombre}* → {hi}-{hf} ({netas}h). Motivo: {req.motivo}")
    
    return {"success": True, "mensaje": f"✅ Corregido: *{emp_nombre}* {hi}-{hf} ({netas}h)", "fichaje_id": fichaje["id"]}


@app.get("/health")
async def health():
    return{"status":"ok","service":"euromir-fichajes","version":"6.0"}
