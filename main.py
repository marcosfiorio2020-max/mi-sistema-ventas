from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List
import sqlite3
from datetime import datetime

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- NUEVO: Le enseñamos a Python a mostrar la página web ---
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def pagina_principal():
    return FileResponse("static/index.html")

@app.get("/admin.html")
def pagina_admin():
    return FileResponse("static/admin.html")

@app.get("/dashboard.html")
def pagina_dashboard():
    return FileResponse("static/dashboard.html")
# -----------------------------------------------------------

def inicializar_base_datos():
    conexion = sqlite3.connect("sistema_ventas.db")
    cursor = conexion.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS maestro_productos 
                      (id TEXT PRIMARY KEY, nombre TEXT, precio REAL, unidad TEXT, categoria TEXT, stock REAL, costo REAL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS ventas_cabecera 
                      (id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT, metodo_pago TEXT, total REAL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS ventas_detalle 
                      (id INTEGER PRIMARY KEY AUTOINCREMENT, venta_id INTEGER, producto TEXT, 
                       cantidad REAL, unidad TEXT, precio_unitario REAL, subtotal REAL, costo_unitario REAL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS configuracion 
                      (id INTEGER PRIMARY KEY, meta_diaria REAL, meta_mensual REAL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS calendario_financiero 
                      (id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT, tipo TEXT, monto REAL, descripcion TEXT, estado TEXT)''')
    
    try: cursor.execute("ALTER TABLE maestro_productos ADD COLUMN costo REAL DEFAULT 0")
    except: pass 
    try: cursor.execute("ALTER TABLE ventas_detalle ADD COLUMN costo_unitario REAL DEFAULT 0")
    except: pass
    try: cursor.execute("ALTER TABLE configuracion ADD COLUMN meta_mensual REAL DEFAULT 500000")
    except: pass
    
    cursor.execute("SELECT COUNT(*) FROM configuracion")
    if cursor.fetchone()[0] == 0:
        cursor.execute("INSERT INTO configuracion (id, meta_diaria, meta_mensual) VALUES (1, 50000, 500000)")

    # Limpieza de basura
    cursor.execute("DELETE FROM maestro_productos WHERE id = '' OR nombre = '' OR id IS NULL")

    conexion.commit()
    conexion.close()

inicializar_base_datos()

class Producto(BaseModel):
    id: str
    nombre: str
    precio: float
    unidad: str
    categoria: str
    stock: float 
    costo: float

class ItemVenta(BaseModel):
    nombre: str
    precio: float
    cantidad: float
    subtotal: float
    unidad: str
    esManual: bool = False

class PaqueteVenta(BaseModel):
    metodo_pago: str
    total: float
    detalles: List[ItemVenta]

class IngresoStock(BaseModel):
    id_producto: str
    cantidad: float
    nuevo_precio: float
    nuevo_costo: float

class Configuracion(BaseModel):
    meta_diaria: float
    meta_mensual: float

class EventoCalendario(BaseModel):
    fecha: str
    tipo: str
    monto: float
    descripcion: str

@app.post("/api/admin/productos")
def guardar_producto(p: Producto):
    conexion = sqlite3.connect("sistema_ventas.db")
    cursor = conexion.cursor()
    cursor.execute('''INSERT OR REPLACE INTO maestro_productos (id, nombre, precio, unidad, categoria, stock, costo) 
                      VALUES (?, ?, ?, ?, ?, ?, ?)''', (p.id, p.nombre, p.precio, p.unidad, p.categoria, p.stock, p.costo))
    conexion.commit()
    conexion.close()
    return {"status": "success"}

@app.get("/api/admin/ventas")
def obtener_ventas():
    conexion = sqlite3.connect("sistema_ventas.db")
    cursor = conexion.cursor()
    cursor.execute("SELECT id, fecha, metodo_pago, total FROM ventas_cabecera ORDER BY id DESC")
    filas = cursor.fetchall()
    conexion.close()
    return {"status": "success", "ventas": [{"id": f[0], "fecha": f[1], "metodo": f[2], "total": f[3]} for f in filas]}

@app.get("/api/productos")
def obtener_catalogo():
    conexion = sqlite3.connect("sistema_ventas.db")
    cursor = conexion.cursor()
    cursor.execute("SELECT id, nombre, precio, unidad, categoria, stock, costo FROM maestro_productos")
    filas = cursor.fetchall()
    conexion.close()
    productos = [{"id": f[0], "nombre": f[1], "precio": f[2], "unidad": f[3], "categoria": f[4], "stock": f[5], "costo": f[6] if f[6] is not None else 0} for f in filas]
    return {"status": "success", "productos": productos}

@app.put("/api/admin/stock")
def ingresar_stock(ingreso: IngresoStock):
    conexion = sqlite3.connect("sistema_ventas.db")
    cursor = conexion.cursor()
    cursor.execute("UPDATE maestro_productos SET stock = stock + ?, precio = ?, costo = ? WHERE id = ?", 
                   (ingreso.cantidad, ingreso.nuevo_precio, ingreso.nuevo_costo, ingreso.id_producto))
    conexion.commit()
    conexion.close()
    return {"status": "success"}

@app.post("/api/ventas")
def registrar_venta(venta: PaqueteVenta):
    if not venta.detalles or venta.total <= 0:
        raise HTTPException(status_code=400, detail="Venta inválida")

    conexion = sqlite3.connect("sistema_ventas.db")
    cursor = conexion.cursor()
    
    try:
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("INSERT INTO ventas_cabecera (fecha, metodo_pago, total) VALUES (?, ?, ?)", (fecha, venta.metodo_pago, venta.total))
        id_venta = cursor.lastrowid
        
        for i in venta.detalles:
            cursor.execute("SELECT stock, costo FROM maestro_productos WHERE nombre = ?", (i.nombre,))
            resultado = cursor.fetchone()
            
            costo_historico = 0
            if resultado and not i.esManual:
                stock_actual = resultado[0] if resultado[0] is not None else 0
                costo_historico = resultado[1] if resultado[1] is not None else 0
                nuevo_stock = stock_actual - i.cantidad
                
                if nuevo_stock < 0:
                    raise Exception(f"Fallo crítico: Stock insuficiente de {i.nombre}.")
                    
                cursor.execute("UPDATE maestro_productos SET stock = ? WHERE nombre = ?", (nuevo_stock, i.nombre))
                
            cursor.execute("INSERT INTO ventas_detalle (venta_id, producto, cantidad, unidad, precio_unitario, subtotal, costo_unitario) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (id_venta, i.nombre, i.cantidad, i.unidad, i.precio, i.subtotal, costo_historico))
        
        conexion.commit()
        return {"status": "success"}
        
    except Exception as e:
        conexion.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conexion.close()

@app.delete("/api/admin/productos/{id_producto}")
def eliminar_producto(id_producto: str):
    conexion = sqlite3.connect("sistema_ventas.db")
    cursor = conexion.cursor()
    cursor.execute("DELETE FROM maestro_productos WHERE id = ?", (id_producto,))
    conexion.commit()
    conexion.close()
    return {"status": "success"}

@app.get("/api/dashboard")
def obtener_dashboard():
    conexion = sqlite3.connect("sistema_ventas.db")
    cursor = conexion.cursor()
    hoy_str = datetime.now().strftime("%Y-%m-%d")
    mes_str = datetime.now().strftime("%Y-%m")
    cursor.execute("SELECT meta_diaria, meta_mensual FROM configuracion WHERE id = 1")
    config = cursor.fetchone()
    meta_diaria = config[0] if config and config[0] else 50000
    meta_mensual = config[1] if config and config[1] else 500000
    cursor.execute("SELECT COUNT(id), SUM(total) FROM ventas_cabecera WHERE fecha LIKE ?", (hoy_str + '%',))
    ventas_hoy = cursor.fetchone()
    cant_tickets = ventas_hoy[0] or 0
    ingreso_hoy = ventas_hoy[1] or 0
    ticket_promedio = ingreso_hoy / cant_tickets if cant_tickets > 0 else 0
    cursor.execute('''SELECT SUM(d.subtotal - (d.cantidad * d.costo_unitario)) 
                      FROM ventas_detalle d JOIN ventas_cabecera c ON d.venta_id = c.id 
                      WHERE c.fecha LIKE ?''', (hoy_str + '%',))
    ganancia_hoy = cursor.fetchone()[0] or 0
    cursor.execute('''SELECT SUM(d.subtotal - (d.cantidad * d.costo_unitario)) 
                      FROM ventas_detalle d JOIN ventas_cabecera c ON d.venta_id = c.id 
                      WHERE c.fecha LIKE ?''', (mes_str + '%',))
    ganancia_mes = cursor.fetchone()[0] or 0
    cursor.execute('''SELECT producto, SUM(cantidad) as cant FROM ventas_detalle 
                      GROUP BY producto ORDER BY cant DESC LIMIT 5''')
    top_volumen = [{"nombre": f[0], "cantidad": f[1]} for f in cursor.fetchall()]
    cursor.execute('''SELECT producto, SUM(subtotal - (cantidad * costo_unitario)) as ganancia 
                      FROM ventas_detalle GROUP BY producto ORDER BY ganancia DESC LIMIT 5''')
    top_ganancia = [{"nombre": f[0], "ganancia": f[1]} for f in cursor.fetchall()]
    conexion.close()
    return {"status": "success", "metas": {"diaria": meta_diaria, "costos_fijos_mes": meta_mensual}, "hoy": {"ingreso": ingreso_hoy, "ganancia": ganancia_hoy, "tickets": cant_tickets, "ticket_promedio": ticket_promedio}, "mes": {"ganancia": ganancia_mes}, "top_volumen": top_volumen, "top_ganancia": top_ganancia}

@app.put("/api/configuracion")
def actualizar_configuracion(config: Configuracion):
    conexion = sqlite3.connect("sistema_ventas.db")
    cursor = conexion.cursor()
    cursor.execute("INSERT OR IGNORE INTO configuracion (id, meta_diaria, meta_mensual) VALUES (1, 50000, 500000)")
    cursor.execute("UPDATE configuracion SET meta_diaria = ?, meta_mensual = ? WHERE id = 1", (config.meta_diaria, config.meta_mensual))
    conexion.commit()
    conexion.close()
    return {"status": "success"}

@app.get("/api/calendario")
def obtener_calendario():
    conexion = sqlite3.connect("sistema_ventas.db")
    cursor = conexion.cursor()
    cursor.execute("SELECT id, fecha, tipo, monto, descripcion FROM calendario_financiero WHERE estado = 'Pendiente' ORDER BY fecha ASC")
    filas = cursor.fetchall()
    conexion.close()
    return {"status": "success", "eventos": [{"id": f[0], "fecha": f[1], "tipo": f[2], "monto": f[3], "descripcion": f[4]} for f in filas]}

@app.post("/api/calendario")
def agregar_evento(evento: EventoCalendario):
    conexion = sqlite3.connect("sistema_ventas.db")
    cursor = conexion.cursor()
    cursor.execute("INSERT INTO calendario_financiero (fecha, tipo, monto, descripcion, estado) VALUES (?, ?, ?, ?, 'Pendiente')", (evento.fecha, evento.tipo, evento.monto, evento.descripcion))
    conexion.commit()
    conexion.close()
    return {"status": "success"}

@app.put("/api/calendario/{id_evento}/saldar")
def saldar_evento(id_evento: int):
    conexion = sqlite3.connect("sistema_ventas.db")
    cursor = conexion.cursor()
    cursor.execute("UPDATE calendario_financiero SET estado = 'Saldado' WHERE id = ?", (id_evento,))
    conexion.commit()
    conexion.close()
    return {"status": "success"}

@app.get("/api/reporte/eerr")
def estado_resultados(inicio: str, fin: str):
    conexion = sqlite3.connect("sistema_ventas.db")
    cursor = conexion.cursor()
    fin_completo = fin + " 23:59:59"
    cursor.execute("SELECT SUM(total) FROM ventas_cabecera WHERE fecha >= ? AND fecha <= ?", (inicio, fin_completo))
    ingresos = cursor.fetchone()[0] or 0
    cursor.execute('''SELECT SUM(d.cantidad * d.costo_unitario) FROM ventas_detalle d JOIN ventas_cabecera c ON d.venta_id = c.id WHERE c.fecha >= ? AND c.fecha <= ?''', (inicio, fin_completo))
    cmv = cursor.fetchone()[0] or 0
    cursor.execute('''SELECT SUM(monto) FROM calendario_financiero WHERE tipo = 'Pago' AND estado = 'Saldado' AND fecha >= ? AND fecha <= ?''', (inicio, fin_completo))
    gastos = cursor.fetchone()[0] or 0
    conexion.close()
    return {"status": "success", "ingresos": ingresos, "cmv": cmv, "ganancia_bruta": ingresos - cmv, "gastos": gastos, "ganancia_neta": (ingresos - cmv) - gastos}