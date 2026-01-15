"""
ECHO Robot Caretaker Controller
Extended with MQTT integration for web application communication
"""

import sys
import os

# ==================== WEBOTS RUNTIME CONFIGURATION ====================
# This allows running the script directly without a launcher

# 1. Ensure WEBOTS_HOME is set
if "WEBOTS_HOME" not in os.environ:
    # Default installation path on Windows
    default_webots_home = r"C:\Program Files\Webots"
    if os.path.exists(default_webots_home):
        print(f"Configuring Webots environment from: {default_webots_home}")
        os.environ["WEBOTS_HOME"] = default_webots_home
    else:
        print("WARNING: Webots installation not found at default location. Please set WEBOTS_HOME environment variable.")

# 2. Add Webots Python API to sys.path (CRITICAL)
# Do this even if WEBOTS_HOME was already set by the system
if "WEBOTS_HOME" in os.environ:
    webots_home = os.environ["WEBOTS_HOME"]
    lib_path = os.path.join(webots_home, "lib", "controller", "python")
    
    # Check if we need to add it
    if os.path.exists(lib_path):
        if lib_path not in sys.path:
            sys.path.append(lib_path)
            print(f"Added Webots Python API to path: {lib_path}")
    else:
        print(f"WARNING: Could not find Webots Python library at: {lib_path}")

# Imports
try:
    from controller import Supervisor
except ImportError as e:
    print(f"CRITICAL ERROR: Could not import 'controller' library. Error: {e}")
    if "WEBOTS_HOME" in os.environ:
        print(f"WEBOTS_HOME is set to: {os.environ['WEBOTS_HOME']}")
    print(f"Current sys.path: {sys.path}")
    sys.exit(1)

import math
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import os
import heapq
import json
import base64
import threading
import random
import time
from typing import Dict, List, Optional, Tuple

# MQTT imports
try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
    print("Warning: paho-mqtt not installed. MQTT features disabled.")
    MQTT_AVAILABLE = False


