
import time
import threading
import sys
import os
import cv2
import numpy as np

# Adjust path to import src
sys.path.append(os.getcwd())

from src.services.scanner.manager import scanner_manager

def verify_status_flow():
    print("--- Verifying Scanner Status Flow ---")

    # 1. Start Scanner
    print("Starting scanner...")
    scanner_manager.start()
    time.sleep(0.5)

    # 2. Check Initial Status (Should be Stopped/Paused by default)
    status = scanner_manager.get_status()
    paused = scanner_manager.is_paused()
    print(f"Initial Status: {status}, Paused: {paused}")

    if not paused or status not in ["Stopped", "Paused"]:
        print(f"FAIL: Scanner should be paused/stopped by default. Got: {status}")
        return False

    print("PASS: Initial state is correct.")

    # 3. Submit a Dummy Task
    print("Submitting dummy task...")
    # Create a dummy image
    dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", dummy_img)
    content = buf.tobytes()

    scanner_manager.submit_scan(content, {"tracks": []}, label="Test Scan", filename="test.jpg")

    # 4. Resume (Start Processing)
    print("Resuming scanner...")
    scanner_manager.resume()
    time.sleep(0.2)

    # 5. Check Processing Status
    status = scanner_manager.get_status()
    print(f"Status after resume: {status}")

    # It should be "Processing: test.jpg" or "Idle" if it finished very fast.
    # Since we have no tracks, it might be fast.
    # Let's check debug_state logs to confirm it ran.
    logs = scanner_manager.debug_state.get('logs', [])
    print(f"Logs: {logs[:3]}")

    found_processing = False
    for log in logs:
        if "Processing: test.jpg" in log or "Started: test.jpg" in log:
            found_processing = True
            break

    if not found_processing:
        # Maybe it's still running?
        if "Processing" in status:
             print("PASS: Scanner entered processing state.")
        else:
             print("WARN: Did not catch 'Processing' state in logs yet.")
    else:
        print("PASS: Scanner processed the task.")

    # 6. Check Idle Status
    # Wait for it to finish (increase timeout for model downloads)
    print("Waiting for scan to complete...")
    for _ in range(60):
        status = scanner_manager.get_status()
        if status == "Idle":
            break
        time.sleep(1.0)

    final_status = scanner_manager.get_status()
    print(f"Final Status: {final_status}")

    if final_status != "Idle":
        print("FAIL: Scanner did not return to Idle.")
        return False

    print("PASS: Scanner returned to Idle.")

    # 7. Pause again
    print("Pausing scanner...")
    scanner_manager.pause()
    time.sleep(0.2)

    if not scanner_manager.is_paused():
        print("FAIL: Scanner failed to pause.")
        return False

    print("PASS: Scanner paused successfully.")

    scanner_manager.stop()
    return True

if __name__ == "__main__":
    if verify_status_flow():
        print("\nStatus Flow Verification: SUCCESS")
    else:
        print("\nStatus Flow Verification: FAILED")
        sys.exit(1)
