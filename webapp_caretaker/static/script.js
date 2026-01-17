/**
 * ECHO Robot Web Application - JavaScript
 * Handles SocketIO communication, canvas rendering, and UI interactions
 */

// ==================== GLOBAL STATE ====================
let socket = null;
let currentElderId = null;
let selectedElderForAction = null;

// ==================== SOCKET.IO SETUP ====================
function initSocket() {
    socket = io();

    socket.on('connect', () => {
        console.log('Connected to server');
        updateConnectionStatus(true);
    });

    socket.on('disconnect', () => {
        console.log('Disconnected from server');
        updateConnectionStatus(false);
    });

    socket.on('robot_status', (data) => {
        updateRobotStatus(data);
    });

    socket.on('camera_feed', (data) => {
        updateCameraFeed(data.image);
    });

    socket.on('map_update', (data) => {
        if (data && data.data) {
            updateMapCanvas(data.data);
        }
    });

    // Listen for schedule status updates from robot
    socket.on('schedule_status', function (data) {
        console.log("[DEBUG] Received schedule_status payload:", data); // RAW PAYLOAD
        const toggle = document.getElementById('card-schedule-toggle');

        if (toggle) {
            console.log("[DEBUG] Current toggle state:", toggle.checked);
            console.log("[DEBUG] Setting toggle to:", data.enabled);
            toggle.checked = data.enabled;
            console.log("[DEBUG] New toggle state:", toggle.checked);

            // Force redraw/status text update if needed
            // (Optional: Add visual feedback logic here if slider needs class update)
            // Force redraw/status text update if needed
            // (Optional: Add visual feedback logic here if slider needs class update)
        } else {
            // Harmless on Admin page
            console.log("[INFO] Schedule toggle not present on this page (normal for Admin view).");
        }
    });

    socket.on('elder_alert', (data) => {
        showElderAlert(data);
    });

    socket.on('ambulance_dialog', (data) => {
        startAmbulanceConversation(data);
    });

    socket.on('nurse_reply', (data) => {
        addChatMessage('nurse-chat', 'Nurse', data.message, 'nurse');
    });

    socket.on('emergency_alert', function(data) {
        showEmergencyAlert(data);
    });

}

function updateConnectionStatus(connected) {
    const statusEl = document.getElementById('connection-status');
    if (statusEl) {
        if (connected) {
            statusEl.textContent = '● Connected';
            statusEl.classList.remove('disconnected');
            statusEl.classList.add('connected');
        } else {
            statusEl.textContent = '● Disconnected';
            statusEl.classList.remove('connected');
            statusEl.classList.add('disconnected');
        }
    }
}

// ==================== STATUS UPDATES ====================
let lowBatteryShown = false;

function updateRobotStatus(data) {
    // Update battery
    const batteryValue = document.getElementById('battery-value');
    const batteryBar = document.getElementById('battery-bar');
    if (batteryValue && data.battery !== undefined) {
        batteryValue.textContent = `${data.battery}%`;
        if (batteryBar) {
            batteryBar.style.width = `${data.battery}%`;
        }
    }

    // Update temperature
    const tempValue = document.getElementById('temp-value');
    if (tempValue && data.temperature !== undefined) {
        tempValue.textContent = `${data.temperature}°C`;
    }

    // Update speed
    const speedValue = document.getElementById('speed-value'); // Stats card
    const speedSlider = document.getElementById('speed-slider'); // Control slider
    const speedDisplay = document.getElementById('speed-display'); // Slider label

    if (data.speed !== undefined) {
        if (speedValue) speedValue.textContent = data.speed;

        // Update slider if it exists and isn't being dragged (simple check: active element)
        if (speedSlider && document.activeElement !== speedSlider) {
            speedSlider.value = data.speed;
        }
        if (speedDisplay && document.activeElement !== speedSlider) {
            speedDisplay.textContent = data.speed;
        }
    }

    // Update simulation time
    const timeValue = document.getElementById('sim-time-value');
    if (timeValue && data.time !== undefined) {
        timeValue.textContent = data.time;
    }

    // Update task
    const taskValue = document.getElementById('task-value');
    if (taskValue && data.task !== undefined) {
        taskValue.textContent = data.task;
    }

    // FIX: Update Schedule Toggle on Connection/Status Update
    // This allows the toggle to be correct even if changed on another tab (Admin)
    if (data.schedule_enabled !== undefined) {
        const toggle = document.getElementById('card-schedule-toggle');
        if (toggle) {
            // Only update if different to avoid infinite loops if we were the one clicking it?
            // Actually, for display purposes, we should always match server state.
            if (toggle.checked !== data.schedule_enabled) {
                console.log("[DEBUG] Syncing toggle on load/status:", data.schedule_enabled);
                toggle.checked = data.schedule_enabled;
            }
        }
    }

    // Low battery warning popup REMOVED as per request
    // if (data.low_battery && !lowBatteryShown) { ... }

    // Update last update time
    const lastUpdate = document.getElementById('last-update');
    if (lastUpdate) {
        lastUpdate.textContent = `Last update: ${new Date().toLocaleTimeString()}`;
    }
}

