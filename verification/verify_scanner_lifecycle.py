
import time
import threading
import sys
import os

# Adjust path to import src
sys.path.append(os.getcwd())

from src.services.scanner.manager import scanner_manager

def verify_lifecycle():
    print("--- Verifying Scanner Lifecycle ---")

    # 1. Initial State
    print(f"Initial running state: {scanner_manager.running}")
    if scanner_manager.running:
        print("WARN: Scanner was already running.")

    # 2. Start Scanner
    print("Starting scanner...")
    scanner_manager.start()
    time.sleep(0.5)

    if not scanner_manager.running:
        print("FAIL: Scanner failed to start.")
        return False

    if not scanner_manager.thread or not scanner_manager.thread.is_alive():
        print("FAIL: Scanner thread is not alive.")
        return False

    print("PASS: Scanner started and thread is alive.")

    # 3. Simulate multiple starts (Idempotency)
    old_thread = scanner_manager.thread
    print("Calling start() again...")
    scanner_manager.start()
    if scanner_manager.thread != old_thread:
        print("FAIL: start() created a new thread instead of reusing existing one.")
        return False
    print("PASS: start() is idempotent.")

    # 4. Stop Scanner
    print("Stopping scanner...")
    scanner_manager.stop()
    time.sleep(0.5)

    if scanner_manager.running:
        print("FAIL: Scanner failed to stop (running=True).")
        return False

    if scanner_manager.thread and scanner_manager.thread.is_alive():
        print("FAIL: Scanner thread is still alive after stop().")
        return False

    print("PASS: Scanner stopped successfully.")

    return True

if __name__ == "__main__":
    if verify_lifecycle():
        print("\nLifecycle Verification: SUCCESS")
    else:
        print("\nLifecycle Verification: FAILED")
        sys.exit(1)
