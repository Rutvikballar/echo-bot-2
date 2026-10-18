# ECHO-BOT Project Report
## Individual Contribution Report

---

**Project Title:** ECHO-BOT - Elder Care Home Operated Bot

**Student Name:** Rutvikkumar Babubhai Balar

**Git Repository:** https://git.oth-aw.de/47c0/care-robot

**Date:** January 2026

---

## Abstract

This report details the technical implementation of the external control system for the ECHO-BOT project. The primary focus was engineering a robust data pipeline that bridges the Webots simulation environment with a user-facing web dashboard, enabling low-latency video streaming and real-time telemetry over standard web protocols.

My contributions encompass three core technical implementation areas: (1) a multi-threaded Flask backend acting as a protocol bridge between MQTT and WebSockets to serve as the external controller, (2) comprehensive enhancements to the Webots robot controller including efficient Base64 image encoding, grid-based mapping, and autonomous battery management, and (3) a responsive, role-based frontend architecture capable of rendering high-frequency data streams for live interaction across Admin, Nurse, and Elder dashboards.

---

## 1. Introduction

### 1.1 Project Overview

The ECHO-BOT (Elder Care Home Operated Bot) project addresses a critical need in modern elder care facilities: the shortage of staff to provide constant, personalized attention to every resident. This project develops a mobile robot system capable of autonomous navigation, medication delivery, and real-time video monitoring. The robot operates within a simulated environment powered by Webots, where it can navigate a care home floor plan, respond to commands from caregivers, and manage its own power consumption through an intelligent docking system. The accompanying web-based interface allows staff to monitor the robot's status and issue commands without requiring physical proximity to the robot.

### 1.2 Project Goals

As defined in the project requirements, the system must fulfill the following objectives: (1) **Self-Monitoring**, where the robot tracks and reports its own battery level, temperature, speed, and current task status; (2) **Environment Monitoring**, providing visual feedback to users through a live camera feed and a dynamic 2D map of the surroundings; (3) **MQTT Communication**, transmitting all telemetry and receiving commands via the lightweight MQTT protocol; (4) **External Display**, presenting the robot's information on a user-accessible web dashboard; and (5) **User Commands**, enabling remote control through interactive buttons, keyboard inputs, and emergency actions.

### 1.3 Contributions

This report documents the development of the complete external control system and enhancements to the robot controller. The work is divided into three main components:
-   **Web Application Backend:** A Flask server with SocketIO for real-time bidirectional communication.
-   **User Interfaces:** Three role-based dashboards (Admin, Nurse, Elder) tailored to each user's needs.
-   **Robot Controller Enhancements:** MQTT integration, a battery management system with charging logic, keyboard-based manual control, and an autonomous idle-docking feature.

---

## 2. System Architecture

### 2.1 Decoupled System Architecture

The system relies on a **Decoupled Architecture** that strictly separates the robot control layer from the user interface layer. By using an MQTT broker as an intermediary, the system achieves:
1.  **Asynchronous Buffering:** The High-frequency robot control loop (32ms) is never blocked by network latency from web clients.
2.  **Scalability:** The robot publishes telemetry once to the broker, regardless of whether 1 or 100 admins are watching. The Flask server handles the fan-out to clients.
3.  **Resilience:** A crash in the web interface does not affect the robot's physical safety or autonomous behaviors.

The three-tier design enables robust real-time data flow:

![System Architecture](system%20architecture3.png)

### 2.2 MQTT Communication Protocol

The robot and server communicate through four MQTT topics:

| Topic | Direction | Data Content |
|-------|-----------|--------------|
| `robot/status` | Robot → Server | JSON: battery, temperature, speed, task, low_battery flag |
| `robot/camera` | Robot → Server | Base64-encoded JPEG image frames |
| `robot/map` | Robot → Server | JSON: 2D grid array with robot position |
| `robot/command` | Server → Robot | String: navigation and control commands |

The Flask server subscribes to these topics. Upon receiving a message, the payload is immediately broadcast to connected clients via Socket.IO events. This decoupled publish-subscribe model ensures that the robot is not burdened by the number of active web clients; it simply publishes once to the broker.

### 2.3 Project Files Summary

**Files Created:**