function showLowBatteryPopup(batteryLevel) {
    // Create popup if it doesn't exist
    let popup = document.getElementById('low-battery-popup');
    if (!popup) {
        popup = document.createElement('div');
        popup.id = 'low-battery-popup';
        popup.className = 'low-battery-popup';
        popup.innerHTML = `
            <div class="popup-content">
                <span class="popup-icon">⚠️</span>
                <span class="popup-message">LOW BATTERY: ${batteryLevel}%</span>
                <span class="popup-submessage">Robot is auto-docking to charge</span>
                <button onclick="closeLowBatteryPopup()">OK</button>
            </div>
        `;
        document.body.appendChild(popup);
    } else {
        popup.querySelector('.popup-message').textContent = `LOW BATTERY: ${batteryLevel}%`;
        popup.style.display = 'flex';
    }
}

function closeLowBatteryPopup() {
    const popup = document.getElementById('low-battery-popup');
    if (popup) {
        popup.style.display = 'none';
    }
}

// ==================== CAMERA FEED ====================
function updateCameraFeed(base64Image) {
    const canvas = document.getElementById('camera-canvas');
    const placeholder = document.getElementById('camera-placeholder');

    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const img = new Image();

    img.onload = () => {
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        if (placeholder) placeholder.style.display = 'none';
    };

    img.src = `data:image/jpeg;base64,${base64Image}`;
}

// ==================== MAP RENDERING ====================
const MAP_COLORS = {
    '-1': '#000000', // Unknown - Black
    '0': '#ffffff',  // Free - White
    '1': '#808080',  // Wall - Gray
    '2': '#ffa500',  // Padding - Orange
    '3': '#00ffff',  // Elder 1 - Cyan
    '4': '#ff00ff',  // Elder 2 - Magenta
    '5': '#ffff00',  // Elder 3 - Yellow
    '6': '#ff0000',  // Medicine - Red
    '7': '#00ff00',  // Dock - Green
    '8': '#0000ff'   // Robot - Blue
};

function updateMapCanvas(mapData) {
    const canvas = document.getElementById('map-canvas');
    const placeholder = document.getElementById('map-placeholder');

    if (!canvas) return;

    try {
        // Parse map data (could be JSON array or compressed)
        let mapArray;
        if (typeof mapData === 'string') {
            mapArray = JSON.parse(mapData);
        } else {
            mapArray = mapData;
        }

        if (!mapArray || !mapArray.length) return;

        const ctx = canvas.getContext('2d');
        const rows = mapArray.length;
        const cols = mapArray[0].length;

        // Crop coordinates to show only the map area (no black borders)
        const cropStartRow = 0;    // Start row
        const cropEndRow = 480;    // End row
        const cropStartCol = 100;  // Start column
        const cropEndCol = 610;    // End column

        // Calculate actual crop bounds (ensure within map size)
        const startRow = Math.max(0, cropStartRow);
        const endRow = Math.min(rows, cropEndRow);
        const startCol = Math.max(0, cropStartCol);
        const endCol = Math.min(cols, cropEndCol);

        const croppedRows = endRow - startRow;
        const croppedCols = endCol - startCol;

        const cellWidth = canvas.width / croppedCols;
        const cellHeight = canvas.height / croppedRows;

        ctx.clearRect(0, 0, canvas.width, canvas.height);

        for (let i = startRow; i < endRow; i++) {
            for (let j = startCol; j < endCol; j++) {
                const value = mapArray[i][j];
                const color = MAP_COLORS[String(value)] || '#000000';

                ctx.fillStyle = color;
                ctx.fillRect(
                    (j - startCol) * cellWidth,
                    (i - startRow) * cellHeight,
                    cellWidth + 1,
                    cellHeight + 1
                );
            }
        }

        if (placeholder) placeholder.style.display = 'none';

    } catch (e) {
        console.error('Error rendering map:', e);
    }
}