class BurgerNavigator:
    def __init__(self, map_file="burger_map.npz"):
        self.robot = Supervisor()
        self.timestep = int(self.robot.getBasicTimeStep())

        # Resolve map file robustly (relative paths depend on process CWD).
        self.map_file = self._resolve_map_file(map_file)
        
        # Keyboard for WASD
        self.keyboard = self.robot.keyboard
        self.keyboard.enable(self.timestep)

        # Motors (Same config as your mapper)
        self.left_motor = self.robot.getDevice("left wheel motor")
        self.right_motor = self.robot.getDevice("right wheel motor")
        self.left_motor.setPosition(float('inf'))
        self.right_motor.setPosition(float('inf'))
        self.left_motor.setVelocity(0.0)
        self.right_motor.setVelocity(0.0)
        self.max_velocity = 15  # Default speed (range 5-25)
        self.turn_velocity = self.max_velocity / 5.0

        # Ground Truth Pose Tracking
        self.robot_node = self.robot.getSelf()
        self.robot_node.enablePoseTracking(self.timestep)
        self.trans_field = self.robot_node.getField("translation")

        # Camera setup (optional)
        self.camera = None
        try:
            self.camera = self.robot.getDevice("camera")
            if self.camera:
                self.camera.enable(self.timestep)
                print("Camera enabled")
        except Exception as e:
            print(f"Camera not available: {e}")

        # 1. Load the Static Map
        if os.path.exists(self.map_file):
            data = np.load(self.map_file)
            self.static_map = data['grid']
            self.grid_size = self.static_map.shape[0]
            # Use 0.04 as default cell size based on your previous code
            self.cell_size = 0.04 
            print(f"Successfully loaded map: {self.map_file}")
        else:
            print(f"Error: {self.map_file} not found!")
            exit()

        # Traversability: allow free and targets; block walls/padding/unknown.
        self.passable = (self.static_map != 1) & (self.static_map != 2) & (self.static_map != -1)

        # Control / autonomy state
        self.mode = "manual"  # "manual" | "auto"
        self.current_target_value: Optional[int] = None
        self.current_path: List[Tuple[int, int]] = []
        self.path_index = 0
        self.current_task = "Idle"

        # A* tuning: prefer straight paths by penalizing turns.
        self.turn_penalty = 6.0

        self.step_counter = 0
        
        # ==================== BATTERY & TEMPERATURE ====================
        self.battery = 100  # Integer percentage
        self.temperature = 42  # Base temperature
        self.battery_drain_counter = 0
        self.auto_dock_triggered = False
        self.idle_counter = 0  # Track idle steps for auto-dock
        
        # ==================== MQTT SETUP ====================
        self.mqtt_client = None
        self.mqtt_command = None
        self.mqtt_lock = threading.Lock()
        
        if MQTT_AVAILABLE:
            self._setup_mqtt()
        
        # Multi-step task queue (for medicine + elder delivery)
        self.task_queue: List[int] = []
        
        # Emergency Override Flag (Admin Stop)
        self.emergency_stop_active = False

        # ==================== MEDICATION SCHEDULE & TIME ====================
        # Simulation Time: Starts at 06:00 (Morning)
        # 1 real second = 1 simulation minute (slower, more realistic for robot speed)
        self.sim_hour = 6
        self.sim_minute = 0
        self.last_time_update = time.time()
        self.medication_schedule = {}
        # Path to JSON in webapp folder (relative to this controller)
        self.schedule_file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../webapp_caretaker/medication_schedule.json"))
        self._load_medication_schedule()
        
        # Track if we already delivered for a time slot today
        self.delivery_status = {"morning": False, "afternoon": False, "evening": False}
        
        # Schedule Enable Flag (User Toggle / Auto-Stop)
        self.schedule_enabled = True
        
        # Flag to distinguish Automatic Schedule tasks from Manual Button 'Auto' tasks
        # True = Robot is executing a Time-Triggered Schedule
        # False = Robot is executing a Manual Command (e.g. 'Give Medication' button)
        self.is_scheduled_task = False

    def _resolve_map_file(self, map_file: str) -> str:
        if os.path.isabs(map_file) and os.path.exists(map_file):
            return map_file

        # Try relative to this controller.
        here = os.path.dirname(__file__)
        candidate = os.path.join(here, map_file)
        if os.path.exists(candidate):
            return candidate

        # Try common location: mapper controller output.
        candidate = os.path.normpath(os.path.join(here, "..", "mapper", map_file))
        if os.path.exists(candidate):
            return candidate

        return map_file

    # ==================== MQTT METHODS ====================
    def _setup_mqtt(self):
        """Initialize MQTT client and connect to broker"""
        try:
            self.mqtt_client = mqtt.Client()
            self.mqtt_client.on_connect = self._on_mqtt_connect
            self.mqtt_client.on_message = self._on_mqtt_message
            self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
            
            self.mqtt_client.connect("127.0.0.1", 1883, 60)
            self.mqtt_client.loop_start()
            print("MQTT client started, connecting to 127.0.0.1:1883")
        except Exception as e:
            print(f"MQTT connection failed: {e}")
            self.mqtt_client = None

    def _on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print("Connected to MQTT broker")
            client.subscribe("robot/command")
            print("Subscribed to robot/command")
        else:
            print(f"MQTT connection failed with code: {rc}")

    def _on_mqtt_message(self, client, userdata, msg):
        try:
            command = msg.payload.decode('utf-8')
            print(f"Received MQTT command: {command}")
            with self.mqtt_lock:
                self.mqtt_command = command
        except Exception as e:
            print(f"Error processing MQTT message: {e}")

    def _on_mqtt_disconnect(self, client, userdata, rc):
        print("Disconnected from MQTT broker")

    def _publish_status(self):
        """Publish robot status to MQTT"""
        if not self.mqtt_client:
            return
            
        status = {
            "battery": self.battery,
            "temperature": self.temperature,
            "speed": int(self.max_velocity),
            "task": self.current_task,
            "low_battery": self.battery <= 20,
            "position": {
                "x": round(self.trans_field.getSFVec3f()[0], 2),
                "y": round(self.trans_field.getSFVec3f()[1], 2)
            },
            "time": f"{self.sim_hour:02d}:{self.sim_minute:02d}"
        }
        
        try:
            self.mqtt_client.publish("robot/status", json.dumps(status))
        except Exception as e:
            print(f"Error publishing status: {e}")

    def _publish_schedule_status(self):
        """Publish schedule enabled/disabled status"""
        if self.mqtt_client:
            try:
                self.mqtt_client.publish("robot/schedule_status", json.dumps({"enabled": self.schedule_enabled}))
            except Exception as e:
                print(f"Error publishing schedule status: {e}")

    def _publish_camera(self):
        """Capture camera image and publish as Base64"""
        if not self.mqtt_client or not self.camera:
            return
            
        try:
            # Get camera image
            image = self.camera.getImage()
            if image:
                width = self.camera.getWidth()
                height = self.camera.getHeight()
                
                # Convert to numpy array (BGRA format)
                img_array = np.frombuffer(image, dtype=np.uint8).reshape((height, width, 4))
                
                # Convert BGRA to RGB
                rgb_array = img_array[:, :, [2, 1, 0]]
                
                # Encode as JPEG using PIL if available, otherwise raw
                try:
                    from PIL import Image
                    import io
                    pil_img = Image.fromarray(rgb_array)
                    buffer = io.BytesIO()
                    pil_img.save(buffer, format='JPEG', quality=60)
                    img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                    self.mqtt_client.publish("robot/camera", img_base64)
                except ImportError:
                    # Fallback: send raw RGB data encoded
                    img_base64 = base64.b64encode(rgb_array.tobytes()).decode('utf-8')
                    self.mqtt_client.publish("robot/camera", img_base64)
        except Exception as e:
            print(f"Error publishing camera: {e}")

    def _publish_map(self):
        """Publish map data with robot position"""
        if not self.mqtt_client:
            return
            
        try:
            pos = self.trans_field.getSFVec3f()
            mi, mj = self.world_to_grid(pos[0], pos[1])
            
            # Create display map with robot position
            display_map = self.static_map.copy()
            
            if 0 <= mi < self.grid_size and 0 <= mj < self.grid_size:
                r = 5
                i_min, i_max = max(0, mi - r), min(self.grid_size, mi + r + 1)
                j_min, j_max = max(0, mj - r), min(self.grid_size, mj + r + 1)
                display_map[i_min:i_max, j_min:j_max] = 8
            
            # Convert to list and publish as JSON
            map_list = display_map.tolist()
            self.mqtt_client.publish("robot/map", json.dumps(map_list))
        except Exception as e:
            print(f"Error publishing map: {e}")

    def _process_mqtt_command(self):
        """Process any pending MQTT command"""
        command = None
        with self.mqtt_lock:
            if self.mqtt_command:
                command = self.mqtt_command
                self.mqtt_command = None
        
        if not command:
            return
            
        print(f"Processing command: {command}")
        
        if command == "stop":
            # Nurse Stop (Standard stop, allows auto-dock if idle)
            if self.is_scheduled_task: self._reset_current_delivery_status() # FIX
            self.schedule_enabled = False # Auto-disable schedule
            self.mode = "manual"  # Force manual mode to stop auto navigation
            self.current_path = [] # Clear current path
            self.path_index = 0
            self.task_queue = [] # Clear pending tasks
            self.left_motor.setVelocity(0.0)
            self.right_motor.setVelocity(0.0)
            self.current_task = "Stopped (Idle)"
            print("Nurse STOP command received - Schedule Disabled, Queue Cleared")
            self._publish_schedule_status()
        elif command == "admin_stop":
            # Admin Stop (Emergency stop, PREVENTS auto-dock)
            if self.is_scheduled_task: self._reset_current_delivery_status() # FIX
            self.schedule_enabled = False # Auto-disable schedule
            self.task_queue = [] # Clear pending tasks
            self._emergency_stop()
            self._publish_schedule_status()
        elif command == "enable_schedule":
            self.schedule_enabled = True
            print("Schedule ENABLED by user")
            self._publish_schedule_status()
        elif command == "disable_schedule":
            self.schedule_enabled = False
            print("Schedule DISABLED by user")
            self._publish_schedule_status()
            
            # Robust Stop: If robot is in AUTO mode AND it's a SCHEDULE Task, ABORT IT.
            # This prevents stopping manual "Give Medicine" actions when toggling schedule.
            if self.mode == "auto" and self.is_scheduled_task:
                 print("Aborting SCHEDULE task (Schedule Disabled)...")
                 self._reset_current_delivery_status() # FIX: Reset status so it can retry if re-enabled
                 self.mode = "manual"
                 self.is_scheduled_task = False # Reset flag
                 self.current_path = []
                 self.path_index = 0
                 self.task_queue = []
                 self.left_motor.setVelocity(0.0)
                 self.right_motor.setVelocity(0.0)
                 self.current_task = "Stopped (Schedule Disabled)"
                 self._publish_schedule_status()
        elif command == "manual_mode":
            self.mode = "manual"
            self.is_scheduled_task = False # Reset flag
            self._switch_to_manual()
        elif command == "disable_manual_mode":
            self._disable_manual_mode()
        elif command == "goto_elder_1":
            self.is_scheduled_task = False # Manual Action
            self._navigate_to(3, "Navigating to Elder 1")
        elif command == "goto_elder_2":
            self.is_scheduled_task = False # Manual Action
            self._navigate_to(4, "Navigating to Elder 2")
        elif command == "goto_elder_3":
            self.is_scheduled_task = False # Manual Action
            self._navigate_to(5, "Navigating to Elder 3")
        elif command.startswith("monitor_elder_"):
            try:
                elder_num = int(command.split("_")[-1])
                target = 2 + elder_num # 3, 4, 5
                self.is_scheduled_task = False
                self._navigate_to(target, f"Monitoring Elder {elder_num}")
                # Removed explicit dock command, will auto-dock on idle
                self.task_queue = [] 
            except ValueError:
                print(f"Invalid monitor command: {command}")

        elif command.startswith("give_medicine_"):
            # Format: give_medicine_ID_Name
            parts = command.split("_")
            if len(parts) >= 4:
                try:
                    elder_num = int(parts[2])
                    med_name = "_".join(parts[3:]) # Rejoin name parts
                    
                    print(f"Giving medicine [{med_name}] to the Elder {elder_num}")
                    
                    self.is_scheduled_task = False
                    self._navigate_to(6, f"Getting Medicine ({med_name})")
                    # Queue: Elder only. Auto-dock will happen on idle.
                    self.task_queue = [2 + elder_num]
                except ValueError:
                    print(f"Invalid medicine command parsing: {command}")
            else:
                 print(f"Invalid medicine command format: {command}")

        elif command == "goto_medicine":
            self.is_scheduled_task = False # Manual Action
            self._navigate_to(6, "Going to Medicine Cabinet")
        elif command == "goto_dock":
            if self.is_scheduled_task: self._reset_current_delivery_status() # FIX
            self.is_scheduled_task = False # Manual Action
            self.schedule_enabled = False # EXISTING LOGIC REQUEST: Disable schedule
            self.task_queue = [] # FIX: Clear pending scheduled tasks so we don't resume them
            self._navigate_to(7, "Returning to Dock")
            self._publish_schedule_status()
        # Old command support (optional, can be removed if not used by admin)
        elif command.startswith("goto_medicine_elder_"):
            try:
                elder_num = int(command.split("_")[-1])
                self.is_scheduled_task = False
                self._navigate_to(6, "Going to Medicine Cabinet")
                self.task_queue = [2 + elder_num] # Removed explicit dock logic
            except:
                print(f"Invalid command: {command}")
        elif command == "manual_forward":
            self._manual_move("forward")
        elif command == "manual_back":
            self._manual_move("back")
        elif command == "manual_left":
            self._manual_move("left")
        elif command == "manual_right":
            self._manual_move("right")
        elif command == "manual_stop":
            self._manual_stop()
        elif command.startswith("set_speed_"):
            try:
                speed = int(command.split("_")[-1])
                speed = max(5, min(25, speed))
                self.max_velocity = speed
                self.turn_velocity = speed / 5.0
                print(f"Speed set to {speed}")
            except:
                print(f"Invalid speed command: {command}")
                
        # Reset emergency flag on any *new* movement command
        if command and command not in ["stop", "admin_stop", "manual_stop", "manual_mode", "disable_manual_mode"]:
            if self.emergency_stop_active:
                self.emergency_stop_active = False
                print("Emergency Stop RELEASED by new command")

    def _switch_to_manual(self):
        """Switch to manual driving mode"""
        self.mode = "manual"
        self.current_target_value = None
        self.current_path = []
        self.path_index = 0
        self.left_motor.setVelocity(0.0)
        self.right_motor.setVelocity(0.0)
        self.current_task = "Manual Drive Mode"
        print("Switched to MANUAL mode via web interface")

    def _disable_manual_mode(self):
        """Disable manual mode and return to idle"""
        self.mode = "manual"
        self.left_motor.setVelocity(0.0)
        self.right_motor.setVelocity(0.0)
        self.current_task = "Idle"
        print("Manual mode disabled - Robot Idle")

    def _manual_stop(self):
        """Stop movement in manual mode (called when WASD keys released)"""
        self.left_motor.setVelocity(0.0)
        self.right_motor.setVelocity(0.0)
        if self.mode == "manual":
            self.current_task = "Manual Drive Mode"

    def _emergency_stop(self):
        """Emergency stop - halt all movement and BLOCK auto-docking"""
        self.mode = "manual"
        self.current_target_value = None
        self.current_path = []
        self.path_index = 0
        self.task_queue = []
        self.left_motor.setVelocity(0.0)
        self.right_motor.setVelocity(0.0)
        
        self.emergency_stop_active = True
        self.current_task = "ADMIN STOP - Auto-Dock Disabled"
        print("ADMIN STOP activated: Auto-docking BLOCKED until new command")

    def _navigate_to(self, target: int, task_name: str):
        """Start navigation to a target"""
        x, y, _ = self.get_pose()
        start = self.world_to_grid(x, y)
        
        if self.go_to(start, target):
            self.current_task = task_name
        else:
            self.current_task = f"Failed: {task_name}"

    def _manual_move(self, direction: str):
        """Execute manual movement command"""
        self.mode = "manual"
        self.current_task = f"Manual: {direction}"
        
        if direction == "forward":
            self.left_motor.setVelocity(self.max_velocity)
            self.right_motor.setVelocity(self.max_velocity)
        elif direction == "back":
            self.left_motor.setVelocity(-self.max_velocity)
            self.right_motor.setVelocity(-self.max_velocity)
        elif direction == "left":
            self.left_motor.setVelocity(-self.turn_velocity)
            self.right_motor.setVelocity(self.turn_velocity)
        elif direction == "right":
            self.left_motor.setVelocity(self.turn_velocity)
            self.right_motor.setVelocity(-self.turn_velocity)

    # ==================== BATTERY MANAGEMENT ====================
    def _update_battery(self):
        """Update battery level (integer only)"""
        # Check if robot is at the dock (target value 7)
        pos = self.trans_field.getSFVec3f()
        mi, mj = self.world_to_grid(pos[0], pos[1])
        at_dock = False
        
        # Check if current position or nearby cells are dock (value 7)
        if 0 <= mi < self.grid_size and 0 <= mj < self.grid_size:
            # Check current cell and nearby cells (dock area)
            for di in range(-3, 4):
                for dj in range(-3, 4):
                    ni, nj = mi + di, mj + dj
                    if 0 <= ni < self.grid_size and 0 <= nj < self.grid_size:
                        if self.static_map[ni, nj] == 7:
                            at_dock = True
                            break
                if at_dock:
                    break
        
        # Robot is idle at dock: charge the battery
        is_idle = (self.current_path == [] or self.path_index >= len(self.current_path))
        is_not_moving = abs(self.left_motor.getVelocity()) < 0.1 and abs(self.right_motor.getVelocity()) < 0.1
        
        if at_dock and is_idle and is_not_moving:
            self.battery_drain_counter += 1
            # Update task status to show charging
            self.current_task = f"Charging ({self.battery}%)"
            # Charge 1% every ~10 steps (faster than drain)
            if self.battery_drain_counter >= 10:
                self.battery_drain_counter = 0
                if self.battery < 100:
                    self.battery += 1
                    if self.battery % 10 == 0:
                        print(f"CHARGING: Battery at {self.battery}%")
        # Drain battery when moving
        elif self.mode == "auto" or (abs(self.left_motor.getVelocity()) > 0.1 or abs(self.right_motor.getVelocity()) > 0.1):
            self.battery_drain_counter += 1
            # Drain 1% every ~300 steps (6x slower than before)
            if self.battery_drain_counter >= 300:
                self.battery_drain_counter = 0
                if self.battery > 0:
                    self.battery -= 1
        
        # Update temperature with small fluctuation
        self.temperature = 42 + random.randint(-3, 3)
        
        # Auto-dock at 20% battery
        if self.battery <= 20 and not self.auto_dock_triggered:
            if self.mode == "manual" or self.current_path == []:
                # If idle, immediately go to dock
                print("LOW BATTERY: Auto-docking initiated")
                self._navigate_to(7, "Auto-docking (Low Battery)")
                self.auto_dock_triggered = True
            else:
                # If navigating, mark for dock after current task
                print("LOW BATTERY: Will dock after current task")
        
        # Reset auto-dock flag when battery charged above threshold
        if self.battery > 20:
            self.auto_dock_triggered = False

    # ==================== MEDICATION SCHEDULE METHODS ====================
    def _reset_current_delivery_status(self):
        """Resets the delivery status for the current time slot so schedule can retry."""
        time_slot = None
        if 7 <= self.sim_hour < 12:
            time_slot = "morning"
        elif 12 <= self.sim_hour < 18:
            time_slot = "afternoon"
        elif 18 <= self.sim_hour < 22:
            time_slot = "evening"
            
        if time_slot:
            self.delivery_status[time_slot] = False
            print(f"Reset delivery status for {time_slot} due to interruption.")
            
    def _load_medication_schedule(self):
        """Loads the medication schedule from a JSON file."""
        if os.path.exists(self.schedule_file_path):
            try:
                with open(self.schedule_file_path, 'r') as f:
                    self.medication_schedule = json.load(f)
                print(f"Medication schedule loaded from {self.schedule_file_path}")
            except Exception as e:
                print(f"Error loading medication schedule: {e}")
                self.medication_schedule = {}
        else:
            print(f"Medication schedule file not found: {self.schedule_file_path}")
            self.medication_schedule = {}

    def _check_medication_schedule(self):
        """Checks the current simulation time against the medication schedule."""
        # Always reload schedule to pick up live changes
        self._load_medication_schedule()
        
        current_time_str = f"{self.sim_hour:02d}:{self.sim_minute:02d}"
        
        # Determine current time slot and exact trigger hour
        time_slot = None
        
        # Morning: 07:00 - 12:00 (Trigger at 07:00)
        # Note: We check if it is within the window, and if not delivered yet.
        # This allows us to "catch up" if we start simulation at 8am, OR if we interrupt another task.
        if 7 <= self.sim_hour < 12:
            time_slot = "morning"
        elif 12 <= self.sim_hour < 18:
            time_slot = "afternoon"
        elif 18 <= self.sim_hour < 22:
            time_slot = "evening"

        # If we are in a valid slot and HAVEN'T delivered yet for this slot
        if time_slot and not self.delivery_status[time_slot]:
            
            # Condition 1: Schedule must be ENABLED
            if not self.schedule_enabled:
                return

            # Condition 2: Priority Check (Low Priority)
            # Only start if Robot is Idle, Charging, or Auto-docking.
            # DO NOT start if in Manual Mode, Emergency Stop, or performing another Task.
            # EXCEPTION: If is_scheduled_task is TRUE, allow self-interrupt (Evening replaces Afternoon).
            
            # Check if current task is interruptible (Idle, Charging, Auto-docking)
            allowed_prefixes = ["idle", "charging", "auto-docking"]
            current_task_lower = self.current_task.lower()
            is_allowed_state = False # FIX: Initialize before loop
            
            for prefix in allowed_prefixes:
                if current_task_lower.startswith(prefix) or current_task_lower == prefix:
                    is_allowed_state = True
                    break
            
            if is_allowed_state:
                print(f"[SCHEDULE] Interrupting task '{self.current_task}' for medication delivery.")

            
            # Also allow if strictly idle
            if self.current_path == [] and self.task_queue == [] and not self.emergency_stop_active:
                is_allowed_state = True

            # Allow Self-Interrupt (New Schedule overwrites Old Schedule)
            if self.is_scheduled_task:
                 is_allowed_state = True

            if not is_allowed_state:
                # Robot is busy with something else (e.g. Manual Mode). Skip check for now.
                # We will check again in the next loop.
                return
            
            if not self.emergency_stop_active:
                
                # Find which elders need meds in this slot
                elders_to_visit = []
                for elder_id, meds in self.medication_schedule.items():
                    # Check if elder 1/2/3 has meds for this slot
                    # meds[time_slot] is list of meds
                    if time_slot in meds and len(meds[time_slot]) > 0:
                        # Map elder IDs to targets: Elder 1 -> 3, Elder 2 -> 4, Elder 3 -> 5
                        try:
                            elder_num = int(str(elder_id).split('_')[1]) # Extract number from "elder_1"
                            target_id = 2 + elder_num
                            elders_to_visit.append(target_id)
                        except ValueError:
                            print(f"Warning: Could not parse elder ID from '{elder_id}'")
                            pass
                
                if elders_to_visit:
                    print(f"[{time_slot.upper()}] Starting Medication Delivery Route: {elders_to_visit}")
                    
                    self.is_scheduled_task = True # MARK AS SCHEDULED TASK
                    
                    # 1. Reset Path/Queue immediately (Interrupt logic)
                    self.current_path = []
                    self.path_index = 0
                    
                    # 2. Go to Medicine Cabinet First (Target 6)
                    self._navigate_to(6, f"Scheduled: Collecting Meds (Cabinet)")
                    
                    # 3. Queue up the elders
                    elders_to_visit.sort() # Ensure consistent delivery order
                    self.task_queue = list(elders_to_visit) # Copy list to avoid reference issues
                    
                    # 4. Mark this slot as handled
                    self.delivery_status[time_slot] = True
                    
                    
                    # 5. Stop motors immediately (reset state)
                    self.left_motor.setVelocity(0)
                    self.right_motor.setVelocity(0)
                    
                    elder_names = [f"Elder {vid-2}" for vid in elders_to_visit]
                    print(f"Started delivery route for {time_slot}: Cabinet -> {', '.join(elder_names)}")
                else:
                    # No meds needed for anyone this slot, mark as done so we don't check again
                    self.delivery_status[time_slot] = True
                    # print(f"No medications scheduled for {time_slot}. Skipping.")
            
    # ==================== EXISTING NAVIGATION METHODS ====================
    def get_yaw(self) -> float:
        """Robot yaw in ENU (X-East, Y-North, Z-Up)."""
        o = self.robot_node.getOrientation()
        return math.atan2(o[3], o[0])

    def get_pose(self) -> Tuple[float, float, float]:
        pos = self.trans_field.getSFVec3f()
        return pos[0], pos[1], self.get_yaw()

    def world_to_grid(self, x, y):
        """Converts Webots world coordinates to map indices."""
        mi = int(math.floor((x / self.cell_size) + (self.grid_size / 2)))
        mj = int(math.floor((y / self.cell_size) + (self.grid_size / 2)))
        return mi, mj

    def grid_to_world(self, mi: int, mj: int) -> Tuple[float, float]:
        """Converts map indices to the center of a cell in Webots world coordinates."""
        x = ((mi + 0.5) - (self.grid_size / 2)) * self.cell_size
        y = ((mj + 0.5) - (self.grid_size / 2)) * self.cell_size
        return x, y

    def _find_nearest_goal(self, start: Tuple[int, int], target_value: int) -> Optional[Tuple[int, int]]:
        goals = np.argwhere(self.static_map == target_value)
        if goals.size == 0:
            return None

        si, sj = start
        dists = np.abs(goals[:, 0] - si) + np.abs(goals[:, 1] - sj)
        best = goals[int(np.argmin(dists))]
        return int(best[0]), int(best[1])

    @staticmethod
    def _normalize_angle(a: float) -> float:
        while a > math.pi:
            a -= 2 * math.pi
        while a < -math.pi:
            a += 2 * math.pi
        return a

    def _reconstruct_path(
        self,
        came_from: Dict[Tuple[int, int, int], Tuple[int, int, int]],
        end_state: Tuple[int, int, int],
    ) -> List[Tuple[int, int]]:
        path: List[Tuple[int, int]] = []
        s = end_state
        while s in came_from:
            path.append((s[0], s[1]))
            s = came_from[s]
        path.append((s[0], s[1]))
        path.reverse()

        if len(path) < 3:
            return path

        smoothed = [path[0]]
        prev_dx = path[1][0] - path[0][0]
        prev_dy = path[1][1] - path[0][1]
        for k in range(1, len(path) - 1):
            dx = path[k + 1][0] - path[k][0]
            dy = path[k + 1][1] - path[k][1]
            if dx == prev_dx and dy == prev_dy:
                continue
            smoothed.append(path[k])
            prev_dx, prev_dy = dx, dy
        smoothed.append(path[-1])
        return smoothed

    def _a_star_with_turn_penalty(
        self,
        start: Tuple[int, int],
        goal: Tuple[int, int],
    ) -> List[Tuple[int, int]]:
        si, sj = start
        gi, gj = goal

        if not (0 <= si < self.grid_size and 0 <= sj < self.grid_size):
            return []
        if not (0 <= gi < self.grid_size and 0 <= gj < self.grid_size):
            return []
        if not self.passable[gi, gj]:
             return []

        # Relax Start Node Check: If start is blocked (drift), find nearest valid neighbor
        if not self.passable[si, sj]:
            print(f"Warning: Start ({si},{sj}) is blocked. Searching for valid neighbor...")
            found_start = False
            # Search radius 3
            for r in range(1, 4):
                for di_s in range(-r, r+1):
                    for dj_s in range(-r, r+1):
                        nsi, nsj = si + di_s, sj + dj_s
                        if 0 <= nsi < self.grid_size and 0 <= nsj < self.grid_size and self.passable[nsi, nsj]:
                            si, sj = nsi, nsj
                            found_start = True
                            print(f"Recovered start at ({si}, {sj})")
                            break
                    if found_start: break
                if found_start: break
            
            if not found_start:
                print("Could not find valid start node nearby.")
                return []

        di = (1, 0, -1, 0)
        dj = (0, 1, 0, -1)

        def h(i: int, j: int) -> float:
            return abs(i - gi) + abs(j - gj)

        open_heap: List[Tuple[float, float, int, int, int]] = []
        came_from: Dict[Tuple[int, int, int], Tuple[int, int, int]] = {}
        gscore: Dict[Tuple[int, int, int], float] = {}

        for d in range(4):
            state = (si, sj, d)
            gscore[state] = 0.0
            heapq.heappush(open_heap, (h(si, sj), 0.0, si, sj, d))

        best_goal_state: Optional[Tuple[int, int, int]] = None
        best_goal_cost = float("inf")

        while open_heap:
            f, g, i, j, d = heapq.heappop(open_heap)

            if g != gscore.get((i, j, d), float("inf")):
                continue

            if (i, j) == (gi, gj):
                if g < best_goal_cost:
                    best_goal_cost = g
                    best_goal_state = (i, j, d)
                if g <= best_goal_cost:
                    pass

            for nd in range(4):
                ni = i + di[nd]
                nj = j + dj[nd]
                if ni < 0 or nj < 0 or ni >= self.grid_size or nj >= self.grid_size:
                    continue
                if not self.passable[ni, nj]:
                    continue

                step_cost = 1.0
                turn_cost = 0.0 if nd == d else self.turn_penalty
                ng = g + step_cost + turn_cost
                nstate = (ni, nj, nd)

                if ng < gscore.get(nstate, float("inf")):
                    gscore[nstate] = ng
                    came_from[nstate] = (i, j, d)
                    heapq.heappush(open_heap, (ng + h(ni, nj), ng, ni, nj, nd))

        if best_goal_state is None:
            return []
        return self._reconstruct_path(came_from, best_goal_state)

    def go_to(self, start: Tuple[int, int], end: int) -> bool:
        """Plan and start following a path. end must be one of 3/4/5/6/7."""
        if end not in (3, 4, 5, 6, 7):
            return False

        goal = self._find_nearest_goal(start, end)
        if goal is None:
            print(f"No goal cells found for value {end}.")
            return False

        path = self._a_star_with_turn_penalty(start, goal)
        if not path:
            print(f"No path found from {start} to {goal}.")
            return False

        self.current_target_value = end
        self.current_path = path
        self.path_index = 0
        self.mode = "auto"
        print(f"AUTO: target={end}, path_len={len(path)}")
        return True

    def _follow_path_step(self):
        if not self.current_path or self.path_index >= len(self.current_path):
            self.left_motor.setVelocity(0.0)
            self.right_motor.setVelocity(0.0)
            self.current_path = [] # CRITICAL FIX: Clear path so is_idle becomes True
            
            # Check for queued tasks
            if self.task_queue:
                next_target = self.task_queue.pop(0)
                target_names = {3: "Elder 1", 4: "Elder 2", 5: "Elder 3", 6: "Medicine", 7: "Dock"}
                # Use "Going to Elder X" as requested
                status_msg = f"Going to {target_names.get(next_target, 'Unknown')}"
                if next_target == 6:
                    status_msg = "Scheduled: Collecting Meds (Cabinet)"
                self._navigate_to(next_target, status_msg)
            else:
                self.current_task = "Idle"
                self.mode = "manual" # IMPORTANT: Reset to manual to allow auto-dock triggers
                self.is_scheduled_task = False # Reset schedule flag when queue finished
                
                # Check if we need to auto-dock after task completion
                if self.battery <= 15 and not self.auto_dock_triggered:
                    self._navigate_to(7, "Auto-docking (Low Battery)")
                    self.auto_dock_triggered = True
            return

        rx, ry, yaw = self.get_pose()
        wi, wj = self.current_path[self.path_index]
        tx, ty = self.grid_to_world(wi, wj)

        dx = tx - rx
        dy = ty - ry
        dist = math.hypot(dx, dy)

        if dist < 0.06:
            self.path_index += 1
            if self.path_index >= len(self.current_path):
                print("AUTO: arrived")
                self.left_motor.setVelocity(0.0)
                self.right_motor.setVelocity(0.0)
            return

        desired = math.atan2(dy, dx)
        err = self._normalize_angle(desired - yaw)

        if abs(err) > 0.35:
            v = 0.0
            w = self.turn_velocity if err > 0 else -self.turn_velocity
        else:
            v = min(self.max_velocity * 0.7, self.max_velocity * (dist / 0.4))
            w = max(-self.turn_velocity, min(self.turn_velocity, err * (self.turn_velocity / 0.35)))

        v_l = v - w
        v_r = v + w
        v_l = max(-self.max_velocity, min(self.max_velocity, v_l))
        v_r = max(-self.max_velocity, min(self.max_velocity, v_r))
        self.left_motor.setVelocity(v_l)
        self.right_motor.setVelocity(v_r)

    def _read_keyboard_and_act(self) -> Tuple[float, float]:
        """Reads keyboard events ONCE per step."""
        v_l, v_r = 0.0, 0.0
        pressed_target: Optional[int] = None
        pressed_manual = False

        key = self.keyboard.getKey()
        while key != -1:
            if key in (ord('m'), ord('M')):
                pressed_manual = True
            elif key in (ord('3'), ord('4'), ord('5'), ord('6'), ord('7')):
                pressed_target = int(chr(key))
            elif key == ord('W'):
                v_l, v_r = self.max_velocity, self.max_velocity
            elif key == ord('S'):
                v_l, v_r = -self.max_velocity, -self.max_velocity
            elif key == ord('A'):
                v_l, v_r = -self.turn_velocity, self.turn_velocity
            elif key == ord('D'):
                v_l, v_r = self.turn_velocity, -self.turn_velocity
            key = self.keyboard.getKey()

        if pressed_manual:
            self.mode = "manual"
            self.current_target_value = None
            self.current_path = []
            self.path_index = 0
            self.current_task = "Manual Mode"
            print("Robot Stopped and Switched to MANUAL Mode.")

        if pressed_target is not None:
            x, y, _ = self.get_pose()
            start = self.world_to_grid(x, y)
            target_names = {3: "Elder 1", 4: "Elder 2", 5: "Elder 3", 6: "Medicine", 7: "Dock"}
            self.current_task = f"Navigating to {target_names.get(pressed_target, 'Unknown')}"
            self.go_to(start, pressed_target)

        return v_l, v_r

    def run(self):
        # Matplotlib visualization commented out - now shown in web app
        # plt.ion()
        # fig, ax = plt.subplots(figsize=(8, 8))
        # 
        # colors = ['black', 'white', 'gray', 'orange', 'cyan', 'magenta', 'yellow', 'red', 'green', 'blue']
        # cmap = mcolors.ListedColormap(colors)
        # bounds = np.arange(-1.5, 9.5, 1)
        # norm = mcolors.BoundaryNorm(bounds, cmap.N)
        # 
        # img = ax.imshow(self.static_map.T, cmap=cmap, norm=norm, origin='lower')
        # plt.title("ECHO Robot | WASD=Manual | 3-7=Navigate | M=Stop")

        print("ECHO Robot Controller started")
        print("MQTT enabled" if self.mqtt_client else "MQTT disabled")

        while self.robot.step(self.timestep) != -1:
            # Process MQTT commands
            self._process_mqtt_command()
            
            # Read keyboard input
            v_l, v_r = self._read_keyboard_and_act()
            
            if self.mode == "manual":
                # Only set velocities from keyboard if keys were actually pressed
                # MQTT commands set velocities directly via _manual_move()
                if v_l != 0.0 or v_r != 0.0:
                    self.left_motor.setVelocity(v_l)
                    self.right_motor.setVelocity(v_r)
            else:
                self._follow_path_step()
            
            # Check if robot is idle (not moving and no task)
            is_not_moving = abs(self.left_motor.getVelocity()) < 0.1 and abs(self.right_motor.getVelocity()) < 0.1
            is_idle = self.current_path == [] and self.task_queue == []
            
            # Check if current task indicates charging or auto-docking (exclude from idle trigger)
            is_charging = self.current_task.startswith("Charging")
            is_auto_docking = self.current_task.startswith("Auto-docking")
            
            # Check if current task contains "Idle" or is exactly "Idle"
            has_idle_in_task = "Idle" in self.current_task or self.current_task == "Idle"
            
            # Only auto-dock if we are NOT in emergency stop
            if is_not_moving and is_idle and not is_charging and not is_auto_docking and not self.emergency_stop_active:
                # Update task to show idle status based on mode
                if self.mode == "manual" and self.current_task == "Manual Drive Mode":
                    self.current_task = "Manual Mode (Idle)"
                
                # Check if already at dock
                pos = self.trans_field.getSFVec3f()
                mi, mj = self.world_to_grid(pos[0], pos[1])
                at_dock = False
                for di in range(-3, 4):
                    for dj in range(-3, 4):
                        ni, nj = mi + di, mj + dj
                        if 0 <= ni < self.grid_size and 0 <= nj < self.grid_size:
                            if self.static_map[ni, nj] == 7:
                                at_dock = True
                                break
                    if at_dock:
                        break
                
                if not at_dock:
                    self.idle_counter += 1
                    # Auto-dock after 100 steps of being idle
                    if self.idle_counter >= 100:
                        print(f"IDLE for 100 steps (task: {self.current_task}): Auto-docking initiated")
                        self._navigate_to(7, "Auto-docking (Idle)")
                        self.idle_counter = 0
                else:
                    self.idle_counter = 0  # Reset if at dock
            else:
                self.idle_counter = 0  # Reset if not idle
                # Reset manual mode task when moving
                if self.mode == "manual" and self.current_task == "Manual Mode (Idle)":
                    self.current_task = "Manual Drive Mode"
            
            # Update battery
            self._update_battery()
            
            # Get position for visualization
            pos = self.trans_field.getSFVec3f()
            mi, mj = self.world_to_grid(pos[0], pos[1])
                
            # ==================== TIME SIMULATION ====================
            current_real_time = time.time()
            if current_real_time - self.last_time_update >= 1.0:  # Every 1 real second
                self.last_time_update = current_real_time
                self.sim_minute += 10 # 10 minute per second
                if self.sim_minute >= 60:
                    self.sim_minute = 0
                    self.sim_hour += 1
                if self.sim_hour >= 24:
                    self.sim_hour = 0
                    # Reset daily delivery status at midnight
                    self.delivery_status = {"morning": False, "afternoon": False, "evening": False}
                    print("NEW DAY: Delivery status reset")
                
                # Print time occasionally
                if self.sim_minute == 0:
                    print(f"🕒 SIMULATION TIME: {self.sim_hour:02d}:00")
                
                # Check Schedule
                self._check_medication_schedule()

            # Publish status every 20 steps
            if self.step_counter % 10 == 0:
                self._publish_status()
                
            # Publish map every 5 steps (faster update rate)
            if self.step_counter % 50 == 0:
                self._publish_map()
                
            # Publish camera every 10 steps (higher frame rate)
            if self.step_counter % 1 == 0:
                self._publish_camera()
            
            self.step_counter += 1

    def cleanup(self):
        """Clean up resources"""
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
            print("MQTT client disconnected")


if __name__ == "__main__":
    bot = BurgerNavigator()
    try:
        bot.run()
    finally:
        bot.cleanup()