| File | Purpose |
|------|---------|
| `webapp_caretaker/app.py` | Flask backend with MQTT-SocketIO bridge |
| `webapp_caretaker/templates/admin.html` | Full admin dashboard |
| `webapp_caretaker/templates/nurse.html` | Nurse control panel |
| `webapp_caretaker/templates/elder.html` | Elder request interface |
| `webapp_caretaker/static/style.css` | Complete CSS styling |
| `webapp_caretaker/static/script.js` | Frontend JavaScript logic |
| `webapp_caretaker/requirements.txt` | Python dependencies 

**Files Modified:**

| File | Modifications |
|------|---------------|
| `webots/controllers/caretaker/caretaker.py` | MQTT integration, battery system, manual control, auto-docking, task status |

---

## 3. Web Application Implementation

### 3.1 Backend Server

The Flask backend (`app.py`) serves as the central hub connecting users to the robot:

**Key Components:**
- **Flask-SocketIO:** Enables persistent WebSocket connections for instant updates
- **paho-mqtt client:** Connects to MQTT broker (Mosquitto) for robot communication
- **Event handlers:** Automatically forward robot data to all connected browsers
**REST API Architecture:**
To facilitate direct control commands from the various dashboards, I implemented specific REST endpoints that map directly to robot functions. The `/api/command` POST endpoint acts as the primary gateway for navigation tasks. It accepts JSON payloads containing target definitions (e.g., `{"command": "goto_elder_1"}`), which are then sanitized and published to the `robot/command` MQTT topic. Additionally, a dedicated `/api/speed` endpoint allows for precise velocity adjustments, accepting integer values from 5 to 25.

**The MQTT-to-Socket Bridge:**
A core technical achievement was the creation of a seamless bridge between the asynchronous MQTT protocol and the real-time WebSocket layer. The server listens for specific MQTT topics and transforms them into browser-compatible events:
*   **Status Synchronization:** Messages on `robot/status` are parsed from JSON and emitted as `robot_status` events, carrying vital telemetry like battery voltage and task identifiers.
*   **Video Tunneling:** High-bandwidth image data on `robot/camera` is instantly forwarded as `camera_feed` events. By avoiding intermediary storage, we achieve near-real-time latency.
*   **Map Updates:** Grid data arriving on `robot/map` is relayed as `map_update` events, triggering a canvas redraw on the client.

The server utilizes Flask-SocketIO's broadcasting capability to push these updates to multiple clients simultaneously without the overhead of individual polling requests. This is crucial for maintaining synchronization across the Admin, Nurse, and Elder panels.

### 3.2 Admin Dashboard

The Admin interface provides comprehensive control and monitoring:

![Admin Dashboard Screenshot](admin_dashboard.png)

**Key UI Elements:**
- Statistics cards displaying battery, temperature, speed, and current task
- Live camera feed canvas with real-time video from robot
- 2D map visualization with color-coded rooms and robot position
- Manual control panel with directional buttons and WASD keyboard support
- Speed control slider with apply button
- Dark theme with glassmorphism effects for modern aesthetic

**Real-time displays** are powered by the Socket.IO event listeners. The **Battery Indicator** is not just a number but a dynamic progress bar that changes color (Green > 20%, Red ≤ 20%) based on the telemetry data. The **Temperature Reading** enables monitoring of the robot's internal state, while the **Current Speed** display confirms that command adjustments have been received by the controller. The **Active Task** status provides a human-readable text string (e.g., "Navigating to Kitchen"), allowing the administrator to verify autonomous behaviors.

**Control Panel Logic:**
I implemented a robust **Manual Mode** system. The toggle switch sends a specific `manual_mode` MQTT command that overrides the robot's autonomous pathfinding. Once active, the interface listens for keyboard events. I mapped the standard **WASD keys** to directional identifiers. A `keydown` event triggers movement, while the corresponding `keyup` event sends a `manual_stop` command. This "dead man's switch" logic ensures the robot stops immediately if the connection is lost or the user releases the controls. The **Speed Slider** writes the preferred velocity to the browser's `localStorage`, ensuring the setting persists across page reloads.

