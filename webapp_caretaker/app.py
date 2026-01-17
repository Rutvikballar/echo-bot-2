# Declaration: LLM's were used for code debugging and testing

import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import paho.mqtt.client as mqtt
import json
import threading
import os
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

import json
import os
app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)

CORS(app)

# Database Config
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'hospital.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Models

class Elder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    map_id = db.Column(db.Integer)
    gender = db.Column(db.String(10))
    age = db.Column(db.Integer)
    medical_history = db.Column(db.Text)

class Schedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    elder_id = db.Column(db.Integer, db.ForeignKey('elder.id'))
    time_slot = db.Column(db.String(20)) # morning, afternoon, evening
    medicine_name = db.Column(db.String(100))

class MedicationLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    elder_id = db.Column(db.Integer, db.ForeignKey('elder.id'))
    medicine_name = db.Column(db.String(100))
    delivery_type = db.Column(db.String(20)) # Manual/Scheduled
    timestamp = db.Column(db.DateTime, default=datetime.now)

class VitalLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    elder_id = db.Column(db.Integer, db.ForeignKey('elder.id'))
    temperature = db.Column(db.Float)
    blood_pressure = db.Column(db.String(20))
    heart_rate = db.Column(db.Integer)
    oxygen_sat = db.Column(db.Integer)
    is_emergency = db.Column(db.Boolean)
    emergency_reason = db.Column(db.String(100), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.now)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20)) # 'admin', 'nurse', 'elder'

# Init DB
def init_db():
    with app.app_context():
        db.create_all()
        
        # Seed Elders
        if not Elder.query.first():
            print("DB: Seeding Elders...")
            e1 = Elder(id=1, name="Elder 1", map_id=3, gender="Male", age=82, medical_history="Hypertension")
            e2 = Elder(id=2, name="Elder 2", map_id=4, gender="Female", age=79, medical_history="Diabetes")
            e3 = Elder(id=3, name="Elder 3", map_id=5, gender="Male", age=75, medical_history="Arthritis")
            db.session.add_all([e1, e2, e3])
            db.session.commit()
            print("DB: Elders seeded.")

        # Seed Schedule
        if not Schedule.query.first():
            print("DB: Seeding Schedule from JSON...")
            try:
                import json
                # Ensure filename matches EXACTLY where app.py is
                schedule_path = 'medication_schedule.json'  
                
                if os.path.exists(schedule_path):
                    with open(schedule_path, 'r') as f:
                        data = json.load(f)
                    
                    # Parse JSON: {"elder_1": {"morning": ["MedA"], ...}}
                    for elder_key, times in data.items():
                        try:
                            # Extract ID from "elder_1"
                            e_id = int(elder_key.split('_')[1]) 
                            
                            for slot, meds in times.items():
                                for med in meds:
                                    # Create row for each medicine
                                    new_sched = Schedule(elder_id=e_id, time_slot=slot, medicine_name=med)
                                    db.session.add(new_sched)
                        except:
                            pass # Skip malformed keys
                    
                    db.session.commit()
                    print("DB: Schedule seeded successfully.")
                else:
                    print(f"DB ERROR: Could not find {schedule_path}")
            except Exception as e:
                print(f"DB ERROR loading schedule: {e}")

        # Seed Users
        if not User.query.first():
            print("DB: Seeding Users with Hashed Passwords...")
            users = [
                User(username='admin', role='admin', password_hash=generate_password_hash('admin123')),
                User(username='nurse', role='nurse', password_hash=generate_password_hash('nurse123')),
                User(username='elder_1', role='elder', password_hash=generate_password_hash('1234')),
                User(username='elder_2', role='elder', password_hash=generate_password_hash('1234')),
                User(username='elder_3', role='elder', password_hash=generate_password_hash('1234'))
            ]
            db.session.add_all(users)
            db.session.commit()
            print("DB: Users seeded.")

# Initialize
init_db()

# Initialize SocketIO
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Robot state cache
robot_state = {
    'battery': 100,
    'temperature': 42,
    'speed': 15,
    'task': 'Idle',
    'position': {'x': 0, 'y': 0},
    'schedule_enabled': True
}

# MQTT Client
mqtt_client = None