// ==================== COMMANDS ====================
function sendCommand(command) {
    if (socket) {
        socket.emit('send_command', { command: command });
        console.log('Sent command:', command);
    } else {
        // Fallback to REST API
        fetch('/api/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command: command })
        }).then(res => res.json())
            .then(data => console.log('Command sent:', data));
    }
}

function setSpeed(speed) {
    // Save to localStorage so it persists across refreshes
    localStorage.setItem('echoRobotSpeed', speed);

    fetch('/api/speed', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ speed: speed })
    }).then(res => res.json())
        .then(data => console.log('Speed set:', data));
}

// ==================== ADMIN PAGE ====================
let manualModeEnabled = false;

function initAdminPage() {
    initSocket();

    // Manual Mode toggle button
    const btnManualMode = document.getElementById('btn-manual-mode');
    if (btnManualMode) {
        btnManualMode.addEventListener('click', () => {
            manualModeEnabled = !manualModeEnabled;
            if (manualModeEnabled) {
                sendCommand('manual_mode'); // Enable manual mode
            } else {
                sendCommand('disable_manual_mode'); // Disable and set to Idle
            }
            updateManualModeButton();
        });
    }

    // Manual control buttons - use mousedown/mouseup for hold-to-move behavior
    const btnForward = document.getElementById('btn-forward');
    const btnBackward = document.getElementById('btn-backward');
    const btnLeft = document.getElementById('btn-left');
    const btnRight = document.getElementById('btn-right');
    // btnStop removed
    const btnAdminStop = document.getElementById('btn-admin-stop');
    const btnDock = document.getElementById('btn-dock');

    // Helper to setup hold-to-move buttons
    function setupHoldButton(btn, command) {
        if (!btn) return;

        const startMove = () => {
            // Only work if manual mode is enabled
            if (!manualModeEnabled) return;
            sendCommand(command);
            btn.classList.add('active');
        };
        const stopMove = () => {
            // Only send stop if we were moving
            if (!manualModeEnabled) return;
            sendCommand('manual_stop');
            btn.classList.remove('active');
        };

        // Mouse events
        btn.addEventListener('mousedown', startMove);
        btn.addEventListener('mouseup', stopMove);
        btn.addEventListener('mouseleave', stopMove);

        // Touch events for mobile
        btn.addEventListener('touchstart', (e) => {
            e.preventDefault();
            startMove();
        });
        btn.addEventListener('touchend', (e) => {
            e.preventDefault();
            stopMove();
        });
    }

    setupHoldButton(btnForward, 'manual_forward');
    setupHoldButton(btnBackward, 'manual_back');
    setupHoldButton(btnLeft, 'manual_left');
    setupHoldButton(btnRight, 'manual_right');

    if (btnAdminStop) btnAdminStop.addEventListener('click', () => sendCommand('admin_stop'));
    if (btnDock) btnDock.addEventListener('click', () => sendCommand('goto_dock'));

    // Speed slider - load saved value from localStorage
    const speedSlider = document.getElementById('speed-slider');
    const speedDisplay = document.getElementById('speed-display');
    const applySpeed = document.getElementById('apply-speed');

    if (speedSlider && speedDisplay) {
        // REMOVED: Load saved speed from localStorage to prevent overwriting robot default (15)
        // const savedSpeed = localStorage.getItem('echoRobotSpeed');
        // if (savedSpeed) { ... }

        // Update display while dragging
        speedSlider.addEventListener('input', (e) => {
            speedDisplay.textContent = e.target.value;
        });
    }

    if (applySpeed && speedSlider) {
        applySpeed.addEventListener('click', () => {
            setSpeed(speedSlider.value);
        });
    }

    // WASD keyboard controls
    document.addEventListener('keydown', handleKeyDown);
    document.addEventListener('keyup', handleKeyUp);
}