**Live Camera Pipeline:**
The camera feed is a critical feature that required efficient data handling:
1.  **Capture:** The robot controller extracts raw pixels from the Webots camera device.
2.  **Encoding:** This data is compressed into a JPEG byte stream and encoded as a Base64 string directly in Python.
3.  **Transmission:** The string is published to `robot/camera`.
4.  **Rendering:** The frontend receives the Base64 pointer and updates the `src` attribute of the image element (`<img src="data:image/jpeg;base64,...">`). This approach eliminates the need for complex video streaming protocols (like RTSP/HLS) while maintaining acceptable latency for local control.

**Real-time Mapping:**
The map is a dynamic JSON array representing a 2D grid. The frontend iterates through this array, rendering distinct HTML elements (divs) for walls (value 1), floor (0), the dock (7), and the robot itself (5). This ensures the map is always perfectly synced with the robot's internal world view.

### 3.3 Nurse Dashboard

Designed for healthcare staff with streamlined controls:

![Nurse Dashboard Screenshot](nurse_dashboard.png)

**Key UI Elements:**
- Compact status display showing battery and current task
- Elder navigation buttons (Elder 1, 2, 3) with medication delivery options
- Emergency actions row (Stop Task, Force Dock, Call Ambulance)
- Live camera and map visualization
- Alert notification system for elder requests

**Elder Navigation & Care:**
The interface simplifies complex navigation tasks into single-click actions. I created three distinct buttons for "Elder 1", "Elder 2", and "Elder 3". Clicking any of these triggers a modal dialog offering two distinct workflows:
1.  **Monitor Elder:** Captures a command to send the robot directly to the room for observation.
2.  **Give Medication:** Initiates a multi-step sequence where the robot first navigates to the medicine cabinet, pauses to simulate retrieval, and then proceeds to the Elder's room.

**Emergency Actions:**
To ensure safety, I implemented high-priority override commands. The **STOP TASK** button immediately clears the robot's pathfinding queue. **Force Dock** triggers the autonomous return-to-base logic regardless of battery level. The **Call Ambulance** feature initiates the same scripted emergency dialogue used in the Elder panel, allowing the nurse to simulate external help.

**Alert System:**
The dashboard listens for specific "alert" events emitted when an Elder presses their help button. This triggers a visual toast notification that slides into view, informing the nurse of which room needs assistance and allowing them to initiate a chat or send the robot immediately.

### 3.4 Elder Panel

Simplified interface for elderly users:

![Elder Panel Screenshot](elder_panel.png)

**Key UI Elements:**
- Large "Call Nurse" button with categorized request options
- Large "Call Ambulance" button for emergency situations
- High-contrast design with large text for accessibility
- Simulated 112 emergency call conversation feature

**Categorized Request System:**
To aid elders who may have difficulty typing, the "Call Nurse" button opens a simplified menu with pre-defined categories.
*   **Urgent:** Quick access to "Feeling Unwell" or "Fell Down".
*   **Daily Needs:** Common requests like "Need Water" or "Need Food".
*   **Comfort:** Environmental controls like "Adjust Room Temperature".
Selecting any option sends a structured message to the Nurse Dashboard, ensuring the request is clearly understood.

**Simulated Emergency Response:**
The "Call Ambulance" button demonstrates a critical safety feature. Upon activation, it triggers a JavaScript-driven script that simulates a connection to emergency services (112). I implemented a timed sequence of messages ("Connecting...", "Operator: 911, what is your emergency?", "Ambulance Dispatched") to provide reassurance and validation of the emergency protocol without requiring an actual phone line.

### 3.5 Frontend Implementation

The frontend architecture relied on a strong foundation of CSS variables for a consistent dark theme and glassmorphism effects, which allowed other team members to easily style specific buttons (`.btn-primary`, `.btn-danger`) while maintaining visual coherence.

The core logic, however, resides in the **JavaScript client (`script.js`)**, which handles the high-frequency data streams:

**1. Camera Feed Rendering (Base64 optimization):**
Video streaming is often a bottleneck in remote control systems. To solve this, I engineered a frame-by-frame transmission protocol:
1.  **Server-Side:** The Flask backend captures an image, constructs a base64 string, and emits it via the `camera_feed` event.
2.  **Client-Side:** The JS client avoids expensive DOM insertions. Instead, it instantiates a single `Image()` object in memory.
3.  **Synchronization:** The `img.onload` callback ensures that the canvas `ctx.drawImage()` operation only occurs when the frame is fully decoded.