def on_mqtt_connect(client, userdata, flags, rc):
    """MQTT Connect Callback."""
    if rc == 0:
        print("[OK] MQTT connected to broker successfully!")
        client.subscribe("robot/status")
        client.subscribe("robot/camera")
        client.subscribe("robot/map")
        client.subscribe("robot/schedule_status")
        client.subscribe("robot/emergency_alert")
        client.subscribe("robot/log/vitals")
        client.subscribe("robot/log/medication")
        print("[OK] Subscribed to: robot/status, robot/camera, robot/map, robot/schedule_status, robot/emergency_alert")
    else:
        print(f"[ERROR] MQTT connection failed with code: {rc}")

def on_mqtt_message(client, userdata, message):
    """MQTT Message Callback."""
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
            
        elif topic == 'robot/map':
            socketio.emit('map_update', {'data': payload})
            print("[MAP] Update sent to clients")

        elif topic == 'robot/schedule_status':
            schedule_data = json.loads(payload)
            # Update global state
            robot_state['schedule_enabled'] = schedule_data.get('enabled', True)
            socketio.emit('schedule_status', schedule_data)
            print(f"[SCHEDULE] Update sent: {schedule_data}")
        
        elif topic == "robot/emergency_alert":
            alert_data = json.loads(payload)
            socketio.emit('emergency_alert', alert_data)
            print(f"EMERGENCY ALERT: Forwarded to UI - Elder {alert_data.get('elder_id')}")

        # DB Logging
        elif topic == "robot/log/medication":
            try:
                data = json.loads(payload)
                with app.app_context():
                    log = MedicationLog(
                        elder_id=data['elder_id'],
                        medicine_name=data['medicine_name'],
                        delivery_type=data['delivery_type'],
                        timestamp=datetime.strptime(data['timestamp'], "%Y-%m-%d %H:%M:%S")
                    )
                    db.session.add(log)
                    db.session.commit()
                print(f"DB: Saved Medication Log for Elder {data['elder_id']}")
            except Exception as e:
                print(f"DB ERROR (Medication): {e}")

        elif topic == "robot/log/vitals":
            try:
                data = json.loads(payload)
                with app.app_context():
                    log = VitalLog(
                        elder_id=data['elder_id'],
                        temperature=data['temperature_c'],
                        blood_pressure=data['blood_pressure'],
                        heart_rate=data['heart_rate_bpm'],
                        oxygen_sat=data['oxygen_saturation_pct'],
                        is_emergency=data['is_emergency'],
                        emergency_reason=data.get('emergency_reason'),
                        timestamp=datetime.strptime(data['timestamp'], "%Y-%m-%d %H:%M:%S")
                    )
                    db.session.add(log)
                    db.session.commit()
                print(f"DB: Saved Vital Log for Elder {data['elder_id']}")
            except Exception as e:
                print(f"DB ERROR (Vitals): {e}")



            
    except Exception as e:
        print(f"[ERROR] Processing MQTT message on {topic}: {e}")

def on_mqtt_disconnect(client, userdata, rc):
    """MQTT Disconnect Callback."""
    print(f"[WARN] MQTT disconnected with code: {rc}")
    if rc != 0:
        print("  Attempting to reconnect...")

def setup_mqtt():
    """Initialize MQTT."""
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

# Security
def is_role_unlocked(required_role):
    """Check if role is unlocked."""
    unlocked = session.get('unlocked_roles', [])
    return required_role in unlocked

# Routes

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    next_url = request.args.get('next', '/admin')
    
    # Guess target role from URL
    target_role = ''
    if 'nurse' in next_url: target_role = 'nurse'
    elif 'elder' in next_url: target_role = 'elder_1' 
    elif 'admin' in next_url: target_role = 'admin'

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        next_target = request.form.get('next')

        user = User.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            if 'unlocked_roles' not in session:
                session['unlocked_roles'] = []
            
            roles = set(session['unlocked_roles'])
            roles.add(user.username) 
            session['unlocked_roles'] = list(roles)
            
            print(f"LOGIN SUCCESS: Unlocked {user.username}")
            return redirect(next_target if next_target else url_for('admin'))
        else:
            error = "Invalid username or password"

    return render_template('login.html', error=error, next_url=next_url, target_role=target_role)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
def index():
    return redirect(url_for('login', next='/admin'))