function updateManualModeButton() {
    const btnManualMode = document.getElementById('btn-manual-mode');
    if (btnManualMode) {
        if (manualModeEnabled) {
            btnManualMode.textContent = '✓ Manual Mode ACTIVE';
            btnManualMode.style.background = 'linear-gradient(135deg, #10b981, #059669)';
        } else {
            btnManualMode.textContent = '🎯 Enable Manual Mode';
            btnManualMode.style.background = 'linear-gradient(135deg, #f59e0b, #d97706)';
        }
    }
}

// Track which keys are pressed to avoid spam
const pressedKeys = new Set();

function handleKeyDown(e) {
    // Only handle if not in an input field
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    // WASD keys only work in manual mode
    if (!manualModeEnabled) return;

    const key = e.key.toLowerCase();

    // Avoid repeating commands while key is held
    if (pressedKeys.has(key)) return;
    pressedKeys.add(key);

    switch (key) {
        case 'w':
            sendCommand('manual_forward');
            highlightButton('btn-forward');
            break;
        case 's':
            sendCommand('manual_back');
            highlightButton('btn-backward');
            break;
        case 'a':
            sendCommand('manual_left');
            highlightButton('btn-left');
            break;
        case 'd':
            sendCommand('manual_right');
            highlightButton('btn-right');
            break;
        case ' ': // Space for stop
            e.preventDefault();
            // Regular spacebar stop (Nurse Stop behavior - auto-dock still works)
            sendCommand('stop');
            break;
    }
}

function handleKeyUp(e) {
    const key = e.key.toLowerCase();
    pressedKeys.delete(key);

    // When WASD key is released, stop movement (use manual_stop for smoother control)
    if (manualModeEnabled && ['w', 's', 'a', 'd'].includes(key)) {
        sendCommand('manual_stop');
    }

    // Remove highlight from all buttons
    ['btn-forward', 'btn-backward', 'btn-left', 'btn-right'].forEach(id => {
        const btn = document.getElementById(id);
        if (btn) btn.classList.remove('active');
    });
}

function highlightButton(buttonId) {
    const btn = document.getElementById(buttonId);
    if (btn) {
        btn.classList.add('active');
    }
}

// ==================== NURSE PAGE ====================
function initNursePage() {
    initSocket();

    // Listen for elder alerts
    if (socket) {
        socket.on('elder_alert', (data) => {
            showElderAlert(data);
        });
    }
}

function showElderOptions(elderId) {
    selectedElderForAction = elderId;
    const modal = document.getElementById('elder-options-modal');
    const title = document.getElementById('elder-options-title');

    if (title) title.textContent = `Elder ${elderId} Options`;
    if (modal) modal.classList.add('active');
}

function handleElderOption(option) {
    console.log('[DEBUG] handleElderOption called with:', option);
    closeModal('elder-options-modal');

    if (option === 'monitor') {
        sendCommand(`monitor_elder_${selectedElderForAction}`);
    } else if (option === 'medicine') {
        // Show custom modal instead of prompt
        const modal = document.getElementById('medicine-input-modal');
        const input = document.getElementById('medicine-name-input');

        if (modal && input) {
            input.value = ''; // Reset input
            input.focus();
            modal.classList.add('active');

            // Allow Enter key to submit
            input.onkeydown = (e) => {
                if (e.key === 'Enter') confirmMedicine();
            };
        }
    }
}

function showElderAlert(data) {
    const alertEl = document.getElementById('elder-alert');
    const messageEl = document.getElementById('alert-message');

    if (alertEl && messageEl) {
        messageEl.textContent = `Elder ${data.elder_id} needs help: ${data.request_type}`;
        alertEl.classList.remove('hidden');

        // Store data for reply
        alertEl.dataset.elderId = data.elder_id;
        alertEl.dataset.requestType = data.request_type;
    }
}