```javascript
socket.on('camera_feed', (base64_data) => {
    const img = new Image();
    img.onload = () => {
        // Render only when data is ready to prevent flickering
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    };
    // Direct construction of the data URI scheme
    img.src = 'data:image/jpeg;base64,' + base64_data;
});
```

**2. Dynamic Map Rendering Algorithm:**
The map visualization is not a static image but a dynamically generated grid rendered on an HTML5 Canvas. The rendering pipeline involves several computational steps performed client-side to ensure responsive performance:

*   **Data Parsing:** The client receives a raw 2D array of integers via the `map_update` socket event.
*   **Color Mapping:** I implemented a strict lookup table to convert simulation integer values into visual representation colors:
    | ID | Object | Color | Hex Code |
    |----|--------|-------|----------|
    | 0 | Floor | Light Gray | `#f3f4f6` |
    | 1 | Wall | Dark Gray | `#374151` |
    | 5 | Robot | Blue | `#3b82f6` |
    | 7 | Charging Dock | Green | `#10b981` |
*   **Intelligent Cropping:** To maximize the viewing area, the algorithm scans the grid to identify the bounds of the active maze area, stripping away the unused outer padding integers (typically 0 or -1).
*   **Auto-Scaling:** The cropped grid is then mathematically scaled to fit the 100% width of the canvas container while preserving the aspect ratio. This ensures the map renders sharply on both desktop monitors and mobile screens.
*   **Optimized Draw Loop:** `requestAnimationFrame` is used to synchronize drawing with the browser's refresh rate, preventing UI freeze during heavy map updates.

```javascript
// Dynamic Map Rendering Loop
function drawMap(mapData) {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const cellWidth = canvas.width / mapData[0].length;
    const cellHeight = canvas.height / mapData.length;
    
    for (let y = 0; y < mapData.length; y++) {
        for (let x = 0; x < mapData[y].length; x++) {
            const cellValue = mapData[y][x];
            // Apply color map
            if (cellValue === 1) ctx.fillStyle = '#374151'; // Wall
            else if (cellValue === 7) ctx.fillStyle = '#10b981'; // Dock
            else if (cellValue === 5) ctx.fillStyle = '#3b82f6'; // Robot
            else ctx.fillStyle = '#f3f4f6'; // Floor
            
            ctx.fillRect(x * cellWidth, y * cellHeight, cellWidth, cellHeight);
        }
    }
}
```

**3. Resilient Connection Logic:**
```javascript
const socket = io({
    reconnection: true,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 5000,
    reconnectionAttempts: Infinity
});
```
This configuration ensures that if the Flask server restarts or the network fluctuates, the client automatically attempts to reconnect, maintaining the critical control link without user intervention.

**4. Input Handling Bridge:**
For manual control, I implemented a bridge that captures standard DOM events (`keydown`, `mousedown`) and throttles them before emitting socket events. This prevents flooding the MQTT broker with redundant commands while holding a key down.

```javascript
// Dedicated Event Listeners for Manual Control
document.addEventListener('keydown', (e) => {
    if (!manualModeActive) return;
    
    if (e.key === 'w') sendCommand('manual_forward');
    else if (e.key === 's') sendCommand('manual_back');
    else if (e.key === 'a') sendCommand('manual_left');
    else if (e.key === 'd') sendCommand('manual_right');
});

document.addEventListener('keyup', (e) => {
    if (['w','a','s','d'].includes(e.key)) {
        sendCommand('manual_stop');
    }
});
```

---

## 4. Robot Controller Enhancements

### 4.1 MQTT Integration

I extended the Webots controller with a robust MQTT client that handles connection lifecycle management:
```python
self.mqtt_client = mqtt.Client()
self.mqtt_client.on_connect = self._on_mqtt_connect
self.mqtt_client.on_message = self._on_mqtt_message
self.mqtt_client.connect("localhost", 1883)
```

**Command Processing Logic:**
The `_on_mqtt_message` callback acts as the central dispatch hub. It parses the incoming topics and routes execution to specific helper functions. For instance, receiving `manual_mode` triggers an immediate state transition and path clearing, whereas navigation commands like `goto_elder_1` are queued for the A* pathfinder.

