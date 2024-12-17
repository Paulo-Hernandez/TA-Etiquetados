import xmlrpc.client
from datetime import datetime
import os
import shutil
import time


# Función para leer la configuración desde un archivo
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


# Leer configuración de config_odoo.txt
config_path = 'config_odoo.txt'
configuration = read_config(config_path)

url = configuration.get('url')
db = configuration.get('db')
username = configuration.get('username')
password = configuration.get('password')
id_cliente = configuration.get('id_cliente')
id_sucursal = configuration.get('id_sucursal')

# Autenticación
try:
    print("Autenticando...")
    common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
    uid = common.authenticate(db, username, password, {})

    if not uid:
        print("Error de autenticación. Verifica las credenciales o la conexión al servidor.")
        exit()

    print("Versión del servidor:", common.version())
    print("ID del usuario autenticado:", uid)
except Exception as e:
    print(f"Error durante la autenticación: {e}")
    input("Presiona Enter para salir...")
    exit()

# Conexión a los objetos
try:
    models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')
except Exception as e:
    print(f"Error al conectar con el servidor de objetos: {e}")
    input("Presiona Enter para salir...")
    exit()


# Obtener la fecha actual
def get_current_date():
    return datetime.now().strftime('%Y-%m-%d')


# Función para obtener el ID de un producto por su referencia interna
def get_product_id_by_internal_reference(internal_reference):
    try:
        product_ids = models.execute_kw(db, uid, password, 'product.product', 'search', [
            [['default_code', '=', internal_reference]]
        ])
        if product_ids:
            return product_ids[0]
        else:
            print(f"No se encontró ningún producto con la referencia interna '{internal_reference}'")
            return None
    except Exception as e:
        print(f"Error al buscar el producto: {e}")
        return None


# Leer un archivo y extraer referencias internas y cantidades
def extract_references_and_quantities(file_name):
    references_and_quantities = []
    try:
        with open(file_name, 'r') as f:
            for line in f:
                if len(line) >= 12:
                    reference = line[0:6].strip().lstrip('0')
                    quantity_str = line[7:13].strip().zfill(6)
                    try:
                        quantity = int(quantity_str)
                    except ValueError:
                        print(f"Error al convertir la cantidad {quantity_str}' a entero en la línea: {line}")
                        continue

                    if quantity > 50:
                        quantity = quantity / 1000.0

                    references_and_quantities.append((reference, quantity))
    except FileNotFoundError:
        print(f"Archivo {file_name} no encontrado.")
    except Exception as e:
        print(f"Error al leer el archivo {file_name}: {e}")

    return references_and_quantities


# Función para extraer el código completo de un archivo
def extract_ref_complete(file_name):
    try:
        with open(file_name, 'r') as f:
            first_line = f.readline().strip()
            return first_line
    except FileNotFoundError:
        print(f"Archivo {file_name} no encontrado.")
    except Exception as e:
        print(f"Error al leer el archivo {file_name}: {e}")
    return None


# Función para calcular el EAN13 basado en datos de referencia y cantidad
def calculo_ean(complete_code):
    if len(complete_code) < 48:
        print("El código completo no tiene el formato esperado.")
        return ""

    fecha_part = complete_code[25:31]
    numero_bal = "25"
    ticket_part = str(int(complete_code)).zfill(4)[-4:]
    base_number = numero_bal + fecha_part + ticket_part

    ean_12 = base_number.zfill(12)  # Asegurarse de que tenga 12 dígitos

    # Calcular el dígito de control
    def calculate_check_digit(ean_12_code):
        sum_even = sum(int(ean_12_code[i]) for i in range(1, 12, 2))
        sum_odd = sum(int(ean_12_code[i]) for i in range(0, 12, 2))
        check_sum = (sum_odd + 3 * sum_even) % 10
        return (10 - check_sum) % 10

    check_digit = calculate_check_digit(ean_12)
    ean_13 = ean_12 + str(check_digit)
    return ean_13