function closeElderAlert() {
    const alertEl = document.getElementById('elder-alert');
    if (alertEl) alertEl.classList.add('hidden');
}

function replyToElder() {
    const alertEl = document.getElementById('elder-alert');
    const elderId = alertEl?.dataset.elderId;

    // Open chat modal
    const chatModal = document.getElementById('elder-chat-modal');
    const chatContainer = document.getElementById('elder-chat');

    if (chatModal && chatContainer) {
        chatContainer.innerHTML = '';
        addChatMessage('elder-chat', `Elder ${elderId}`, alertEl?.dataset.requestType || 'Help needed', 'elder');
        chatModal.classList.add('active');
    }

    closeElderAlert();
}

function sendNurseReply() {
    const input = document.getElementById('nurse-reply-input');
    if (input && input.value.trim()) {
        addChatMessage('elder-chat', 'Nurse', input.value, 'nurse');

        // Emit to socket
        if (socket) {
            socket.emit('nurse_reply', { message: input.value });
        }

        input.value = '';
    }
}
// ==================== MEDICATION SCHEDULE ====================
let medicationData = null;
function openMedicationSchedule() {
    const modal = document.getElementById('medication-modal');
    if (!modal) return;

    // Fetch data if not loaded
    fetch('/api/medication_schedule')
        .then(response => response.json())
        .then(data => {
            medicationData = data;
            renderScheduleTable();
            modal.classList.add('active');
        })
        .catch(err => console.error('Error loading schedule:', err));
}

// FIXED: Use dark theme for input fields and table cells
function renderScheduleTable() {
    const tbody = document.getElementById('schedule-body');
    if (!tbody || !medicationData) return;

    const times = ['morning', 'afternoon', 'evening'];
    const timeLabels = { 'morning': '🌅 Morning (7-12)', 'afternoon': '☀️ Afternoon (12-18)', 'evening': '🌙 Evening (18-22)' };

    tbody.innerHTML = times.map(time => `
        <tr>
            <td style="padding: 1rem; border: 1px solid #374151; font-weight: bold; background: rgba(255,255,255,0.05);">${timeLabels[time]}</td>
            <td style="padding: 1rem; border: 1px solid #374151;">
                <input type="text" class="med-input" data-elder="elder_1" data-time="${time}" value="${medicationData.elder_1[time].join(', ')}" 
                style="width: 100%; padding: 0.5rem; background: #374151; color: white; border: 1px solid #4b5563; border-radius: 0.25rem;">
            </td>
            <td style="padding: 1rem; border: 1px solid #374151;">
                <input type="text" class="med-input" data-elder="elder_2" data-time="${time}" value="${medicationData.elder_2[time].join(', ')}" 
                style="width: 100%; padding: 0.5rem; background: #374151; color: white; border: 1px solid #4b5563; border-radius: 0.25rem;">
            </td>
            <td style="padding: 1rem; border: 1px solid #374151;">
                <input type="text" class="med-input" data-elder="elder_3" data-time="${time}" value="${medicationData.elder_3[time].join(', ')}" 
                style="width: 100%; padding: 0.5rem; background: #374151; color: white; border: 1px solid #4b5563; border-radius: 0.25rem;">
            </td>
        </tr>
    `).join('');
}

