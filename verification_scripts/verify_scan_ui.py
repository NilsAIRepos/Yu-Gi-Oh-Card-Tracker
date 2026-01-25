from playwright.sync_api import sync_playwright, expect
import os
import time

def verify_scan_ui():
    print("Starting Scan UI Verification...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = context.new_page()

        try:
            # 1. Navigate to Home
            page.goto("http://localhost:8080")
            page.wait_for_load_state("networkidle")

            # 2. Click Sidebar Link to Scan
            # Assuming there is a sidebar or navigation. If not, go direct.
            # Let's try direct navigation to be safe
            page.goto("http://localhost:8080/scan")
            page.wait_for_load_state("networkidle")

            print("Navigated to Scan Page.")

            # 3. Verify Live Scan Tab is active (default)
            # Check for specific elements of the new layout

            # Check Header
            expect(page.get_by_role("button", name="COMMIT")).to_be_visible()
            expect(page.get_by_text("DEFAULTS:")).to_be_visible()

            # Check Left Column (Camera)
            expect(page.get_by_role("button", name="START CAMERA")).to_be_visible()
            expect(page.get_by_role("button", name="CAPTURE & SCAN")).to_be_visible()

            # Check Right Column (Recent Scans)
            expect(page.get_by_text("Recent Scans")).to_be_visible()
            expect(page.get_by_role("button", name="UNDO")).to_be_visible()
            expect(page.get_by_role("button", name="UPDATE")).to_be_visible()
            expect(page.get_by_role("button", name="REMOVE ALL")).to_be_visible()

            # Check Search Input
            expect(page.get_by_placeholder("Search...")).to_be_visible()

            print("All key elements found.")

            # 4. Take Screenshot
            os.makedirs("verification_screenshots", exist_ok=True)
            screenshot_path = "verification_screenshots/scan_ui_layout.png"
            page.screenshot(path=screenshot_path)
            print(f"Screenshot saved to {screenshot_path}")

        except Exception as e:
            print(f"Verification Failed: {e}")
            page.screenshot(path="verification_screenshots/error.png")
            raise e
        finally:
            browser.close()

if __name__ == "__main__":
    # Give the server a moment to ensure it's up if just started
    time.sleep(5)
    verify_scan_ui()