# Crear una orden de venta
def create_sale_order(date_order, complete_code):
    ean_13 = calculo_ean(complete_code)
    ticket_part = str(int(complete_code)).zfill(4)[-4:]
    vendedor = complete_code[38:40]
    if not ean_13:
        print("No se pudo generar el código EAN13.")
        return None

    try:
        order_id = models.execute_kw(db, uid, password, 'etiquetado.etiqueta', 'create', [{
            'numero_etiqueta': ticket_part,
            'status': 'prepared',
            'ean13': ean_13,
            'company_id': int(id_sucursal),
            'lote':"",
        }])
        return order_id
    except Exception as e:
        print(f"Error al crear la orden de venta: {e}")
        return None


# Añadir productos a la orden de venta
def add_products_to_order(etiqueta_id, references_and_quantities):
    if not etiqueta_id:
        print("ID de etiqueta no válido.")
        return

    order_lines = []
    for reference, quantity in references_and_quantities:
        product_id = get_product_id_by_internal_reference(reference)
        if product_id is None:
            continue

        try:
            if isinstance(product_id, list):
                product_id = product_id[0] if product_id else None

            if product_id is None:
                print(f"No se encontró el producto para la referencia '{reference}'")
                continue

            product = models.execute_kw(db, uid, password, 'product.product', 'read', [product_id], {'fields': ['name', 'list_price']})
            if not product:
                print(f"Producto con ID {product_id} no encontrado.")
                continue

            product_name = product[0]['name']
            product_price = product[0]['list_price']

            # Agregar línea al nuevo modelo
            order_lines.append((0, 0, {
                'product_id': product_id,
                'cantidad': quantity,
                'precio_unitario': product_price,
                'etiqueta_id': etiqueta_id  # Usa el campo correcto para enlazar la línea a la etiqueta
            }))
        except Exception as e:
            print(f"Error al leer el producto con ID {product_id}: {e}")

    if order_lines:
        try:
            # Usar el nuevo modelo para añadir las líneas de productos
            models.execute_kw(db, uid, password, 'etiquetado.etiqueta', 'write', [[etiqueta_id], {
                'lineas_ids': order_lines  # Asegúrate de usar el campo correcto en tu nuevo modelo
            }])
            print(f"Productos añadidos a la etiqueta con ID: {etiqueta_id}")
        except Exception as e:
            print(f"Error al añadir productos a la etiqueta: {e}")
    else:
        print("No se añadieron productos a la etiqueta.")


# Función para procesar cualquier archivo .txt en el directorio de entrada
def process_files_in_directory(input_directory, processed_directory):
    # Asegúrate de que la carpeta de procesados exista
    if not os.path.exists(processed_directory):
        os.makedirs(processed_directory)

    # Procesar cada archivo .txt en el directorio de entrada
    for file_name in os.listdir(input_directory):
        if file_name.endswith('.txt'):
            file_path = os.path.join(input_directory, file_name)
            print(f"Procesando el archivo {file_path}...")

            references_and_quantities = extract_references_and_quantities(file_path)
            complete_code = extract_ref_complete(file_path)

            if not complete_code:
                print("Código completo no encontrado.")
                continue

            print("Referencias y cantidades extraídas del archivo:")
            for reference, quantity in references_and_quantities:
                print(f"Referencia: {reference}, Cantidad: {quantity}")

            order_id = create_sale_order(get_current_date(), complete_code)
            if order_id:
                print(f"Orden de venta creada con ID: {order_id}")
                add_products_to_order(order_id, references_and_quantities)
                shutil.move(file_path, os.path.join(processed_directory, file_name))
                print(f"Archivo {file_name} procesado y movido a la carpeta 'Ticket_Procesados'.")
            else:
                print("No se pudo crear la orden de venta.")


# Carpeta donde se encuentran los archivos .txt
input_directory = 'Tickets'
processed_directory = 'Ticket_Procesados'

# Bucle principal que verifica y procesa los archivos
while True:
    process_files_in_directory(input_directory, processed_directory)
    time.sleep(5)  # Espera 30 segundos antes de verificar nuevament