function saveMedicationSchedule() {
    // Gather data from inputs
    const inputs = document.querySelectorAll('.med-input');
    const newData = { elder_1: {}, elder_2: {}, elder_3: {} };

    // Initialize structure
    ['morning', 'afternoon', 'evening'].forEach(time => {
        newData.elder_1[time] = [];
        newData.elder_2[time] = [];
        newData.elder_3[time] = [];
    });

    inputs.forEach(input => {
        const elder = input.dataset.elder;
        const time = input.dataset.time;
        // Split by comma and trim
        const meds = input.value.split(',').map(s => s.trim()).filter(s => s);
        newData[elder][time] = meds;
    });

    // Send to server
    fetch('/api/medication_schedule', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newData)
    })
        .then(response => response.json())
        .then(data => {
            console.log('Schedule saved:', data);
            medicationData = newData;
            closeModal('medication-modal');
            alert('Schedule saved successfully!');
        })
        .catch(err => console.error('Error saving schedule:', err));
}
// ==================== MEDICINE INPUT ====================
function confirmMedicine() {
    const input = document.getElementById('medicine-name-input');
    const modal = document.getElementById('medicine-input-modal');

    if (input && input.value.trim()) {
        const medName = input.value.trim();
        // Send command
        sendCommand(`give_medicine_${selectedElderForAction}_${medName}`);
        // Close modal
        closeModal('medicine-input-modal');
    } else {
        alert("Please enter a medicine name");
        input.focus();
    }
}
// ==================== ELDER PAGE ====================
function initElderPage(elderId) {
    currentElderId = elderId;
    initSocket();

    // Listen for nurse replies
    if (socket) {
        socket.on('nurse_reply', (data) => {
            addChatMessage('nurse-chat', 'Nurse', data.message, 'nurse');
        });
    }
}

function showNurseRequestModal() {
    const modal = document.getElementById('nurse-request-modal');
    if (modal) modal.classList.add('active');
}

function sendNurseRequest(requestType) {
    closeModal('nurse-request-modal');

    // Emit request to server
    if (socket) {
        socket.emit('nurse_request', {
            elder_id: currentElderId,
            request_type: requestType
        });
    }

    // Open chat modal with automated message
    const chatModal = document.getElementById('nurse-chat-modal');
    const chatContainer = document.getElementById('nurse-chat');

    if (chatModal && chatContainer) {
        chatContainer.innerHTML = '';
        addChatMessage('nurse-chat', `Elder ${currentElderId}`, `I need help with: ${requestType}`, 'elder');
        chatModal.classList.add('active');

        // Simulate nurse reply after delay
        setTimeout(() => {
            addChatMessage('nurse-chat', 'Nurse', "I'm on my way! Please stay calm.", 'nurse');
        }, 2000);
    }
}

// Toggle medication schedule enablement
function toggleSchedule(enabled) {
    const command = enabled ? 'enable_schedule' : 'disable_schedule';
    sendCommand(command);
    console.log(`Schedule ${enabled ? 'Enabled' : 'Disabled'}`);
}


function showCustomMessage() {
    const customInput = document.getElementById('custom-message-input');
    if (customInput) customInput.classList.toggle('hidden');
}

function sendCustomRequest() {
    const input = document.getElementById('custom-message');
    if (input && input.value.trim()) {
        sendNurseRequest(input.value);
        input.value = '';
    }
}

function sendElderMessage() {
    const input = document.getElementById('elder-reply-input');
    if (input && input.value.trim()) {
        addChatMessage('nurse-chat', `Elder ${currentElderId}`, input.value, 'elder');
        input.value = '';
    }
}

// ==================== AMBULANCE DIALOG ====================
function showAmbulanceDialog(caller, elderId = null) {
    const modal = document.getElementById('ambulance-modal');
    const chatContainer = document.getElementById('ambulance-chat');
    const callStatus = document.getElementById('call-status');

    if (modal && chatContainer) {
        chatContainer.innerHTML = '';
        if (callStatus) callStatus.textContent = 'Connecting to Emergency Services...';
        modal.classList.add('active');

        // Start simulated conversation locally (no socket emit to avoid duplicates)
        startAmbulanceConversation({
            caller: caller,
            elder_id: elderId || currentElderId,
            location: 'Elder Care Home ECHO, 123 Care Street, Munich'
        });
    }
}

