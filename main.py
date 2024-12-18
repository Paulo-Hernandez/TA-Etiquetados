import xmlrpc.client
from datetime import datetime
import os
import shutil
import time
import watchdog.events
import watchdog.observers

# Leer la configuración desde el archivo config_odoo.txt
def read_config(config_file_path):
    configuration_values = {}
    try:
        with open(config_file_path, 'r') as file:
            for line in file:
                key, value = line.strip().split('=', 1)
                configuration_values[key] = value
    except FileNotFoundError:
        print(f"Archivo de configuración {config_file_path} no encontrado.")
    except Exception as e:
        print(f"Error al leer el archivo de configuración: {e}")
    return configuration_values

# Configuración
config_path = 'config_odoo.txt'
configuration = read_config(config_path)
url = configuration.get('url')
db = configuration.get('db')
username = configuration.get('username')
password = configuration.get('password')
id_sucursal = configuration.get('id_sucursal')
cabecera = configuration.get('cabecera')

# Autenticación en Odoo
try:
    print("Autenticando...")
    common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
    uid = common.authenticate(db, username, password, {})
    if not uid:
        print("Error de autenticación. Verifica credenciales.")
        exit()
    print("Autenticación exitosa, UID:", uid)
except Exception as e:
    print(f"Error durante la autenticación: {e}")
    exit()

# Conexión a modelos
models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')

def get_active_pos_sessions(sucursal_id):
    """ Obtener sesiones POS activas para una sucursal """
    try:
        session_ids = models.execute_kw(db, uid, password, 'pos.session', 'search', [
            [['state', '=', 'opened'], ['config_id', '=', int(sucursal_id)]]
        ])
        return session_ids
    except Exception as e:
        print(f"Error al obtener sesiones activas: {e}")
        return []

def get_current_date():
    return datetime.now().strftime('%Y-%m-%d')

def calculate_ean13(numero_etiqueta):
    """ Calcular el código EAN13 con el formato requerido """
    fixed_number = cabecera  # Ejemplo de número fijo
    date_part = datetime.now().strftime('%d%m%y')
    base_ean = fixed_number + date_part + numero_etiqueta

    def calculate_check_digit(ean12):
        even_sum = sum(int(ean12[i]) for i in range(1, 12, 2))
        odd_sum = sum(int(ean12[i]) for i in range(0, 12, 2))
        total = odd_sum + 3 * even_sum
        return (10 - (total % 10)) % 10

    check_digit = calculate_check_digit(base_ean[:12])
    return base_ean[:12] + str(check_digit)

def get_product_id_by_reference(reference):
    """ Buscar producto por referencia interna """
    try:
        product_ids = models.execute_kw(db, uid, password, 'product.product', 'search', [
            [['default_code', '=', reference]]
        ])
        return product_ids[0] if product_ids else None
    except Exception as e:
        print(f"Error buscando el producto '{reference}': {e}")
        return None

def display_products_and_quantities(products):
    """ Mostrar cantidades junto con la referencia interna del producto """
    print("Resumen de productos y cantidades:")
    for reference, quantity in products:
        print(f"Referencia: {reference}, Cantidad: {quantity:.3f}")

def create_ticket(numero_etiqueta, pos_session_id, company_id, ean13):
    """ Crear registro en tickets.n.products """
    try:
        ticket_id = models.execute_kw(db, uid, password, 'tickets.n.products', 'create', [{
            'numero_etiqueta': numero_etiqueta,
            'status': 'prepared',
            'pos_session_id': pos_session_id,
            'company_id': company_id,
            'ean13': ean13
        }])
        print(f"Ticket creado con ID: {ticket_id}")
        return ticket_id
    except Exception as e:
        print(f"Error al crear el ticket: {e}")
        return None

def add_products_to_ticket(ticket_id, products):
    """ Agregar productos al ticket """
    lines = []
    for reference, quantity in products:
        product_id = get_product_id_by_reference(reference)
        if not product_id:
            print(f"Producto no encontrado: {reference}")
            continue
        # Obtener precio unitario del producto
        product = models.execute_kw(db, uid, password, 'product.product', 'read', [[product_id], ['list_price']])
        price = product[0]['list_price'] if product else 0.0
        lines.append((0, 0, {
            'product_id': product_id,
            'cantidad': quantity,
            'precio_unitario': price
        }))
    display_products_and_quantities(products)
    if lines:
        try:
            models.execute_kw(db, uid, password, 'tickets.n.products', 'write', [[ticket_id], {
                'lineas_ids': lines
            }])
            print(f"Líneas de producto agregadas al ticket {ticket_id}")
        except Exception as e:
            print(f"Error al agregar productos al ticket: {e}")

def process_file(file_path, processed_directory, pos_session_id, company_id):
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()
            ticket_part = lines[0].strip()[-4:]  # Obtener los últimos 4 dígitos de la primera línea
            ean13 = calculate_ean13(ticket_part)
            products = []
            for line in lines:
                reference = line[0:6].strip().lstrip('0')  # Referencia del producto
                middle_number = line[6:7]  # Determinar si es pesable o unitario
                quantity = int(line[7:13].strip())
                if middle_number == '0':
                    quantity = quantity / 1000.0  # Convertir a kilogramos si es pesable
                products.append((reference, quantity))
            display_products_and_quantities(products)
        ticket_id = create_ticket(ticket_part, pos_session_id, company_id, ean13)
        if ticket_id:
            add_products_to_ticket(ticket_id, products)
            shutil.move(file_path, os.path.join(processed_directory, os.path.basename(file_path)))
            print(f"Archivo procesado y movido: {file_path}")
    except Exception as e:
        print(f"Error al procesar el archivo {file_path}: {e}")

class Handler(watchdog.events.FileSystemEventHandler):
    def __init__(self, processed_directory, pos_session_id, company_id):
        self.processed_directory = processed_directory
        self.pos_session_id = pos_session_id
        self.company_id = company_id

    def on_created(self, event):
        if event.is_directory:
            return
        if event.src_path.endswith('.txt'):
            print(f"Nuevo archivo detectado: {event.src_path}")
            process_file(event.src_path, self.processed_directory, self.pos_session_id, self.company_id)

if __name__ == "__main__":
    input_directory = 'Tickets'
    processed_directory = 'Tickets_Procesados'

    active_sessions = get_active_pos_sessions(id_sucursal)
    if not active_sessions:
        print("No hay sesiones POS activas para la sucursal proporcionada.")
        exit()
    pos_session_id = active_sessions[0]
    company_id = int(id_sucursal)  # Se asume que id_sucursal es el company_id correspondiente

    if not os.path.exists(processed_directory):
        os.makedirs(processed_directory)

    observer = watchdog.observers.Observer()
    event_handler = Handler(processed_directory, pos_session_id, company_id)
    observer.schedule(event_handler, path=input_directory, recursive=False)

    print(f"Monitoreando la carpeta '{input_directory}' para nuevos archivos...")
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("Monitoreo detenido.")
    observer.join()