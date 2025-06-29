from time import time, sleep
import requests
import serial
import csv
from datetime import datetime
import os

# API endpoints
url_post = "http://127.0.0.1:2000/post"
url_get = "http://127.0.0.1:2000/get"

# Tank configuration
TANK_HEIGHT = 15.0
SENSOR_OFFSET = 1.3
MIN_WATER_HEIGHT = 2.5
MAX_WATER_HEIGHT = 10.0
SENSOR_TO_BOTTOM = TANK_HEIGHT - SENSOR_OFFSET
SENSOR_TO_EMPTY = SENSOR_TO_BOTTOM - MIN_WATER_HEIGHT
SENSOR_TO_FULL = SENSOR_TO_BOTTOM - MAX_WATER_HEIGHT

# Global variables
current_setpoint = 85.0
last_upper = 0.0
last_lower = 0.0
last_pump = 0

# Serial configuration
SERIAL_PORT = '/dev/ttyUSB0'
SERIAL_BAUDRATE = 9600
SERIAL_TIMEOUT = 1
RETRY_DELAY = 5

# Timing intervals (seconds)
POST_INTERVAL = 1.0
GET_INTERVAL = 1.0
SERIAL_READ_INTERVAL = 0.1

def post(upper_percent, lower_percent, pump_percent):
    data = {
        "upper_percent": upper_percent,
        "pump_percent": pump_percent,
        "lower_percent": lower_percent,
    }
    try:
        res = requests.post(url_post, data, timeout=2.0)
        print(f"POST status: {res.status_code}")
    except requests.RequestException as e:
        print(f"POST error: {e}")

def get():
    global current_setpoint
    try:
        res = requests.get(url_get, timeout=2.0)
        if res.status_code == 200:
            data = res.json()
            
            # Process setpoint update
            if "setpoint" in data:
                new_sp = float(data["setpoint"])
                if new_sp != current_setpoint:
                    current_setpoint = new_sp
                    print(f"New setpoint: {current_setpoint}%")
                    return True
    except Exception as e:
        print(f"GET error: {str(e)}")
    return False

def sensor_to_percent(sensor_reading):
    reading = max(min(sensor_reading, SENSOR_TO_EMPTY), SENSOR_TO_FULL)
    return ((SENSOR_TO_EMPTY - reading) / (SENSOR_TO_EMPTY - SENSOR_TO_FULL)) * 100.0

def send_setpoint_to_arduino(ser, setpoint):
    try:
        ser.flushInput()
        ser.flushOutput()
        ser.write(f"SP:{setpoint}\n".encode('utf-8'))
        print(f"Setpoint sent: {setpoint}%")
    except serial.SerialException as e:
        print(f"Setpoint send error: {e}")
        raise

def establish_serial_connection():
    while True:
        try:
            print(f"Connecting to {SERIAL_PORT}...")
            ser = serial.Serial(
                port=SERIAL_PORT,
                baudrate=SERIAL_BAUDRATE,
                timeout=SERIAL_TIMEOUT
            )
            ser.flushInput()
            ser.flushOutput()
            print("Serial connected, initializing...")
            sleep(2)  # Allow Arduino reset
            send_setpoint_to_arduino(ser, current_setpoint)
            return ser
        except (serial.SerialException, OSError) as e:
            print(f"Connection failed: {e}")
            print(f"Retrying in {RETRY_DELAY}s...")
            sleep(RETRY_DELAY)

def log_pid_data_to_csv(upper_percent, pump_percent, current_setpoint, filename="pid_data.csv"):

    # Verifica se o arquivo já existe para decidir se precisa escrever o cabeçalho
    file_exists = os.path.isfile(filename)
    
    try:
        with open(filename, 'a', newline='') as csvfile:
            fieldnames = ['timestamp', 'PV', 'CO', 'setpoint']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            
            # Escreve o cabeçalho apenas se o arquivo for novo
            if not file_exists:
                writer.writeheader()
            
            # Escreve os dados com timestamp atual
            writer.writerow({
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'PV': f"{upper_percent:.1f}",
                'CO': f"{pump_percent:.1f}",
                'setpoint': f"{current_setpoint:.1f}"
            })
            
    except Exception as e:
        print(f"Erro ao escrever no arquivo CSV: {e}")


if __name__ == '__main__':
    ser = None
    try:
        ser = establish_serial_connection()
        
        # Timing controllers
        last_post_time = time()
        last_get_time = time()
        last_serial_time = time()
        
        while True:
            current_time = time()
            
            # Serial processing (optimized)
            if ser and ser.in_waiting and (current_time - last_serial_time) >= SERIAL_READ_INTERVAL:
                last_serial_time = current_time
                try:
                    while ser.in_waiting:
                        line = ser.readline().decode('utf-8', errors='replace').strip()
                        if "INTERLOCK" in line:
                            print("Arduino ready")
                            continue
                            
                        if line and (',' in line):
                            parts = line.split(',')
                            if len(parts) == 3:
                                try:
                                    last_upper = float(parts[0])
                                    last_lower = float(parts[1])
                                    last_pump = int(parts[2])
                                except ValueError:
                                    continue
                except serial.SerialException as e:
                    print(f"Serial read error: {e}")
                    ser.close()
                    ser = None
            
            # API posting
            if (current_time - last_post_time) >= POST_INTERVAL:
                upper_percent = sensor_to_percent(last_upper)
                lower_percent = sensor_to_percent(last_lower)
                pump_percent = ((last_pump - 16.0) / (50.0 - 16.0)) * 100.0 if last_pump > 0 else 0
                
                log_pid_data_to_csv(upper_percent, pump_percent, current_setpoint)

                print(f"PV: {upper_percent:.1f}%, "
                      f"CO: {pump_percent:.1f}%, "
                      f"Setpoint: {current_setpoint:.1f}%")
                post(upper_percent, lower_percent, pump_percent)
                last_post_time = current_time
            
            # Setpoint check
            if (current_time - last_get_time) >= GET_INTERVAL:
                if get() and ser:
                    try:
                        send_setpoint_to_arduino(ser, current_setpoint)
                    except serial.SerialException:
                        ser = None
                last_get_time = current_time
            
            # Reconnect if needed
            if not ser or not ser.is_open:
                ser = establish_serial_connection()
            
            sleep(0.05)  # Optimal CPU usage
            
    except KeyboardInterrupt:
        print("Program terminated")
    finally:
        if ser and ser.is_open:
            ser.close()
        print("Cleanup complete")