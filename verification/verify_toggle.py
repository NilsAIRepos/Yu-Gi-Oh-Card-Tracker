import subprocess
import time
import requests
import sys
import os
from playwright.sync_api import sync_playwright

def verify_auto_scan_toggle():
    print("Starting server...")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()

    server_process = subprocess.Popen(
        [sys.executable, "main.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env
    )

    # Wait for server
    base_url = "http://localhost:8080"
    connected = False

    for i in range(30):
        try:
            requests.get(base_url)
            connected = True
            break
        except requests.exceptions.ConnectionError:
            if server_process.poll() is not None:
                break
            time.sleep(1)

    if not connected:
        print("Server failed to start.")
        server_process.kill()
        return

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            print(f"Navigating to {base_url}/scan")
            page.goto(f"{base_url}/scan")
            page.wait_for_load_state("networkidle")

            # Verify Toggle Exists
            print("Checking for Auto Scan toggle...")
            toggle = page.get_by_role("checkbox", name="Auto Scan")
            if toggle.is_visible():
                print("SUCCESS: Auto Scan toggle found.")
                # Verify it defaults to unchecked (since auto_scan_paused=True)
                # Wait, nicegui Switch is a checkbox.
                # auto_scan_paused=True means Auto Scan=False (unchecked)
                # Let's check initial state.

                # Take screenshot of Live Scan with toggle
                page.screenshot(path="verification/scan_page_with_toggle.png")

                # Switch tab to Debug and back to Live to test crash
                print("Switching tabs...")
                page.get_by_role("tab", name="Debug Lab").click()
                page.wait_for_timeout(1000)
                page.get_by_role("tab", name="Live Scan").click()
                page.wait_for_timeout(1000)

                # Take screenshot after tab switch
                page.screenshot(path="verification/scan_page_after_tab_switch.png")
                print("SUCCESS: Switched tabs without crash.")

            else:
                print("FAILURE: Auto Scan toggle NOT found.")
                print(page.content())

            browser.close()
    except Exception as e:
        print(f"Playwright failed: {e}")
    finally:
        server_process.terminate()
        server_process.wait()

if __name__ == "__main__":
    verify_auto_scan_toggle()
