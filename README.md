# ECHO-BOT: Elder Care Home Operated Bot

A simulation of a service robot for elder care facilities. The robot provides autonomous navigation, medication delivery, real-time monitoring, and emergency response capabilities through a web-based control interface.

## System Architecture

```
[Web Dashboards] ←→ [Flask Server] ←→ [MQTT Broker] ←→ [Webots Robot]
```

The system uses a **decoupled architecture**:
- **MQTT Protocol** for robot-server communication
- **WebSockets (Socket.IO)** for server-browser real-time updates
- **REST API** for commands

## Features

- **Live Camera Feed** - Real-time video from robot camera
- **2D Map Visualization** - Dynamic environment mapping with robot position
- **Manual Control** - WASD keyboard control for direct robot movement
- **Battery Management** - Automatic low-battery docking
- **Medication Scheduling** - Automated delivery to elder rooms
- **Emergency Alerts** - Elder call buttons and ambulance simulation
- **Role-Based Dashboards** - Admin, Nurse, and Elder interfaces

## Requirements

- [Webots R2023b+](https://cyberbotics.com/)
- [Python 3.10+](https://www.python.org/downloads/)
- [Mosquitto MQTT Broker](https://mosquitto.org/download/)

## Installation

### 1. Clone the Repository
```bash
git clone https://git.oth-aw.de/c311/echo-bot-2.git
cd echo-bot-2
```

### 2. Install Python Dependencies
```bash
cd webapp_caretaker
pip install -r requirements.txt
```

### 3. Start Mosquitto Broker
```bash
# Windows
net start mosquitto

# Linux/Mac
mosquitto -v
```

## Running the System

### Step 1: Start Web Application
```bash
cd webapp_caretaker
python app.py
```
Server runs at: `http://localhost:5050`

### Step 2: Start Webots Simulation
1. Open Webots
2. Load: `webots/worlds/Alia_Delivery.wbt`
3. Press Play

### Step 3: Access Dashboards
| Dashboard | URL | Login |
|-----------|-----|-------|
| Admin | http://localhost:5050/admin | admin / admin123 |
| Nurse | http://localhost:5050/nurse | nurse / nurse123 |
| Elder 1 | http://localhost:5050/elder/1 | elder_1 / 1234 |
| Elder 2 | http://localhost:5050/elder/1 | elder_2 / 1234 |
| Elder 3 | http://localhost:5050/elder/1 | elder_3 / 1234 |

## Project Structure

```
care-robot-master/
├── webapp_caretaker/
│   ├── app.py                 # Flask backend server
│   ├── requirements.txt       # Python dependencies
│   ├── templates/
│   │   ├── admin.html         # Admin dashboard
│   │   ├── nurse.html         # Nurse dashboard
│   │   └── elder.html         # Elder panel
│   └── static/
│       ├── style.css          # Styling
│       └── script.js          # Frontend logic
├── webots/
│   ├── worlds/
│   │   └── Alia_Delivery.wbt  # Simulation world
│   └── controllers/
│       └── caretaker/
│           └── caretaker.py   # Robot controller
└── README.md
```

## MQTT Topics

| Topic | Direction | Content |
|-------|-----------|---------|
| `robot/status` | Robot → Server | Battery, temperature, speed, task |
| `robot/camera` | Robot → Server | Base64 JPEG frames |
| `robot/map` | Robot → Server | 2D grid array |
| `robot/command` | Server → Robot | Navigation commands |

## Multi-PC Setup (Optional)

To run Webots and Flask on different machines:

1. Run Mosquitto on one machine (e.g., `192.168.1.100`)
2. Update both `app.py` and `caretaker.py`:
   ```python
   mqtt_client.connect("192.168.1.100", 1883, 60)
   ```
3. Ensure port 1883 is open in firewall

## Technologies

- Python, Flask, Flask-SocketIO
- Paho-MQTT, Mosquitto
- HTML5, CSS3, JavaScript
- Socket.IO
- Webots Simulator
- SQLite

## Team

- **Repository:** https://git.oth-aw.de/c311/echo-bot-2

## License

This project is for educational purposes at OTH Amberg-Weiden.