function startAmbulanceConversation(data) {
    const chatContainer = document.getElementById('ambulance-chat');
    const callStatus = document.getElementById('call-status');

    if (!chatContainer) return;

    const conversation = [
        { delay: 1000, sender: 'Emergency Services', message: '112, what is your emergency?', type: 'emergency' },
        { delay: 3000, sender: 'ECHO Robot', message: `This is ECHO, an Elder Care Robot calling from ${data.location}.`, type: 'robot' },
        { delay: 5000, sender: 'ECHO Robot', message: `We have a medical emergency. ${data.elder_id ? `Elder ${data.elder_id} requires immediate assistance.` : 'An elder requires immediate assistance.'}`, type: 'robot' },
        { delay: 7000, sender: 'Emergency Services', message: 'Understood. Can you describe the situation?', type: 'emergency' },
        { delay: 9000, sender: 'ECHO Robot', message: 'The elder has activated the emergency call button. They may need urgent medical attention.', type: 'robot' },
        { delay: 11000, sender: 'Emergency Services', message: 'We are dispatching an ambulance to your location right now.', type: 'emergency' },
        { delay: 13000, sender: 'ECHO Robot', message: 'Thank you. The address is Elder Care Home ECHO, 123 Care Street, Munich.', type: 'robot' },
        { delay: 15000, sender: 'Emergency Services', message: 'Confirmed. Ambulance ETA is approximately 8 minutes. Please keep the elder calm and do not move them if they are injured.', type: 'emergency' },
        { delay: 17000, sender: 'ECHO Robot', message: 'Understood. I will monitor the elder and keep the entrance clear for the paramedics.', type: 'robot' },
        { delay: 19000, sender: 'Emergency Services', message: 'Good. Stay on the line if possible. Help is on the way.', type: 'emergency' }
    ];

    if (callStatus) {
        setTimeout(() => {
            callStatus.textContent = 'Connected to 112 Emergency Services';
        }, 800);
    }

    conversation.forEach((item) => {
        setTimeout(() => {
            addChatMessage('ambulance-chat', item.sender, item.message, item.type);
        }, item.delay);
    });

    // Final status
    setTimeout(() => {
        if (callStatus) callStatus.textContent = '🚑 Ambulance dispatched - ETA 8 minutes';
    }, 20000);
}

// ==================== UTILITIES ====================
function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) modal.classList.remove('active');
}

function addChatMessage(containerId, sender, message, type) {
    const container = document.getElementById(containerId);
    if (!container) return;

    const msgDiv = document.createElement('div');
    msgDiv.className = `chat-message ${type}`;
    msgDiv.innerHTML = `
        <div class="sender">${sender}</div>
        <div class="text">${message}</div>
    `;

    container.appendChild(msgDiv);
    container.scrollTop = container.scrollHeight;
}

// Close modal when clicking outside
document.addEventListener('click', (e) => {
    if (e.target.classList.contains('modal')) {
        e.target.classList.remove('active');
    }
});


// ============================================
// EMERGENCY TEMPERATURE ALERT POPUP
// Author: Shubham Ilhe
// ============================================


function showEmergencyAlert(data) {
    // Create alert box dynamically
    const alertBox = document.createElement('div');
    alertBox.className = 'emergency-alert-popup';
    alertBox.innerHTML = `
        <div class="emergency-alert-content">
            <div class="alert-icon">🚨</div>
            <div class="alert-title">CRITICAL ALERT</div>
            <div class="alert-message">
                Elder ${data.elder_id} - ${data.vitals.emergency_reason}
            </div>
            <div class="alert-details">
                Temperature: <strong>${data.vitals.temperature_c}°C</strong>
            </div>
            <div class="alert-timestamp">${data.vitals.timestamp}</div>
        </div>
    `;
    
    document.body.appendChild(alertBox);
    
    // Auto-remove after 3 seconds
    setTimeout(() => {
        alertBox.classList.add('fade-out');
        setTimeout(() => alertBox.remove(), 500);
    }, 3000);
}

// --- NEW LOGS MODAL LOGIC (clean, DB-only) ---

let currentLogsData = null;

async function openLogsModal() {
  const modal = document.getElementById('logs-modal');
  if (!modal) return;

  modal.classList.add('active');
  backToLogSelection();

  await fetchLogsData(); // important
}


function backToLogSelection() {
  const sel = document.getElementById('logs-selection-view');
  const detail = document.getElementById('logs-detail-view');
  if (sel) sel.style.display = 'flex';
  if (detail) detail.style.display = 'none';
}

async function fetchLogsData() {
  try {
    const response = await fetch('/api/logs');
    if (!response.ok) throw new Error('Failed to fetch /api/logs');
    currentLogsData = await response.json();
  } catch (error) {
    console.error('Error fetching logs:', error);
    currentLogsData = null;
  }
}

