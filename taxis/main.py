from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import sqlite3
import uuid
from datetime import datetime
import pandas as pd
from fastapi.responses import FileResponse
from weasyprint import HTML
from jinja2 import Template
from zoneinfo import ZoneInfo

ZONA_MAZATLAN = ZoneInfo("America/Mazatlan")

app = FastAPI(title="Sistema de Asignación de Camiones")
DB_FILE = "taxis.db"

bloqueos_activos = []

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS taxis (id INTEGER PRIMARY KEY, status TEXT)')
    
    # Se agrega hora_inicio para medir el tiempo real
    c.execute('''
        CREATE TABLE IF NOT EXISTS viajes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            taxi_id INTEGER,
            origen_lat REAL,
            origen_lng REAL,
            desvio1_lat REAL,
            desvio1_lng REAL,
            desvio2_lat REAL,
            desvio2_lng REAL,
            dest_lat REAL,
            dest_lng REAL,
            origen_texto TEXT,
            destino_texto TEXT,
            hora_inicio TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # NUEVA TABLA: Registro histórico inmutable para auditorías
    c.execute('''
        CREATE TABLE IF NOT EXISTS historico_viajes (
            movimiento_id TEXT PRIMARY KEY,
            camion_id INTEGER,
            origen_lat REAL,
            origen_lng REAL,
            dest_lat REAL,
            dest_lng REAL,
            origen_texto TEXT,
            destino_texto TEXT,
            hora_inicio TIMESTAMP,
            hora_fin TIMESTAMP,
            duracion_minutos REAL
        )
    ''')
    
    c.execute("SELECT count(*) FROM taxis")
    if c.fetchone()[0] == 0:
        for i in range(1, 21):
            c.execute("INSERT INTO taxis (id, status) VALUES (?, 'Disponible')", (i,))
    conn.commit()
    conn.close()

init_db()

class TripData(BaseModel):
    taxi_id: int
    origen_lat: float
    origen_lng: float
    desvio1_lat: float = None
    desvio1_lng: float = None
    desvio2_lat: float = None
    desvio2_lng: float = None
    dest_lat: float
    dest_lng: float
    origen_texto: str
    destino_texto: str

class CompleteData(BaseModel):
    taxi_id: int
    viaje_id: int 

# NUEVA CLASE PARA LOS BLOQUEOS
class BloqueoData(BaseModel):
    bloqueos: list

@app.get("/", response_class=HTMLResponse)
async def get_index():
    with open("index.html", "r", encoding="utf-8") as f: return HTMLResponse(content=f.read())

@app.post("/api/bloqueos")
def actualizar_bloqueos(data: BloqueoData):
    global bloqueos_activos
    bloqueos_activos = data.bloqueos
    return {"message": "Bloqueos actualizados"}

@app.get("/api/bloqueos")
def obtener_bloqueos():
    return {"bloqueos": bloqueos_activos}


@app.get("/api/kpis")
def get_kpis():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # 1. Utilización de flota
    c.execute("SELECT count(*) FROM taxis")
    total_camiones = c.fetchone()[0] or 1 
    c.execute("SELECT count(*) FROM taxis WHERE status != 'Disponible'")
    camiones_activos = c.fetchone()[0]
    
    # 2. Viajes completados hoy usando LIKE
    hoy_mazatlan = datetime.now(ZONA_MAZATLAN).strftime('%Y-%m-%d')
    c.execute("SELECT count(*), avg(duracion_minutos) FROM historico_viajes WHERE hora_inicio LIKE ?", (hoy_mazatlan + '%',))
    row = c.fetchone()
    viajes_hoy = row[0] or 0
    promedio_tiempo = row[1] or 0.0
    
    conn.close()
    
    uso_flota = round((camiones_activos / total_camiones) * 100, 1) if total_camiones > 0 else 0
    
    return {
        "uso_flota": f"{uso_flota}%",
        "activos": camiones_activos,
        "total": total_camiones,
        "viajes_hoy": viajes_hoy,
        "promedio_tiempo": f"{round(promedio_tiempo, 1)} min"
    }

@app.get("/chofer", response_class=HTMLResponse)
async def get_chofer_page():
    with open("chofer.html", "r", encoding="utf-8") as f: return HTMLResponse(content=f.read())

@app.get("/generar_reporte")
def generar_reporte_pdf():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM historico_viajes", conn)
    conn.close()
    
    # Aquí puedes hacer agrupaciones para el reporte
    # Por ejemplo, calcular el promedio de tiempo por camión
    resumen = df.groupby('camion_id')['duracion_minutos'].agg(['mean', 'count']).reset_index()
    
    # (El código de generación de PDF va aquí, ver paso 4)
    pass


@app.get("/descargar_reporte_pdf")
def descargar_reporte_pdf():
    conn = sqlite3.connect(DB_FILE)
    df = pd.read_sql_query("SELECT * FROM historico_viajes ORDER BY hora_inicio DESC LIMIT 50", conn)
    conn.close()
    
    # Crear una plantilla HTML inyectando los datos del DataFrame
    html_template = """
    <html>
    <head>
        <style>
            body { font-family: 'Helvetica'; font-size: 12px; color: #333; }
            h1 { color: #1e293b; border-bottom: 2px solid #4CAF50; padding-bottom: 5px; }
            table { width: 100%; border-collapse: collapse; margin-top: 20px; }
            th { background-color: #38bdf8; color: white; padding: 8px; text-align: left; }
            td { border-bottom: 1px solid #ddd; padding: 8px; }
            .fuga { color: red; font-weight: bold; }
        </style>
    </head>
    <body>
        <h1>Estudio de Movimientos Logísticos - Pemex</h1>
        <p>Fecha de emisión: {{ fecha_hoy }}</p>
        <table>
            <tr>
                <th>ID Movimiento</th>
                <th>Camión</th>
                <th>Origen -> Destino</th>
                <th>Duración Real</th>
                <th>Lat/Lng Origen</th>
                <th>Lat/Lng Destino</th>
            </tr>
            {% for index, row in df.iterrows() %}
            <tr>
                <td>{{ row['movimiento_id'] }}</td>
                <td>#{{ row['camion_id'] }}</td>
                <td>{{ row['origen_texto'][:15] }}... -> {{ row['destino_texto'][:15] }}...</td>
                <!-- Si la duración es sospechosa (ej. > 60 min), la pintamos de rojo para auditar -->
                <td class="{% if row['duracion_minutos'] > 60 %}fuga{% endif %}">
                    {{ "%.2f"|format(row['duracion_minutos']) }} min
                </td>
                <td style="font-size: 10px;">{{ row['origen_lat'] }}, {{ row['origen_lng'] }}</td>
                <td style="font-size: 10px;">{{ row['dest_lat'] }}, {{ row['dest_lng'] }}</td>
            </tr>
            {% endfor %}
        </table>
    </body>
    </html>
    """
    
    template = Template(html_template)
    html_content = template.render(df=df, fecha_hoy=datetime.now().strftime('%Y-%m-%d %H:%M'))
    
    pdf_path = "reporte_movimientos.pdf"
    HTML(string=html_content).write_pdf(pdf_path)
    
    return FileResponse(pdf_path, media_type='application/pdf', filename='Reporte_Tiempos_Logistia.pdf')

@app.get("/taxis")
def get_taxis():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM taxis")
    taxis = [dict(row) for row in c.fetchall()]
    
    for t in taxis:
        c.execute("SELECT * FROM viajes WHERE taxi_id = ? ORDER BY id ASC", (t["id"],))
        t["viajes"] = [dict(row) for row in c.fetchall()]
    conn.close()
    return taxis



@app.post("/assign_trip")
def assign_trip(trip: TripData):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Obtener hora exacta de inicio en la zona de Mazatán
    hora_inicio_local = datetime.now(ZONA_MAZATLAN).strftime('%Y-%m-%d %H:%M:%S')
    
    c.execute("""
        INSERT INTO viajes (taxi_id, origen_lat, origen_lng, desvio1_lat, desvio1_lng, desvio2_lat, desvio2_lng, dest_lat, dest_lng, origen_texto, destino_texto, hora_inicio) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (trip.taxi_id, trip.origen_lat, trip.origen_lng, trip.desvio1_lat, trip.desvio1_lng, trip.desvio2_lat, trip.desvio2_lng, trip.dest_lat, trip.dest_lng, trip.origen_texto, trip.destino_texto, hora_inicio_local))
    
    c.execute("UPDATE taxis SET status = 'En Viaje' WHERE id = ?", (trip.taxi_id,))
    conn.commit()
    conn.close()
    return {"message": "Viaje encolado"}

    
@app.post("/complete_trip")
def complete_trip(data: CompleteData):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    c.execute("SELECT * FROM viajes WHERE id = ?", (data.viaje_id,))
    viaje = c.fetchone()
    
    if viaje:
        movimiento_id = f"MOV-{str(uuid.uuid4())[:8].upper()}"
        
        # Parsear fechas usando ZONA_MAZATLAN en ambas partes
        try:
            hora_str = str(viaje[12]).split('.')[0] 
            hora_inicio = datetime.strptime(hora_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=ZONA_MAZATLAN)
        except Exception as e:
            hora_inicio = datetime.now(ZONA_MAZATLAN)
            
        hora_fin = datetime.now(ZONA_MAZATLAN)
        # Calcula los minutos reales asegurando un mínimo de 1 minuto
        duracion_min = max((hora_fin - hora_inicio).total_seconds() / 60.0, 1.0)
        
        try:
            c.execute("""
                INSERT INTO historico_viajes 
                (movimiento_id, camion_id, origen_lat, origen_lng, dest_lat, dest_lng, origen_texto, destino_texto, hora_inicio, hora_fin, duracion_minutos)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                movimiento_id, data.taxi_id, 
                viaje[2], viaje[3], viaje[8], viaje[9], 
                viaje[10], viaje[11], 
                hora_inicio.strftime('%Y-%m-%d %H:%M:%S'), hora_fin.strftime('%Y-%m-%d %H:%M:%S'), duracion_min
            ))
            c.execute("DELETE FROM viajes WHERE id = ?", (data.viaje_id,))
        except Exception as e:
            print(f"Error crítico al guardar historial: {e}")
            
    c.execute("SELECT count(*) FROM viajes WHERE taxi_id = ?", (data.taxi_id,))
    if c.fetchone()[0] == 0:
        c.execute("UPDATE taxis SET status = 'Disponible' WHERE id = ?", (data.taxi_id,))
        
    conn.commit()
    conn.close()
    return {"message": "Viaje auditado y completado", "movimiento_id": movimiento_id}