# ECHO Robot Web Application

Web-based control interface for the ECHO Elder Care Robot system.

## Setup

### 1. Install Dependencies

```bash
cd webapp_caretaker
pip install -r requirements.txt
```

### 2. Start MQTT Broker

Make sure Mosquitto or another MQTT broker is running on port 1883:

```bash
# Windows (if Mosquitto is installed)
net start mosquitto

# Or run mosquitto directly
mosquitto -v
```

### 3. Start Flask Server

```bash
python app.py
```

The server will start on `http://localhost:5050`

### 4. Start Webots Simulation

Open your Webots world and run the `caretaker.py` controller.

## Usage

Navigate to the following pages in your browser:

- **Admin Dashboard**: `http://localhost:5050/admin`
  - View all robot stats (battery, temp, speed, task)
  - Live camera feed and map
  - Manual control (arrow buttons)
  - Speed slider (5-25)
  - Force dock button

- **Nurse Dashboard**: `http://localhost:5050/nurse`
  - View battery and current task
  - Live camera feed and map
  - Elder navigation buttons (with Monitor/Medicine options)
  - Emergency stop and force dock
  - Call Ambulance (simulated 112 conversation)

- **Elder Panels**: 
  - `http://localhost:5050/elder/1`
  - `http://localhost:5050/elder/2`
  - `http://localhost:5050/elder/3`
  - Call Nurse (with categorized help options)
  - Call Ambulance

## MQTT Topics

| Topic | Direction | Description |
|-------|-----------|-------------|
| `robot/command` | Web → Robot | Send navigation/control commands |
| `robot/status` | Robot → Web | Battery, temp, speed, task, position |
| `robot/camera` | Robot → Web | Base64 encoded camera image |
| `robot/map` | Robot → Web | JSON array of map grid |

## Commands

| Command | Description |
|---------|-------------|
| `goto_elder_1`, `goto_elder_2`, `goto_elder_3` | Navigate to elder |
| `goto_medicine` | Go to medicine cabinet |
| `goto_medicine_elder_<1/2/3>` | Get medicine, then deliver |
| `goto_dock` | Return to charging dock |
| `stop` | Emergency stop |
| `manual_forward/back/left/right` | Manual control |
| `set_speed_<5-25>` | Set robot speed |
