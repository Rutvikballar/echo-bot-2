# Declaration: LLM's were used for code debugging and testing

import sys
import os

# ==================== WEBOTS RUNTIME CONFIGURATION ====================
# This allows running the script directly without a launcher

# Webots Setup
if "WEBOTS_HOME" not in os.environ:
    if os.path.exists(r"C:\Program Files\Webots"):
        os.environ["WEBOTS_HOME"] = r"C:\Program Files\Webots"

if "WEBOTS_HOME" in os.environ:
    lib_path = os.path.join(os.environ["WEBOTS_HOME"], "lib", "controller", "python")
    if os.path.exists(lib_path) and lib_path not in sys.path:
        sys.path.append(lib_path)

try:
    from controller import Supervisor
except ImportError:
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

# MQTT
try:
    import paho.mqtt.client as mqtt
    MQTT_AVAILABLE = True
except ImportError:
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

        # Motors
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

        # Camera setup
        self.camera = None
        try:
            self.camera = self.robot.getDevice("camera")
            if self.camera:
                self.camera.enable(self.timestep)
                print("Camera enabled")
        except Exception as e:
            print(f"Camera not available: {e}")

        # Load Static Map
        if os.path.exists(self.map_file):
            data = np.load(self.map_file)
            self.static_map = data['grid']
            self.grid_size = self.static_map.shape[0]
            self.cell_size = 0.04 
            print(f"Loaded map: {self.map_file}")
        else:
            print(f"Error: {self.map_file} not found!")
            exit()

        # Traversability: block walls/padding/unknown
        self.passable = (self.static_map != 1) & (self.static_map != 2) & (self.static_map != -1)

        # Control State
        self.mode = "manual"
        self.current_target_value: Optional[int] = None
        self.current_path: List[Tuple[int, int]] = []
        self.path_index = 0
        self.current_task = "Idle"
        self.turn_penalty = 6.0
        self.step_counter = 0
        
        # Battery & Temperature
        self.battery = 100
        self.temperature = 42
        self.battery_drain_counter = 0
        self.auto_dock_triggered = False
        self.idle_counter = 0
        
        # MQTT
        self.mqtt_client = None
        self.mqtt_command = None
        self.mqtt_lock = threading.Lock()
        
        if MQTT_AVAILABLE:
            self._setup_mqtt()
        
        # Multi-step task queue (for medicine + elder delivery)
        self.task_queue: List[int] = []
        self.pending_med_name = None  

        # Emergency Override Flag (Admin Stop)
        self.emergency_stop_active = False

        # Schedule
        self.sim_hour = 6
        self.sim_minute = 0
        self.last_time_update = time.time()
        self.medication_schedule = {}
        self.schedule_file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../webapp_caretaker/medication_schedule.json"))
        self._load_medication_schedule()
        
        self.delivery_status = {"morning": False, "afternoon": False, "evening": False}
        self.schedule_enabled = True
        self.is_scheduled_task = False

    def _resolve_map_file(self, map_file: str) -> str:
        """Find map file either absolute, relative, or in sibling directories."""
        if os.path.isabs(map_file) and os.path.exists(map_file):
            return map_file

        here = os.path.dirname(__file__)
        candidate = os.path.join(here, map_file)
        if os.path.exists(candidate):
            return candidate

        candidate = os.path.normpath(os.path.join(here, "..", "mapper", map_file))
        if os.path.exists(candidate):
            return candidate

        return map_file

    # MQTT Methods
    def _setup_mqtt(self):
        """Initialize MQTT client."""
        try:
            self.mqtt_client = mqtt.Client()
            self.mqtt_client.on_connect = self._on_mqtt_connect
            self.mqtt_client.on_message = self._on_mqtt_message
            self.mqtt_client.on_disconnect = self._on_mqtt_disconnect
            
            self.mqtt_client.connect("127.0.0.1", 1883, 60)
            self.mqtt_client.loop_start()
            print("MQTT connected to 127.0.0.1:1883")
        except Exception as e:
            print(f"MQTT failed: {e}")
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
            # Nurse Stop: Allows auto-dock if idle
            if self.is_scheduled_task: self._reset_current_delivery_status()
            self.schedule_enabled = False
            self.mode = "manual"
            self.current_path = []
            self.path_index = 0
            self.task_queue = []
            self.left_motor.setVelocity(0.0)
            self.right_motor.setVelocity(0.0)
            self.current_task = "Stopped (Idle)"
            print("Nurse STOP: Schedule Disabled, Queue Cleared")
            self._publish_schedule_status()
        elif command == "admin_stop":
            # Admin Stop (Emergency stop, PREVENTS auto-dock)
            if self.is_scheduled_task: self._reset_current_delivery_status()
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
            
            # Abort current schedule task if active
            if self.mode == "auto" and self.is_scheduled_task:
                 print("Aborting SCHEDULE task...")
                 self._reset_current_delivery_status()
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
                    self.pending_med_name = med_name

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
            if self.is_scheduled_task: self._reset_current_delivery_status()
            self.is_scheduled_task = False
            self.schedule_enabled = False
            self.task_queue = []
            self._navigate_to(7, "Returning to Dock")
            self._publish_schedule_status()
        # Old command support 
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
        """Return to idle mode."""
        self.mode = "manual"
        self.left_motor.setVelocity(0.0)
        self.right_motor.setVelocity(0.0)
        self.current_task = "Idle"
        print("Manual mode disabled")

    def _manual_stop(self):
        """Stop movement in manual mode."""
        self.left_motor.setVelocity(0.0)
        self.right_motor.setVelocity(0.0)
        if self.mode == "manual":
            self.current_task = "Manual Drive Mode"

    def _emergency_stop(self):
        """Halt all movement and block auto-docking."""
        self.mode = "manual"
        self.current_target_value = None
        self.current_path = []
        self.path_index = 0
        self.task_queue = []
        self.left_motor.setVelocity(0.0)
        self.right_motor.setVelocity(0.0)
        
        self.emergency_stop_active = True
        self.current_task = "ADMIN STOP - Auto-Dock Disabled"
        print("ADMIN STOP: Auto-docking BLOCKED")

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

    # Battery Management
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
                print("LOW BATTERY: Auto-docking")
                self._navigate_to(7, "Auto-docking (Low Battery)")
                self.auto_dock_triggered = True
            else:
                # If navigating, mark for dock after current task
                print("LOW BATTERY: Will dock after current task")
        
        # Reset auto-dock flag when battery charged above threshold
        if self.battery > 20:
            self.auto_dock_triggered = False

    # Medication Schedule
    def _reset_current_delivery_status(self):
        """Allows schedule to retry the current slot."""
        time_slot = None
        if 7 <= self.sim_hour < 12:
            time_slot = "morning"
        elif 12 <= self.sim_hour < 18:
            time_slot = "afternoon"
        elif 18 <= self.sim_hour < 22:
            time_slot = "evening"
            
        if time_slot:
            self.delivery_status[time_slot] = False
            print(f"Reset delivery status for {time_slot}")
            
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
        
        # Determine current time slot
        time_slot = None
        
        if 7 <= self.sim_hour < 12:
            time_slot = "morning"
        elif 12 <= self.sim_hour < 18:
            time_slot = "afternoon"
        elif 18 <= self.sim_hour < 22:
            time_slot = "evening"

        # Queue delivery if valid slot and not yet delivered
        if time_slot and not self.delivery_status[time_slot]:
            
            # Condition 1: Schedule must be ENABLED
            if not self.schedule_enabled:
                return

            # Priority Check: Only start if Idle/Charging/Auto-docking
            allowed_prefixes = ["idle", "charging", "auto-docking"]
            current_task_lower = self.current_task.lower()
            is_allowed_state = False
            
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
                # will check again in the next loop.
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
            
    # Navigation
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

    def _generate_simulated_vitals(self, elder_id: int):
        """
        Generates simulated physical parameters for a given elder.
        Simulates a 50/50 chance of an 'emergency' based on temperature.
        
        Parameters:
            elder_id (int): The ID of the elder (1, 2, or 3).
        
        Returns:
            dict: Simulated vital signs.
        """
        # Normal ranges (approximate) - Temp: 36.1-37.2C, BP: 90/60-120/80, HR: 60-100, Oxy: 95-100%

        # Temperature: 50% chance normal, 50% chance fever (emergency)
        if random.choice([True, False]):
            temp = round(random.uniform(36.1, 37.2), 1)  # Normal
            is_emergency = False
            emergency_reason = "None"
        else:
            temp = round(random.uniform(37.5, 39.5), 1)  # Fever
            is_emergency = True
            emergency_reason = "Temperature High"
        
        # Other parameters (always normal)
        sys_bp = random.randint(90, 120)
        dia_bp = random.randint(60, 80)
        hr = random.randint(60, 100)
        oxy = random.randint(95, 100)

        vitals = {
            "elder_id": elder_id,
            "temperature_c": temp,
            "blood_pressure": f"{sys_bp}/{dia_bp}",
            "heart_rate_bpm": hr,
            "oxygen_saturation_pct": oxy,
            "is_emergency": is_emergency,
            "emergency_reason": emergency_reason,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        print(f"DEBUG_VITALS: Generated for Elder {elder_id}: {vitals}")
        return vitals

    def _handle_arrival_logging(self, elder_id):
        """Log arrival events to MQTT."""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"DEBUG_ARRIVAL: Task={self.current_task}")
        
        # --- CASE 1: MONITORING ---
        if "Monitoring" in self.current_task:
            vitals = self._generate_simulated_vitals(elder_id)
            print(f"LOG_VERIFY: [MONITORING] Elder {elder_id} | Time: {timestamp} | Vitals: {vitals}")
            
            # 1. Send Log to Database
            if self.mqtt_client:
                log_data = json.dumps(vitals) # Vitals dict matches DB schema nicely
                self.mqtt_client.publish("robot/log/vitals", log_data)

            # 2. Auto-trigger nurse call on emergency
            if vitals['is_emergency']:
                alert_msg = json.dumps({"elder_id": elder_id, "request_type": "Pain / Hurt", "vitals": vitals})
                if self.mqtt_client:
                    self.mqtt_client.publish("robot/emergency_alert", alert_msg)
                print(f"EMERGENCY: Auto-called nurse for Elder {elder_id}")

        # --- CASE 2: SCHEDULED MEDICINE ---
        elif self.is_scheduled_task:
            period = "morning" if 6 <= self.sim_hour < 12 else "afternoon" if 12 <= self.sim_hour < 18 else "evening"
            meds = self.medication_schedule.get(f"elder_{elder_id}", {}).get(period, [])
            med_name = meds[0] if meds else "None Assigned"
            
            print(f"LOG_VERIFY: [SCHEDULED MED] Elder {elder_id} | Time: {timestamp} | Medicine: {med_name}")
            
            # Send Log to Database
            if self.mqtt_client:
                log_data = json.dumps({
                    "elder_id": elder_id,
                    "medicine_name": med_name,
                    "delivery_type": "Scheduled",
                    "timestamp": timestamp
                })
                self.mqtt_client.publish("robot/log/medication", log_data)

        # --- CASE 3: MANUAL MEDICINE ---
        elif self.pending_med_name:
            print(f"LOG_VERIFY: [MANUAL MED] Elder {elder_id} | Time: {timestamp} | Medicine: {self.pending_med_name}")
            
            # Send Log to Database
            if self.mqtt_client:
                log_data = json.dumps({
                    "elder_id": elder_id,
                    "medicine_name": self.pending_med_name,
                    "delivery_type": "Manual",
                    "timestamp": timestamp
                })
                self.mqtt_client.publish("robot/log/medication", log_data)
            
            self.pending_med_name = None

    def _follow_path_step(self):
        if not self.current_path or self.path_index >= len(self.current_path):
            self.left_motor.setVelocity(0.0)
            self.right_motor.setVelocity(0.0)
            self.current_path = []
            
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
                self.mode = "manual"
                self.is_scheduled_task = False
                
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

                # --- NEW LOGGING LOGIC START ---
                if "Elder" in self.current_task:
                    try:
                        # Extract ID (works for "Going to Elder 1", "Monitoring Elder 1", etc.)
                        elder_num = [int(s) for s in self.current_task.split() if s.isdigit()][0]
                        # CALL CENTRAL LOGGER
                        self._handle_arrival_logging(elder_num)
                    except:
                        pass
                # --- NEW LOGGING LOGIC END ---



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
                
            # Time Simulation
            current_real_time = time.time()
            if current_real_time - self.last_time_update >= 1.0:  # Every 1 real second
                self.last_time_update = current_real_time
                self.sim_minute += 1 # 1 minute per second
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