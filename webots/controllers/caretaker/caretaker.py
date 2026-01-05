"""
ECHO Robot Caretaker Controller
Extended with MQTT integration for web application communication
"""

from controller import Supervisor
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
            
            self.mqtt_client.connect("localhost", 1883, 60)
            self.mqtt_client.loop_start()
            print("MQTT client started, connecting to localhost:1883")
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
            }
        }
        
        try:
            self.mqtt_client.publish("robot/status", json.dumps(status))
        except Exception as e:
            print(f"Error publishing status: {e}")

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
        
        # Command mapping
        if command == "stop":
            self._emergency_stop()
        elif command == "manual_mode":
            self._switch_to_manual()
        elif command == "disable_manual_mode":
            self._disable_manual_mode()
        elif command == "goto_elder_1":
            self._navigate_to(3, "Navigating to Elder 1")
        elif command == "goto_elder_2":
            self._navigate_to(4, "Navigating to Elder 2")
        elif command == "goto_elder_3":
            self._navigate_to(5, "Navigating to Elder 3")
        elif command == "goto_medicine":
            self._navigate_to(6, "Going to Medicine Cabinet")
        elif command == "goto_dock":
            self._navigate_to(7, "Returning to Dock")
        elif command.startswith("goto_medicine_elder_"):
            # Multi-step: medicine first, then elder
            try:
                elder_num = int(command.split("_")[-1])
                elder_target = 2 + elder_num  # 3, 4, or 5
                self.task_queue = [6, elder_target]  # Medicine (6), then Elder
                self._navigate_to(6, f"Getting medicine for Elder {elder_num}")
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
        """Emergency stop - halt all movement"""
        self.mode = "manual"
        self.current_target_value = None
        self.current_path = []
        self.path_index = 0
        self.task_queue = []
        self.left_motor.setVelocity(0.0)
        self.right_motor.setVelocity(0.0)
        self.current_task = "Emergency Stop - Idle"
        print("EMERGENCY STOP activated")

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
            # Drain 1% every ~50 steps (faster drain)
            if self.battery_drain_counter >= 50:
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
        if not self.passable[si, sj] or not self.passable[gi, gj]:
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
            
            # Check for queued tasks
            if self.task_queue:
                next_target = self.task_queue.pop(0)
                target_names = {3: "Elder 1", 4: "Elder 2", 5: "Elder 3", 6: "Medicine", 7: "Dock"}
                self._navigate_to(next_target, f"Delivering to {target_names.get(next_target, 'Unknown')}")
            else:
                self.current_task = "Idle"
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
            
            if is_not_moving and is_idle and not is_charging and not is_auto_docking:
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

            # Matplotlib visualization update commented out
            # if self.step_counter % 10 == 0:
            #     display_map = self.static_map.copy()
            #     
            #     if 0 <= mi < self.grid_size and 0 <= mj < self.grid_size:
            #         r = 5
            #         i_min, i_max = max(0, mi - r), min(self.grid_size, mi + r + 1)
            #         j_min, j_max = max(0, mj - r), min(self.grid_size, mj + r + 1)
            #         display_map[i_min:i_max, j_min:j_max] = 8
            #     
            #     img.set_data(display_map.T)
            #     plt.pause(0.001)
                
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