import time
import base64
import cv2
import numpy as np
import threading
from src.services.scanner.manager import ScannerManager

def create_dummy_frame():
    # Create a 640x480 black image with a white rectangle
    img = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(img, (100, 100), (300, 300), (255, 255, 255), -1)
    _, buffer = cv2.imencode('.jpg', img)
    return buffer.tobytes()

def test_manual_scan_debug_capture():
    manager = ScannerManager()
    manager.start()

    # 1. Push a frame
    frame_data = create_dummy_frame()
    manager.push_frame(frame_data)

    time.sleep(0.1) # Let worker process it (auto scan)

    # 2. Trigger Manual Scan
    print("Triggering manual scan...")
    manager.trigger_manual_scan()

    # 3. Push another frame (needed to trigger the loop inside worker)
    # The worker waits for input_queue.get()
    manager.push_frame(frame_data)

    time.sleep(0.5) # Wait for processing

    snapshot = manager.get_debug_snapshot()
    captured_image = snapshot.get("captured_image")

    print(f"Captured Image present: {captured_image is not None}")
    if captured_image:
        print(f"Captured Image length: {len(captured_image)}")
        print(f"Captured Image start: {captured_image[:30]}...")

    print(f"Scan Result: {snapshot.get('scan_result')}")
    print(f"Logs: {snapshot.get('logs')}")

    manager.stop()

if __name__ == "__main__":
    test_manual_scan_debug_capture()
