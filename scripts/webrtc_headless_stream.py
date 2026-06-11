import asyncio
import threading
import logging
import time
from queue import Queue
from flask import Flask, Response
import cv2
from go2_webrtc_driver.webrtc_driver import Go2WebRTCConnection, WebRTCConnectionMethod
from aiortc import MediaStreamTrack

# Logging
logging.basicConfig(level=logging.INFO)

# Flask App
app = Flask(__name__)

# Shared Queue for frames. Bounded + drop-oldest so it can't grow without limit
# when no client is consuming the MJPEG stream.
frame_queue = Queue(maxsize=2)

# WebRTC Setup
# LocalAP: this machine is joined to the robot's own Wi-Fi hotspot (fixed IP 192.168.12.1).
conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalAP)
# LocalSTA alternative (robot on the same LAN):
# conn = Go2WebRTCConnection(WebRTCConnectionMethod.LocalSTA, ip="192.168.1.150")

# Async function to receive frames from robot
async def recv_camera_stream(track: MediaStreamTrack):
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

# Async setup + loop
def run_asyncio_loop(loop):
    asyncio.set_event_loop(loop)

    async def setup():
        try:
            await conn.connect()
            conn.video.switchVideoChannel(True)
            conn.video.add_track_callback(recv_camera_stream)
        except Exception as e:
            logging.error(f"[WebRTC ERROR] {e}")

    loop.run_until_complete(setup())
    loop.run_forever()

# MJPEG Generator for Flask
def generate_frames():
    while True:
        if not frame_queue.empty():
            frame = frame_queue.get()
            ret, buffer = cv2.imencode('.jpg', frame)
            if not ret:
                continue
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
        else:
            time.sleep(0.01)

@app.route('/')
def index():
    return '<h1>Robot Live Stream</h1><img src="/video" width="720"/>'

@app.route('/video')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

def start_flask():
    # Port 5000 is taken by macOS AirPlay Receiver (Control Center), so use 8080.
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

# Entry point
if __name__ == "__main__":
    # Asyncio WebRTC thread
    loop = asyncio.new_event_loop()
    asyncio_thread = threading.Thread(target=run_asyncio_loop, args=(loop,))
    asyncio_thread.start()

    # Flask web stream
    start_flask()

    # On script end, stop async loop
    loop.call_soon_threadsafe(loop.stop)
    asyncio_thread.join()