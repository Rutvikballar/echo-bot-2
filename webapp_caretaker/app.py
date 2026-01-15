"""
ECHO Robot Web Application - Flask Backend
Connects to caretaker.py controller via MQTT broker
"""

import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import paho.mqtt.client as mqtt
import json
import threading
import json
import os
app = Flask(__name__)
app.config['SECRET_KEY'] = 'echo-robot-secret-key'
CORS(app)

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Robot state cache
robot_state = {
    'battery': 100,
    'temperature': 42,
    'speed': 15,
    'task': 'Idle',
    'position': {'x': 0, 'y': 0},
    'schedule_enabled': True # Default state matches robot default
}

# MQTT Client (using paho-mqtt directly for better control)
mqtt_client = None

def on_mqtt_connect(client, userdata, flags, rc):
    """Callback when MQTT connects"""
    if rc == 0:
        print("[OK] MQTT connected to broker successfully!")
        client.subscribe("robot/status")
        client.subscribe("robot/camera")
        client.subscribe("robot/map")
        client.subscribe("robot/schedule_status")
        print("[OK] Subscribed to: robot/status, robot/camera, robot/map, robot/schedule_status")
    else:
        print(f"[ERROR] MQTT connection failed with code: {rc}")

def on_mqtt_message(client, userdata, message):
    """Callback when MQTT message received"""
    topic = message.topic
    try:
        payload = message.payload.decode('utf-8', errors='ignore')
        
        if topic == 'robot/status':
            status_data = json.loads(payload)
            robot_state.update(status_data)
            socketio.emit('robot_status', status_data)
            print(f"[STATUS] Battery={status_data.get('battery')}%, Task={status_data.get('task')}")
            
        elif topic == 'robot/camera':
            socketio.emit('camera_feed', {'image': payload})
            # Removed print to reduce console spam
            
        elif topic == 'robot/map':
            socketio.emit('map_update', {'data': payload})
            print("[MAP] Update sent to clients")

        elif topic == 'robot/schedule_status':
            schedule_data = json.loads(payload)
            # FIX: Update global state so new clients get the correct value on connect
            robot_state['schedule_enabled'] = schedule_data.get('enabled', True)
            socketio.emit('schedule_status', schedule_data)
            print(f"[SCHEDULE] Update sent: {schedule_data}")
            
    except Exception as e:
        print(f"[ERROR] Processing MQTT message on {topic}: {e}")

def on_mqtt_disconnect(client, userdata, rc):
    """Callback when MQTT disconnects"""
    print(f"[WARN] MQTT disconnected with code: {rc}")
    if rc != 0:
        print("  Attempting to reconnect...")

def setup_mqtt():
    """Initialize MQTT client"""
    global mqtt_client
    try:
        mqtt_client = mqtt.Client()
        mqtt_client.on_connect = on_mqtt_connect
        mqtt_client.on_message = on_mqtt_message
        mqtt_client.on_disconnect = on_mqtt_disconnect
        
        print("Connecting to MQTT broker at 127.0.0.1:1883...")
        mqtt_client.connect("127.0.0.1", 1883, 60)
        mqtt_client.loop_start()
        print("MQTT client loop started")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to connect to MQTT broker: {e}")
        print("  Make sure Mosquitto is running!")
        return False

# ==================== ROUTES ====================

@app.route('/')
def index():
    """Redirect to admin page by default"""
    return render_template('admin.html')

@app.route('/admin')
def admin():
    """Admin dashboard with full controls"""
    return render_template('admin.html')

@app.route('/nurse')
def nurse():
    """Nurse dashboard with navigation controls"""
    return render_template('nurse.html')

@app.route('/elder/<int:elder_id>')
def elder(elder_id):
    """Elder panel with emergency buttons"""
    if elder_id not in [1, 2, 3]:
        elder_id = 1
    return render_template('elder.html', elder_id=elder_id)

