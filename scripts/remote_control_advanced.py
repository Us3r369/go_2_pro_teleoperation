import asyncio
import threading
import logging
import time
import json
from queue import Queue
from flask import Flask, Response, request, jsonify, render_template_string
import cv2
from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from go2_webrtc_driver.constants import RTC_TOPIC, SPORT_CMD
from aiortc import MediaStreamTrack

# Set up logging
logging.basicConfig(level=logging.INFO)

# Global event loop
event_loop = None

# Create Flask app
app = Flask(__name__)

# Create a queue to store video frames. Bounded + drop-oldest so it can't grow
# without limit when no browser is consuming the MJPEG stream.
frame_queue = Queue(maxsize=2)

# WebRTC Setup
# LocalAP: this machine is joined to the robot's own Wi-Fi hotspot (fixed IP 192.168.12.1).
conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)
# LocalSTA alternative (robot on the same LAN):
# conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip="192.168.1.150")

# Function to receive video frames from the robot
async def recv_camera_stream(track: MediaStreamTrack):
    logging.info("Started camera stream receiver")
    while True:
        try:
            frame = await track.recv()
        except Exception as e:
            logging.info(f"Video track ended: {e}")
            break
        img = frame.to_ndarray(format="bgr24")
        if frame_queue.full():
            try:
                frame_queue.get_nowait()
            except Exception:
                pass
        frame_queue.put_nowait(img)

# Generate MJPEG stream for web
def generate_frames():
    while True:
        if not frame_queue.empty():
            frame = frame_queue.get()
            ret, buffer = cv2.imencode('.jpg', frame)
            if not ret:
                logging.warning("Failed to encode frame")
                continue
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        else:
            time.sleep(0.01)

# HTML template for the web interface
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Go2 Robot Control</title>
    <style>
        body { 
            text-align: center; 
            font-family: Arial, sans-serif;
            background-color: #f0f2f5;
            margin: 0;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background-color: white;
            padding: 20px;
            border-radius: 10px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .video-container {
            margin: 20px 0;
        }
        #video { 
            max-width: 640px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .control-section {
            margin: 20px 0;
            padding: 15px;
            background-color: #f8f9fa;
            border-radius: 8px;
        }
        .control-section h2 {
            color: #1a73e8;
            margin-bottom: 15px;
        }
        .button-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
            gap: 10px;
            margin: 10px 0;
        }
        button { 
            padding: 12px 20px;
            margin: 5px;
            font-size: 16px;
            cursor: pointer;
            border: none;
            border-radius: 5px;
            background-color: #1a73e8;
            color: white;
            transition: background-color 0.3s;
        }
        button:hover {
            background-color: #1557b0;
        }
        .movement-controls {
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 10px;
            max-width: 300px;
            margin: 0 auto;
        }
        .status {
            margin-top: 20px;
            padding: 10px;
            background-color: #e8f0fe;
            border-radius: 5px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Go2 Robot Control</h1>
        
        <div class="video-container">
            <img id="video" src="/video_feed"/>
        </div>

        <div class="control-section">
            <h2>Movement Controls</h2>
            <div class="movement-controls">
                <div></div>
                <button onclick="sendCommand('forward')">↑</button>
                <div></div>
                <button onclick="sendCommand('left')">←</button>
                <button onclick="sendCommand('stop')">Stop</button>
                <button onclick="sendCommand('right')">→</button>
                <div></div>
                <button onclick="sendCommand('backward')">↓</button>
                <div></div>
            </div>
            <div class="button-grid" style="max-width: 300px; margin: 20px auto;">
                <button onclick="sendCommand('turn_left')">↺ Turn Left</button>
                <button onclick="sendCommand('turn_right')">↻ Turn Right</button>
            </div>
        </div>

        <div class="control-section">
            <h2>Basic Actions</h2>
            <div class="button-grid">
                <button onclick="sendCommand('stand')">Stand</button>
                <button onclick="sendCommand('sit')">Sit</button>
                <button onclick="sendCommand('standup')">Stand Up</button>
                <button onclick="sendCommand('recovery_stand')">Recovery Stand</button>
            </div>
        </div>

        <div class="control-section">
            <h2>Special Moves</h2>
            <div class="button-grid">
                <button onclick="sendCommand('hello')">Hello</button>
                <button onclick="sendCommand('stretch')">Stretch</button>
                <button onclick="sendCommand('dance1')">Dance 1</button>
                <button onclick="sendCommand('dance2')">Dance 2</button>
                <button onclick="sendCommand('wiggle_hips')">Wiggle Hips</button>
                <button onclick="sendCommand('finger_heart')">Finger Heart</button>
            </div>
        </div>

        <div class="control-section">
            <h2>Advanced Moves</h2>
            <div class="button-grid">
                <button onclick="sendCommand('front_flip')">Front Flip</button>
                <button onclick="sendCommand('back_flip')">Back Flip</button>
                <button onclick="sendCommand('left_flip')">Left Flip</button>
                <button onclick="sendCommand('right_flip')">Right Flip</button>
                <button onclick="sendCommand('front_jump')">Front Jump</button>
                <button onclick="sendCommand('front_pounce')">Front Pounce</button>
            </div>
        </div>

        <div class="control-section">
            <h2>Safe Shutdown</h2>
            <p style="margin: 0 0 10px; color: #5f6368; font-size: 14px;">
                Relaxes the motors so the robot settles gently to the ground. Use this
                to park the robot in a safe position before quitting the script.
            </p>
            <div class="button-grid">
                <button onclick="sendCommand('damp')" style="background-color: #34a853;">Damp (Relax Motors)</button>
            </div>
        </div>

        <div class="status" id="status">
            Status: Ready
        </div>
    </div>

    <script>
        function sendCommand(action) {
            document.getElementById('status').textContent = 'Status: Executing ' + action;
            fetch('/move', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({action: action})
            })
            .then(response => response.json())
            .then(data => {
                document.getElementById('status').textContent = 'Status: ' + action + ' completed';
            })
            .catch(error => {
                document.getElementById('status').textContent = 'Status: Error executing ' + action;
            });
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

async def ensure_normal_mode():
    try:
        response = await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["MOTION_SWITCHER"],
            {"api_id": 1001}
        )
        logging.info(f"Normal mode check response: {response}")
        code = response['data']['header']['status']['code']
        if code == 0:
            data = json.loads(response['data']['data'])
            current_mode = data.get('name', '')
            if current_mode != "normal":
                await conn.datachannel.pub_sub.publish_request_new(
                    RTC_TOPIC["MOTION_SWITCHER"],
                    {
                        "api_id": 1002,
                        "parameter": {"name": "normal"}
                    }
                )
                logging.info("Switched robot mode to 'normal'")
                await asyncio.sleep(3)
    except Exception as e:
        logging.error(f"Error ensuring normal mode: {e}")