```python
def _on_mqtt_message(self, client, userdata, msg):
    try:
        command = msg.payload.decode()
        if command == "manual_mode":
            self._switch_to_manual()
        elif command == "disable_manual_mode":
            self._disable_manual_mode()
        elif command.startswith("goto_elder"):
            elder_id = command.split("_")[-1]
            self._navigate_to(int(elder_id), f"Navigating to Elder {elder_id}")
        elif command == "stop":
             self._emergency_stop()
    except Exception as e:
        print(f"Error processing command: {e}")
```

This separation ensures that high-priority safety commands (like Emergency Stop) are processed instantly even if the robot is calculating a path.

**Status Publishing:**
To maintain state synchronization with the web interface, the controller runs a scheduled task:

```python
def _publish_status(self):
    status = {
        "battery": self.battery,
        "temperature": self.temperature,
        "speed": int(self.max_velocity),
        "task": self.current_task,
        "low_battery": self.battery <= 20
    }
    self.mqtt_client.publish("robot/status", json.dumps(status))
```

**Publishing Schedule:**
- Status: Every 10 simulation steps
- Camera: Every simulation step (maximum frame rate)
- Map: Every 5 simulation steps

**Task Status System:**
To provide granular feedback to the user, I implemented a status tracking system that maps internal robot states to human-readable strings sent via MQTT:

| Status | Meaning |
|--------|---------|
| `Idle` | Robot has no active task |
| `Manual Drive Mode` | Robot in manual mode, ready for control |
| `Navigating to Elder X` | Moving to an elder's room |
| `Going to Medicine Cabinet` | Fetching medicine |
| `Returning to Dock` | Going to charging station |
| `Auto-docking (Idle)` | Triggered after 100 steps of inactivity |
| `Auto-docking (Low Battery)` | Triggered when battery dropped below 20% |
| `Charging (X%)` | Currently dock-charging |
| `Emergency Stop` | Stopped by user command |

### 4.2 Battery Management System

Implemented realistic battery simulation with three states:

**Draining Logic:**
The discharge model creates a linear dependency between motor usage and battery life. During every simulation step where `velocity > 0`, a counter increments. The system is calibrated such that **every 50 cumulative active steps results in a 1% drop**.

**Charging & Dock Detection:**
I implemented a proximity-based charging state. When the robot enters a cell defined as "Dock" (value 7 in the grid) and velocity is zero, the state switches to `CHARGING`.

```python
# Charging Logic Snippet
if self.map[robot_y][robot_x] == 7 and self.current_velocity < 0.01:
    self.is_charging = True
    if self.battery < 100:
        self.battery += 0.1  # Charge rate
        if self.battery % 10 == 0:
            print(f"Charging... {self.battery}%")
    else:
        self.battery = 100
        self.is_charging = False # Auto-undock when full
```

This bypasses the drain logic and **increments battery percentage by 1% every 10 simulation steps** until full.

**Low Battery Failsafe:**
A conditional check runs in the main control loop: `if self.battery <= 20 and not self.docking_active:`. This sets the `low_battery` flag to `True` (triggering the popup on the frontend) and immediately pre-empts current tasks to initiate the auto-docking sequence.

### 4.3 Manual Control System

While Webots provides low-level physics for motor velocity, my primary contribution was **bridging this control to the web interface** safely. I implemented a command handler that overrides the autonomous navigation logic when a manual override is active.

**Mode Management:**
- `manual_mode` command: Activates manual control, stops current navigation, and clears the path buffer.
- `disable_manual_mode` command: Returns to idle state, allowing the autonomous logic to resume.

**Movement Logic:**
Each directional command sets the motor velocity directly. The implementation ensures that manual inputs override any existing autonomous behaviors:

```python
def _manual_move(self, direction):
    self.mode = "manual"
    if direction == "forward":
        self.left_motor.setVelocity(self.max_velocity)
        self.right_motor.setVelocity(self.max_velocity)
    # ... logic repeats for back, left, right ...
```

**Safety Implementation:**
The main loop only applies keyboard velocities when keys are pressed, allowing MQTT commands to persist without being overwritten:

```python
if v_l != 0.0 or v_r != 0.0:  # Only if keyboard input
    self.left_motor.setVelocity(v_l)
    self.right_motor.setVelocity(v_r)
# Otherwise, MQTT-set velocities remain active
```

### 4.4 Autonomous Docking System

Intelligent return-to-dock functionality:

**Idle State Logic:**
The robot controller monitors for inactivity to ensure the robot is always ready for service. The system tracks an `idle_timer` that increments whenever:
1.  Velocity is near-zero (< 0.1).
2.  Task queue is empty.
3.  Robot is not already charging.

This applies to multiple states including "Idle", "Manual Mode (Idle)", and "Emergency Stop - Idle".

**Trigger Condition:**
If `idle_timer > 100` (simulation steps), the state transitions to `RETURNING_TO_DOCK`. The controller utilizes the shared A* pathfinding helper to calculate a route from the current coordinates to the fixed Dock coordinates `(0,0)`.

```python
if self.idle_counter >= 100:
    print(f"IDLE for 100 steps: Auto-docking initiated")
    self._navigate_to(7, "Auto-docking (Idle)")
    self.idle_counter = 0
```

**Dock Detection Algorithm:**
To ensure precise docking, the robot doesn't just rely on odometry. It actively scans the local 7x7 map grid. Upon detecting the specific integer value representing the dock, it fine-tunes its final approach to ensure it "parks" correctly, triggering the charging state.

---

## 5. Conclusion

My contributions to the ECHO-BOT project successfully addressed all requirements defined in the project description:

| Requirement | Implementation |
|-------------|----------------|
| Robot self-monitoring | Battery, temperature, speed, task status tracking |
| Environment monitoring | Live camera feed and 2D map visualization |
| MQTT communication | Bidirectional data flow via robot/status, camera, map, command topics |
| External display | Three web dashboards with real-time updates |
| User commands | Navigation buttons, manual WASD/mouse control, emergency actions |

The implemented system transforms the ECHO robot into a fully integrated component of a smart elder care infrastructure, enabling caregivers to remotely monitor and control the robot while maintaining autonomous capabilities like battery management and idle docking.

**Technologies Used:** Python, Flask, Flask-SocketIO, paho-mqtt, MQTT (Mosquitto), HTML5, CSS3, JavaScript, Socket.IO, Webots

### 5.1 Future Work

While the current system meets all project requirements, several enhancements could further improve its utility in real-world care scenarios:

1.  **Video Streaming Optimization:**
    Currently, the system uses Base64 encoding for video frames, which is sufficient for local networks but bandwidth-intensive. Future iterations could implement WebRTC for peer-to-peer streaming, significantly reducing latency and server load for remote access over the internet.

2.  **Advanced Path planning:**
    The A* algorithm is effective for static environments. Integrating D* (Dynamic A*) or RRT (Rapidly-exploring Random Tree) would allow the robot to recalculate paths in real-time around moving obstacles, such as people walking in the care home.

3.  **Voice Interaction:**
    Integrating a voice recognition API (like Web Speech API) into the Elder Dashboard would allow residents to speak commands ("Call Nurse") instead of using the touchscreen, greatly enhancing accessibility for users with limited motor control.

4.  **Cloud Analytics:**
    Offloading robot telemetry to a cloud database (AWS/Azure) would enable long-term trend analysis. Care facility managers could view historical data on battery usage, most successful routes, and response times to optimize the robot's deployment schedule.

---

---

## 6. Declaration of AI Usage

In accordance with academic integrity guidelines, I hereby declare the use of Generative AI tools (Large Language Models) during the development of this project. AI assistance was utilized for:
1.  **Debugging:** Analyzing error logs and suggesting potential fixes for syntax errors in the Flask-SocketIO implementation.
2.  **Refactoring:** Optimizing specific code blocks (e.g., the map rendering loop) for better variable naming and readability.
3.  **Documentation:** Assisting in the drafting and language refinement of this technical report to ensure professional clarity.

The core architectural design, logical flow of the robot controller, and the final implementation of all features detailed in this report are my own original work. All AI-generated suggestions were critically reviewed, validated, and adapted to fit the specific constraints of the ECHO-BOT system.

---

## References

[1] J. Galeas, A. Tudela, Ó. Pons, J. P. Bandera, and A. Bandera, "Design of a Cyber-Physical System-of-Systems Architecture for Elderly Care at Home," *Electronics*, vol. 13, no. 23, p. 4583, Jan. 2024, doi: 10.3390/electronics13234583.

---

**Submitted by:** Rutvikkumar Babubhai Balar