# Medication Schedule API
SCHEDULE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'medication_schedule.json')
@app.route('/api/medication_schedule', methods=['GET'])
def get_medication_schedule():
    if os.path.exists(SCHEDULE_FILE):
        with open(SCHEDULE_FILE, 'r') as f:
            return jsonify(json.load(f))
    else:
        # Default empty structure if file missing
        return jsonify({
            "elder_1": {"morning": [], "afternoon": [], "evening": []},
            "elder_2": {"morning": [], "afternoon": [], "evening": []},
            "elder_3": {"morning": [], "afternoon": [], "evening": []}
        })
@app.route('/api/medication_schedule', methods=['POST'])
def save_medication_schedule():
    data = request.json
    with open(SCHEDULE_FILE, 'w') as f:
        json.dump(data, f, indent=2)
    return jsonify({"status": "success"})

# ==================== REST API ====================

@app.route('/api/command', methods=['POST'])
def send_command():
    """Send command to robot via MQTT"""
    data = request.json
    command = data.get('command', '')
    
    if command and mqtt_client:
        mqtt_client.publish('robot/command', command)
        print(f"[CMD] Command sent: {command}")
        return jsonify({'status': 'success', 'command': command})
    
    return jsonify({'status': 'error', 'message': 'No command provided or MQTT not connected'}), 400


@app.route('/api/status')
def get_status():
    """Get current robot status"""
    return jsonify(robot_state)


@app.route('/api/speed', methods=['POST'])
def set_speed():
    """Set robot speed (5-25)"""
    data = request.json
    speed = data.get('speed', 15)
    speed = max(5, min(25, int(speed)))
    
    if mqtt_client:
        mqtt_client.publish('robot/command', f'set_speed_{speed}')
        print(f"[CMD] Speed command sent: {speed}")
    return jsonify({'status': 'success', 'speed': speed})


# ==================== SOCKETIO HANDLERS ====================

@socketio.on('connect')
def handle_socket_connect():
    """Send current state when client connects"""
    print("[SOCKET] Client connected to SocketIO")
    emit('robot_status', robot_state)


@socketio.on('disconnect')
def handle_socket_disconnect():
    """Log when client disconnects"""
    print("[SOCKET] Client disconnected from SocketIO")


@socketio.on('send_command')
def handle_socket_command(data):
    """Handle commands from frontend via SocketIO"""
    command = data.get('command', '')
    if command and mqtt_client:
        mqtt_client.publish('robot/command', command)
        print(f"[CMD] SocketIO command sent: {command}")
        emit('command_sent', {'command': command})


@socketio.on('nurse_request')
def handle_nurse_request(data):
    """Handle nurse request from elder"""
    elder_id = data.get('elder_id', 1)
    request_type = data.get('request_type', 'Unknown')
    message = data.get('message', '')
    
    print(f"[ALERT] Elder {elder_id} requested: {request_type}")
    
    # Broadcast to all connected clients (nurse dashboard)
    socketio.emit('elder_alert', {
        'elder_id': elder_id,
        'request_type': request_type,
        'message': message
    })


@socketio.on('ambulance_call')
def handle_ambulance_call(data):
    """Handle ambulance call - simulate 112 conversation"""
    caller = data.get('caller', 'Unknown')
    elder_id = data.get('elder_id', None)
    
    print(f"[EMERGENCY] AMBULANCE CALL from {caller}" + (f" for Elder {elder_id}" if elder_id else ""))
    
    # Emit ambulance dialog event
    socketio.emit('ambulance_dialog', {
        'caller': caller,
        'elder_id': elder_id,
        'location': 'Elder Care Home ECHO, 123 Care Street, Munich'
    })


# ==================== MAIN ====================

if __name__ == '__main__':
    print("=" * 50)
    print("  ECHO Robot Web Application")
    print("=" * 50)
    print()
    
    # Setup MQTT
    mqtt_success = setup_mqtt()
    
    if not mqtt_success:
        print()
        print("⚠ WARNING: Running without MQTT connection!")
        print("  Camera and map feeds will not work.")
        print("  Start Mosquitto and restart this app.")
        print()
    
    print()
    print(f"Starting web server on http://0.0.0.0:5050")
    print("Open http://localhost:5050 in your browser")
    print()
    
    socketio.run(app, host='0.0.0.0', port=5050, debug=True, use_reloader=False)