function showElderLogs(elderId) {
  const sel = document.getElementById('logs-selection-view');
  const detail = document.getElementById('logs-detail-view');
  if (sel) sel.style.display = 'none';
  if (detail) detail.style.display = 'block';

  const detailsContainer = document.getElementById('elder-static-details');
  const medsBody = document.getElementById('elder-med-logs-body');
  const vitalsBody = document.getElementById('elder-vital-logs-body');

  if (!currentLogsData) {
    if (detailsContainer) detailsContainer.innerHTML = `<div>Loading logs...</div>`;
    if (medsBody) medsBody.innerHTML = `<tr><td colspan="3" style="padding:0.75rem;">Loading...</td></tr>`;
    if (vitalsBody) vitalsBody.innerHTML = `<tr><td colspan="3" style="padding:0.75rem;">Loading...</td></tr>`;
    return;
  }

  // Accept both backend shapes:
  // old: medlogs/vitallogs + elderid
  // new: med_logs/vital_logs + elder_id
  const elders = currentLogsData.elders || [];
  const medlogsAll = currentLogsData.medlogs || currentLogsData.med_logs || [];
  const vitallogsAll = currentLogsData.vitallogs || currentLogsData.vital_logs || [];

  const elderInfo = elders.find(e => String(e.id) === String(elderId));

  const medLogs = medlogsAll.filter(m =>
    String(m.elderid ?? m.elder_id) === String(elderId)
  );

  const vitalLogs = vitallogsAll.filter(v =>
    String(v.elderid ?? v.elder_id) === String(elderId)
  );

  // Static details
  if (detailsContainer) {
    if (elderInfo) {
      detailsContainer.innerHTML = `
        <div><strong>Name:</strong> ${elderInfo.name ?? ''}</div>
        <div><strong>Age:</strong> ${elderInfo.age ?? ''}</div>
        <div><strong>Condition:</strong> ${elderInfo.history ?? elderInfo.medicalhistory ?? ''}</div>
      `;
    } else {
      detailsContainer.innerHTML = `<div>No static data found for Elder ${elderId}</div>`;
    }
  }

  // Med table
  if (medsBody) {
    const rows = medLogs.slice(0, 10);
    medsBody.innerHTML = rows.length
      ? rows.map(log => `
          <tr>
            <td style="padding:0.75rem; border-bottom:1px solid #374151;">${log.time ?? log.timestamp ?? ''}</td>
            <td style="padding:0.75rem; border-bottom:1px solid #374151;">${log.type ?? log.deliverytype ?? ''}</td>
            <td style="padding:0.75rem; border-bottom:1px solid #374151;">${log.medicine ?? log.medicinename ?? log.medicine_name ?? ''}</td>
          </tr>
        `).join('')
      : `<tr><td colspan="3" style="padding:0.75rem;">No records found.</td></tr>`;
  }

  // Vital table
// Vitals table (DB-only)
    if (vitalsBody) {
    const rows = vitalLogs.slice(0, 10);
    vitalsBody.innerHTML = rows.length
        ? rows.map(log => {
            const temp = (log.temp ?? log.temperature ?? '');
            const bp = (log.bp ?? log.bloodpressure ?? ''); // NEW LINE
            const emergencyFlag = (log.emergency ?? log.isemergency ?? log.is_alert);
            const isAlert = (emergencyFlag === 'YES' || emergencyFlag === true);

            return `
            <tr>
                <td style="padding:0.75rem; border-bottom:1px solid #374151;">${log.time ?? log.timestamp ?? ''}</td>
                <td style="padding:0.75rem; border-bottom:1px solid #374151;">${temp}${temp !== '' ? '°C' : ''}</td>
                <td style="padding:0.75rem; border-bottom:1px solid #374151;">${bp}</td>
                <td style="padding:0.75rem; border-bottom:1px solid #374151;">
                ${isAlert
                    ? '<span style="color:#ef4444">⚠️ Alert</span>'
                    : '<span style="color:#10b981">Normal</span>'}
                </td>
            </tr>
            `;
        }).join('')
        : '<tr><td colspan="4" style="padding:0.75rem;">No records found.</td></tr>'; // 4 columns now
}

}




