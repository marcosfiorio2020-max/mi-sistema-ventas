from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import hashlib
from typing import List
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import jwt

# --- CONFIGURACIÓN ---
URL_BASE_DATOS = "postgresql://neondb_owner:npg_EaVGnUC3obt2@ep-bitter-mud-ac5lh1s6.sa-east-1.aws.neon.tech/neondb?sslmode=require"
SECRET_KEY = "clave_maestra_super_segura_mini_sap" # En producción, esto va en variables de entorno

app = FastAPI()

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# --- FUNCIONES DE SEGURIDAD Y PRECISIÓN ---
def a_decimal(valor):
    try:
        return Decimal(str(valor)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal('0.00')

def verificar_token(token: str) -> str:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload["empresa_id"]
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Sesión inválida o expirada")

# --- BASE DE DATOS ---
def inicializar_base_datos():
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS usuarios (id SERIAL PRIMARY KEY, usuario TEXT UNIQUE, password TEXT, empresa_id TEXT)''')
    try:
        cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS activo BOOLEAN DEFAULT TRUE")
    except Exception:
        conexion.rollback()

    cursor.execute('''CREATE TABLE IF NOT EXISTS maestro_productos (id TEXT PRIMARY KEY, nombre TEXT, precio NUMERIC, unidad TEXT, categoria TEXT, stock NUMERIC, costo NUMERIC, empresa_id TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS ventas_cabecera (id SERIAL PRIMARY KEY, fecha TEXT, metodo_pago TEXT, total NUMERIC, empresa_id TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS ventas_detalle (id SERIAL PRIMARY KEY, venta_id INTEGER, producto TEXT, cantidad NUMERIC, unidad TEXT, precio_unitario NUMERIC, subtotal NUMERIC, costo_unitario NUMERIC, empresa_id TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS configuracion (id INTEGER PRIMARY KEY, meta_diaria NUMERIC, meta_mensual NUMERIC, empresa_id TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS calendario_financiero (id SERIAL PRIMARY KEY, fecha TEXT, tipo TEXT, monto NUMERIC, descripcion TEXT, estado TEXT, empresa_id TEXT)''')
    
    conexion.commit()
    cursor.close()
    conexion.close()

inicializar_base_datos()

# --- MODELOS PYDANTIC ---
class Producto(BaseModel):
    id: str; nombre: str; precio: float; unidad: str; categoria: str; stock: float; costo: float
class ItemVenta(BaseModel):
    nombre: str; precio: float; cantidad: float; subtotal: float; unidad: str; esManual: bool = False
class PaqueteVenta(BaseModel):
    metodo_pago: str; total: float; detalles: List[ItemVenta]
class IngresoStock(BaseModel):
    id_producto: str; cantidad: float; nuevo_precio: float; nuevo_costo: float
class Configuracion(BaseModel):
    meta_diaria: float; meta_mensual: float
class EventoCalendario(BaseModel):
    fecha: str; tipo: str; monto: float; descripcion: str
class UsuarioLogin(BaseModel):
    usuario: str; password: str

# --- RUTAS VISTAS ---
@app.get("/")
def pagina_principal(): return FileResponse("static/login.html") 
@app.get("/mostrador")
def pagina_mostrador(): return FileResponse("static/index.html") 
@app.get("/admin.html")
def pagina_admin(): return FileResponse("static/admin.html")
@app.get("/dashboard.html")
def pagina_dashboard(): return FileResponse("static/dashboard.html")

# --- RUTAS API - AUTENTICACIÓN ---
@app.post("/api/registro")
def registro(user: UsuarioLogin):
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    hash_pw = hashlib.sha256(user.password.encode()).hexdigest()
    empresa_id = user.usuario.lower() 
    try:
        cursor.execute("INSERT INTO usuarios (usuario, password, empresa_id, activo) VALUES (%s, %s, %s, TRUE)", (user.usuario, hash_pw, empresa_id))
        conexion.commit()
        return {"status": "success"}
    except psycopg2.errors.UniqueViolation: 
        conexion.rollback()
        raise HTTPException(status_code=400, detail="El usuario ya existe")
    finally:
        cursor.close()
        conexion.close()

@app.post("/api/login")
def login(user: UsuarioLogin):
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    hash_pw = hashlib.sha256(user.password.encode()).hexdigest()
    cursor.execute("SELECT empresa_id, activo FROM usuarios WHERE usuario = %s AND password = %s", (user.usuario, hash_pw))
    resultado = cursor.fetchone()
    cursor.close()
    conexion.close()
    
    if resultado:
        if not resultado[1]:
            raise HTTPException(status_code=403, detail="Su cuenta comercial se encuentra suspendida.")
        token = jwt.encode({"empresa_id": resultado[0], "usuario": user.usuario}, SECRET_KEY, algorithm="HS256")
        return {"status": "success", "token": token, "empresa_id": resultado[0]}
    else:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")

# --- RUTAS API - SUPERADMIN ---
@app.get("/api/superadmin/usuarios")
def listar_usuarios_plataforma(token: str):
    empresa_id = verificar_token(token)
    if empresa_id != "admin": raise HTTPException(status_code=403, detail="Acceso denegado")
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT id, usuario, empresa_id, activo FROM usuarios WHERE usuario != 'admin' ORDER BY id ASC")
    usuarios = cursor.fetchall()
    cursor.close()
    conexion.close()
    return usuarios

@app.put("/api/superadmin/usuarios/{usuario_id}/toggle")
def alternar_estado_usuario(usuario_id: int, token: str):
    empresa_id = verificar_token(token)
    if empresa_id != "admin": raise HTTPException(status_code=403, detail="Acceso denegado")
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    cursor.execute("UPDATE usuarios SET activo = NOT activo WHERE id = %s", (usuario_id,))
    conexion.commit()
    cursor.close()
    conexion.close()
    return {"status": "success"}

# --- RUTAS API - PRODUCTOS Y STOCK ---
@app.post("/api/admin/productos")
def guardar_producto(p: Producto, token: str):
    empresa_id = verificar_token(token)
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    sql = '''INSERT INTO maestro_productos (id, nombre, precio, unidad, categoria, stock, costo, empresa_id) 
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
             ON CONFLICT (id) 
             DO UPDATE SET nombre=EXCLUDED.nombre, precio=EXCLUDED.precio, unidad=EXCLUDED.unidad, 
                           categoria=EXCLUDED.categoria, stock=EXCLUDED.stock, costo=EXCLUDED.costo, empresa_id=EXCLUDED.empresa_id'''
    cursor.execute(sql, (p.id, p.nombre, a_decimal(p.precio), p.unidad, p.categoria, a_decimal(p.stock), a_decimal(p.costo), empresa_id))
    conexion.commit()
    cursor.close()
    conexion.close()
    return {"status": "success"}

@app.get("/api/productos")
def obtener_catalogo(token: str):
    empresa_id = verificar_token(token)
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    cursor.execute("SELECT id, nombre, precio, unidad, categoria, stock, costo FROM maestro_productos WHERE empresa_id = %s", (empresa_id,))
    filas = cursor.fetchall()
    cursor.close()
    conexion.close()
    return [{"id": f[0], "nombre": f[1], "precio": float(f[2]), "unidad": f[3], "categoria": f[4], "stock": float(f[5]), "costo": float(f[6])} for f in filas]

@app.put("/api/admin/stock")
def ingresar_stock(ingreso: IngresoStock, token: str):
    empresa_id = verificar_token(token)
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    cursor.execute("UPDATE maestro_productos SET stock = stock + %s, precio = %s, costo = %s WHERE id = %s AND empresa_id = %s", 
                   (a_decimal(ingreso.cantidad), a_decimal(ingreso.nuevo_precio), a_decimal(ingreso.nuevo_costo), ingreso.id_producto, empresa_id))
    conexion.commit()
    cursor.close()
    conexion.close()
    return {"status": "success"}

@app.delete("/api/admin/productos/{id_producto}")
def eliminar_producto(id_producto: str, token: str):
    empresa_id = verificar_token(token)
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    cursor.execute("DELETE FROM maestro_productos WHERE id = %s AND empresa_id = %s", (id_producto, empresa_id))
    conexion.commit()
    cursor.close()
    conexion.close()
    return {"status": "success"}

# --- RUTAS API - VENTAS (TRANSACCIONES ATÓMICAS BLINDADAS) ---
@app.post("/api/ventas")
def registrar_venta(venta: PaqueteVenta, token: str):
    empresa_id = verificar_token(token)
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        cursor.execute("BEGIN")
        total_decimal = a_decimal(venta.total)
        cursor.execute("INSERT INTO ventas_cabecera (fecha, metodo_pago, total, empresa_id) VALUES (%s, %s, %s, %s) RETURNING id", (fecha_actual, venta.metodo_pago, total_decimal, empresa_id))
        venta_id = cursor.fetchone()[0]
        
        for d in venta.detalles:
            cursor.execute("SELECT costo FROM maestro_productos WHERE nombre = %s AND empresa_id = %s", (d.nombre, empresa_id))
            res_costo = cursor.fetchone()
            costo_u = a_decimal(res_costo[0]) if res_costo else a_decimal(0)
            
            cursor.execute(
                '''INSERT INTO ventas_detalle (venta_id, producto, cantidad, unidad, precio_unitario, subtotal, costo_unitario, empresa_id) 
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
                (venta_id, d.nombre, a_decimal(d.cantidad), d.unidad, a_decimal(d.precio), a_decimal(d.subtotal), costo_u, empresa_id)
            )
            if not d.esManual:
                cursor.execute("UPDATE maestro_productos SET stock = stock - %s WHERE nombre = %s AND empresa_id = %s", (a_decimal(d.cantidad), d.nombre, empresa_id))
        
        conexion.commit()
        return {"status": "success"}
    except Exception as e:
        conexion.rollback()
        raise HTTPException(status_code=500, detail=f"Error en la transacción: {str(e)}")
    finally:
        cursor.close()
        conexion.close()

@app.get("/api/admin/ventas")
def obtener_ventas_admin(token: str):
    empresa_id = verificar_token(token)
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    cursor.execute("SELECT id, fecha, metodo_pago, total FROM ventas_cabecera WHERE empresa_id = %s ORDER BY id DESC", (empresa_id,))
    filas = cursor.fetchall()
    cursor.close()
    conexion.close()
    return [{"id": f[0], "fecha": f[1], "metodo": f[2], "total": float(f[3])} for f in filas]

@app.delete("/api/admin/ventas/{id_venta}")
def eliminar_venta(id_venta: int, token: str):
    empresa_id = verificar_token(token)
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    try:
        cursor.execute("BEGIN")
        cursor.execute("SELECT producto, cantidad FROM ventas_detalle WHERE venta_id = %s AND empresa_id = %s", (id_venta, empresa_id))
        detalles = cursor.fetchall()
        
        for d in detalles:
            cursor.execute("UPDATE maestro_productos SET stock = stock + %s WHERE nombre = %s AND empresa_id = %s", (a_decimal(d[1]), d[0], empresa_id))
            
        cursor.execute("DELETE FROM ventas_detalle WHERE venta_id = %s AND empresa_id = %s", (id_venta, empresa_id))
        cursor.execute("DELETE FROM ventas_cabecera WHERE id = %s AND empresa_id = %s", (id_venta, empresa_id))
        conexion.commit()
        return {"status": "success"}
    except Exception as e:
        conexion.rollback()
        raise HTTPException(status_code=500, detail=f"Error al anular: {str(e)}")
    finally:
        cursor.close()
        conexion.close()

# --- RUTAS API - DASHBOARD Y CALENDARIO ---
@app.get("/api/dashboard")
def obtener_dashboard(token: str):
    empresa_id = verificar_token(token)
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    hoy_busqueda = datetime.now().strftime("%Y-%m-%d") + "%"
    mes_busqueda = datetime.now().strftime("%Y-%m") + "%"
    
    cursor.execute("SELECT meta_diaria, meta_mensual FROM configuracion WHERE empresa_id = %s", (empresa_id,))
    config = cursor.fetchone()
    meta_diaria = float(config[0]) if config and config[0] else 50000.0
    meta_mensual = float(config[1]) if config and config[1] else 500000.0
    
    cursor.execute("SELECT COUNT(id), SUM(total) FROM ventas_cabecera WHERE fecha LIKE %s AND empresa_id = %s", (hoy_busqueda, empresa_id))
    ventas_hoy = cursor.fetchone()
    cant_tickets = ventas_hoy[0] or 0
    ingreso_hoy = float(ventas_hoy[1] or 0)
    ticket_promedio = ingreso_hoy / cant_tickets if cant_tickets > 0 else 0
    
    cursor.execute('''SELECT SUM(d.subtotal - (d.cantidad * d.costo_unitario)) FROM ventas_detalle d JOIN ventas_cabecera c ON d.venta_id = c.id WHERE c.fecha LIKE %s AND c.empresa_id = %s''', (hoy_busqueda, empresa_id))
    ganancia_hoy = float(cursor.fetchone()[0] or 0)
    
    cursor.execute('''SELECT SUM(d.subtotal - (d.cantidad * d.costo_unitario)) FROM ventas_detalle d JOIN ventas_cabecera c ON d.venta_id = c.id WHERE c.fecha LIKE %s AND c.empresa_id = %s''', (mes_busqueda, empresa_id))
    ganancia_bruta_mes = float(cursor.fetchone()[0] or 0)
    
    cursor.execute('''SELECT SUM(monto) FROM calendario_financiero WHERE fecha LIKE %s AND empresa_id = %s''', (mes_busqueda, empresa_id))
    gastos_fijos_mes = float(cursor.fetchone()[0] or 0)
    
    ganancia_mes = ganancia_bruta_mes - gastos_fijos_mes
    
    cursor.execute('''SELECT producto, SUM(cantidad) as cant FROM ventas_detalle WHERE empresa_id = %s GROUP BY producto ORDER BY cant DESC LIMIT 5''', (empresa_id,))
    top_volumen = [{"nombre": f[0], "cantidad": float(f[1])} for f in cursor.fetchall()]
    
    cursor.execute('''SELECT producto, SUM(subtotal - (cantidad * costo_unitario)) as ganancia FROM ventas_detalle WHERE empresa_id = %s GROUP BY producto ORDER BY ganancia DESC LIMIT 5''', (empresa_id,))
    top_ganancia = [{"nombre": f[0], "ganancia": float(f[1])} for f in cursor.fetchall()]
    
    # PRODUCTOS INMOVILIZADOS
    cursor.execute('''
        SELECT p.nombre, p.stock, COALESCE(SUM(d.cantidad), 0) as vendidos 
        FROM maestro_productos p 
        LEFT JOIN ventas_detalle d ON p.nombre = d.producto AND p.empresa_id = d.empresa_id 
        WHERE p.empresa_id = %s 
        GROUP BY p.id, p.nombre, p.stock 
        ORDER BY vendidos ASC, p.stock DESC LIMIT 10
    ''', (empresa_id,))
    top_inactivos = [{"nombre": f[0], "stock": float(f[1]), "vendidos": float(f[2])} for f in cursor.fetchall()]
    
    cursor.close()
    conexion.close()
    return {
        "status": "success", "metas": {"diaria": meta_diaria, "costos_fijos_mes": meta_mensual}, 
        "hoy": {"ingreso": ingreso_hoy, "ganancia": ganancia_hoy, "tickets": cant_tickets, "ticket_promedio": ticket_promedio}, 
        "mes": {"ganancia": ganancia_mes}, "top_volumen": top_volumen, "top_ganancia": top_ganancia, "top_inactivos": top_inactivos
    }

@app.put("/api/configuracion")
def actualizar_configuracion(config: Configuracion, token: str):
    empresa_id = verificar_token(token)
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    cursor.execute("SELECT id FROM configuracion WHERE empresa_id = %s", (empresa_id,))
    existe = cursor.fetchone()
    if existe: cursor.execute("UPDATE configuracion SET meta_diaria = %s, meta_mensual = %s WHERE empresa_id = %s", (config.meta_diaria, config.meta_mensual, empresa_id))
    else:
        cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM configuracion")
        cursor.execute("INSERT INTO configuracion (id, meta_diaria, meta_mensual, empresa_id) VALUES (%s, %s, %s, %s)", (cursor.fetchone()[0], config.meta_diaria, config.meta_mensual, empresa_id))
    conexion.commit()
    cursor.close()
    conexion.close()
    return {"status": "success"}

@app.get("/api/calendario")
def obtener_calendario(token: str):
    empresa_id = verificar_token(token)
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    cursor.execute("SELECT id, fecha, tipo, monto, descripcion FROM calendario_financiero WHERE estado = 'Pendiente' AND empresa_id = %s ORDER BY fecha ASC", (empresa_id,))
    filas = cursor.fetchall()
    cursor.close()
    conexion.close()
    return {"status": "success", "eventos": [{"id": f[0], "fecha": f[1], "tipo": f[2], "monto": float(f[3]), "descripcion": f[4]} for f in filas]}

@app.post("/api/calendario")
def agregar_evento(evento: EventoCalendario, token: str):
    empresa_id = verificar_token(token)
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    cursor.execute("INSERT INTO calendario_financiero (fecha, tipo, monto, descripcion, estado, empresa_id) VALUES (%s, %s, %s, %s, 'Pendiente', %s)", (evento.fecha, evento.tipo, a_decimal(evento.monto), evento.descripcion, empresa_id))
    conexion.commit()
    cursor.close()
    conexion.close()
    return {"status": "success"}

@app.put("/api/calendario/{id_evento}/saldar")
def saldar_evento(id_evento: int, token: str):
    empresa_id = verificar_token(token)
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    cursor.execute("UPDATE calendario_financiero SET estado = 'Saldado' WHERE id = %s AND empresa_id = %s", (id_evento, empresa_id))
    conexion.commit()
    cursor.close()
    conexion.close()
    return {"status": "success"}

@app.delete("/api/calendario/{id_evento}")
def eliminar_evento_calendario(id_evento: int, token: str):
    empresa_id = verificar_token(token)
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    cursor.execute("DELETE FROM calendario_financiero WHERE id = %s AND empresa_id = %s", (id_evento, empresa_id))
    conexion.commit()
    cursor.close()
    conexion.close()
    return {"status": "success"}

@app.get("/api/reporte/eerr")
def estado_resultados(inicio: str, fin: str, token: str):
    empresa_id = verificar_token(token)
    conexion = psycopg2.connect(URL_BASE_DATOS)
    cursor = conexion.cursor()
    fin_completo = fin + " 23:59:59"
    
    cursor.execute("SELECT SUM(total) FROM ventas_cabecera WHERE fecha >= %s AND fecha <= %s AND empresa_id = %s", (inicio, fin_completo, empresa_id))
    ingresos = float(cursor.fetchone()[0] or 0)
    
    cursor.execute('''SELECT SUM(d.cantidad * d.costo_unitario) FROM ventas_detalle d JOIN ventas_cabecera c ON d.venta_id = c.id WHERE c.fecha >= %s AND c.fecha <= %s AND c.empresa_id = %s''', (inicio, fin_completo, empresa_id))
    cmv = float(cursor.fetchone()[0] or 0)
    
    cursor.execute('''SELECT descripcion, SUM(monto) FROM calendario_financiero WHERE tipo = 'Pago' AND estado = 'Saldado' AND fecha >= %s AND fecha <= %s AND empresa_id = %s GROUP BY descripcion''', (inicio, fin_completo, empresa_id))
    gastos_detalle = [{"descripcion": f[0], "monto": float(f[1])} for f in cursor.fetchall()]
    gastos_total = sum(g["monto"] for g in gastos_detalle)
    
    cursor.close()
    conexion.close()
    return {
        "status": "success", "ingresos": ingresos, "cmv": cmv, "ganancia_bruta": ingresos - cmv, 
        "gastos_detalle": gastos_detalle, "gastos": gastos_total, "ganancia_neta": (ingresos - cmv) - gastos_total
    }