@app.route('/move', methods=['POST'])
def move_robot():
    data = request.json
    action = data.get('action')

    async def do_move():
        try:
            logging.info(f"Received action: {action}")
            # Damp is a safe-shutdown relax: skip the mode switch (which could make
            # the robot stand up first) and just relax the motors in place.
            if action != 'damp':
                await ensure_normal_mode()

            if action in ['forward', 'backward', 'left', 'right', 'turn_left', 'turn_right']:
                await send_sport_command("BalanceStand")
                await asyncio.sleep(1)

            if action == 'forward':
                await send_movement_command(x=0.4)
            elif action == 'backward':
                await send_movement_command(x=-0.4)
            elif action == 'left':
                await send_movement_command(y=0.4)
            elif action == 'right':
                await send_movement_command(y=-0.4)
            elif action == 'turn_left':
                await send_movement_command(z=0.4)
            elif action == 'turn_right':
                await send_movement_command(z=-0.4)
            elif action == 'stop':
                await send_movement_command(x=0, y=0, z=0)

            elif action == 'stand':
                await send_sport_command("BalanceStand")
            elif action == 'sit':
                await send_sport_command("Sit")
            elif action == 'standup':
                await send_sport_command("StandUp")
            elif action == 'recovery_stand':
                await send_sport_command("RecoveryStand")
            elif action == 'damp':
                # Relax the motors so the robot settles gently to the ground.
                # Safe "park before exit" posture.
                await send_sport_command("Damp")

            elif action == 'hello':
                await send_sport_command("Hello")
            elif action == 'stretch':
                await send_sport_command("Stretch")
            elif action == 'dance1':
                await send_sport_command("Dance1")
            elif action == 'dance2':
                await send_sport_command("Dance2")
            elif action == 'wiggle_hips':
                await send_sport_command("WiggleHips")
            elif action == 'finger_heart':
                await send_sport_command("FingerHeart")

            elif action == 'front_flip':
                await send_sport_command("FrontFlip")
            elif action == 'back_flip':
                await send_sport_command("BackFlip")
            elif action == 'left_flip':
                await send_sport_command("LeftFlip")
            elif action == 'right_flip':
                await send_sport_command("RightFlip")
            elif action == 'front_jump':
                await send_sport_command("FrontJump")
            elif action == 'front_pounce':
                await send_sport_command("FrontPounce")
        except Exception as e:
            logging.error(f"Error executing move: {e}")

    # Run in background loop
    future = asyncio.run_coroutine_threadsafe(do_move(), event_loop)
    try:
        future.result(timeout=30)
    except Exception as e:
        logging.error(f"Async call failed: {e}")
    return jsonify({"status": "ok"})

async def send_movement_command(x=0, y=0, z=0):
    logging.info(f"Sending movement x={x}, y={y}, z={z}")
    try:
        resp1 = await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["SPORT_MOD"],
            {
                "api_id": SPORT_CMD["Move"],
                "parameter": {"x": x, "y": y, "z": z}
            }
        )
        await asyncio.sleep(0.5)
        resp2 = await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["SPORT_MOD"],
            {
                "api_id": SPORT_CMD["Move"],
                "parameter": {"x": 0, "y": 0, "z": 0}
            }
        )
        logging.info(f"Movement responses: {resp1}, {resp2}")
    except Exception as e:
        logging.error(f"Movement command failed: {e}")

async def send_sport_command(command_name):
    try:
        api_id = SPORT_CMD[command_name]
        logging.info(f"Sending sport command {command_name} (ID {api_id})")
        resp = await conn.datachannel.pub_sub.publish_request_new(
            RTC_TOPIC["SPORT_MOD"],
            {
                "api_id": api_id
            }
        )
        logging.info(f"Sport command response: {resp}")
    except Exception as e:
        logging.error(f"Sport command {command_name} failed: {e}")

async def start_webrtc():
    await conn.connect()
    conn.video.switchVideoChannel(True)
    conn.video.add_track_callback(lambda track: asyncio.create_task(recv_camera_stream(track)))

if __name__ == '__main__':
    def run_webrtc_loop():
        global event_loop
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        event_loop.run_until_complete(start_webrtc())
        event_loop.run_forever()

    webrtc_thread = threading.Thread(target=run_webrtc_loop)
    webrtc_thread.start()

    # Port 5000 is taken by macOS AirPlay Receiver (Control Center), so use 8080.
    app.run(host='0.0.0.0', port=8080, debug=False)