@app.route('/admin')
def admin():
    if not is_role_unlocked('admin'):
        return redirect(url_for('login', next='/admin'))
    return render_template('admin.html')

@app.route('/nurse')
def nurse():
    if not is_role_unlocked('nurse'):
        return redirect(url_for('login', next='/nurse'))
    return render_template('nurse.html')

@app.route('/elder/<int:elder_id>')
def elder(elder_id):
    if elder_id not in [1, 2, 3]:
        elder_id = 1
    
    required_user = f'elder_{elder_id}'
    if not is_role_unlocked(required_user):
        return redirect(url_for('login', next=f'/elder/{elder_id}'))
        
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
    """Send MQTT command."""
    data = request.json
    command = data.get('command', '')
    
    if command and mqtt_client:
        mqtt_client.publish('robot/command', command)
        print(f"[CMD] Command sent: {command}")
        return jsonify({'status': 'success', 'command': command})
    
    return jsonify({'status': 'error', 'message': 'No command provided or MQTT not connected'}), 400

@app.route('/api/status')
def get_status():
    """Get robot status."""
    return jsonify(robot_state)

@app.route('/api/speed', methods=['POST'])
def set_speed():
    """Set robot speed."""
    data = request.json
    speed = data.get('speed', 15)
    speed = max(5, min(25, int(speed)))
    
    if mqtt_client:
        mqtt_client.publish('robot/command', f'set_speed_{speed}')
        print(f"[CMD] Speed command sent: {speed}")
    return jsonify({'status': 'success', 'speed': speed})

@app.route('/api/logs', methods=['GET'])
def get_logs():
    with app.app_context():
        # Elders
        elders = Elder.query.all()
        elders_data = [{
            'id': e.id, 'name': e.name, 'age': e.age, 
            'history': e.medical_history
        } for e in elders]

        # Medication Logs
        med_logs = MedicationLog.query.order_by(MedicationLog.timestamp.desc()).limit(20).all()
        med_data = [{
            'time': m.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            'elder_id': m.elder_id,
            'type': m.delivery_type,
            'medicine': m.medicine_name
        } for m in med_logs]

        # Vital Logs
        vital_logs = VitalLog.query.order_by(VitalLog.timestamp.desc()).limit(20).all()
        vital_data = [{
            'time': v.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            'elder_id': v.elder_id,
            'temp': v.temperature,
            'bp': v.blood_pressure,
            'emergency': "YES" if v.is_emergency else "No"
        } for v in vital_logs]

        return jsonify({
            'elders': elders_data,
            'med_logs': med_data,
            'vital_logs': vital_data
        })

# SocketIO Handlers

@socketio.on('connect')
def handle_socket_connect():
    """Send state on connect."""
    print("[SOCKET] Client connected to SocketIO")
    emit('robot_status', robot_state)

@socketio.on('disconnect')
def handle_socket_disconnect():
    """Log disconnect."""
    print("[SOCKET] Client disconnected from SocketIO")

@socketio.on('send_command')
def handle_socket_command(data):
    """Handle frontend commands."""
    command = data.get('command', '')
    if command and mqtt_client:
        mqtt_client.publish('robot/command', command)
        print(f"[CMD] SocketIO command sent: {command}")
        emit('command_sent', {'command': command})

@socketio.on('nurse_request')
def handle_nurse_request(data):
    """Handle nurse request."""
    elder_id = data.get('elder_id', 1)
    request_type = data.get('request_type', 'Unknown')
    message = data.get('message', '')
    
    print(f"[ALERT] Elder {elder_id} requested: {request_type}")
    
    # Broadcast to nurse dashboard
    socketio.emit('elder_alert', {
        'elder_id': elder_id,
        'request_type': request_type,
        'message': message
    })

@socketio.on('ambulance_call')
def handle_ambulance_call(data):
    """Handle ambulance call."""
    caller = data.get('caller', 'Unknown')
    elder_id = data.get('elder_id', None)
    
    print(f"[EMERGENCY] AMBULANCE CALL from {caller}" + (f" for Elder {elder_id}" if elder_id else ""))
    
    # Emit ambulance dialog event
    socketio.emit('ambulance_dialog', {
        'caller': caller,
        'elder_id': elder_id,
        'location': 'Elder Care Home ECHO, 123 Care Street, Munich'
    })

# Main

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
