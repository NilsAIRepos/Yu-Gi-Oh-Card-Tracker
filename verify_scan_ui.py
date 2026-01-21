from playwright.sync_api import sync_playwright

def verify_scan_ui():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_viewport_size({"width": 1280, "height": 1024})

        print("Navigating to home...")
        page.goto("http://localhost:8080/")

        print("Clicking Scan Cards...")
        # Assuming there is a menu or link to Scan Cards.
        # Based on file structure, it's likely part of the main layout.
        # I'll check for "Scan Cards" text or similar.
        page.get_by_text("Scan Cards").click()

        print("Waiting for Scan Page...")
        page.wait_for_load_state("networkidle")

        # Verify Capture Button exists
        print("Checking for Capture & Scan button...")
        if page.get_by_role("button", name="Capture & Scan").count() > 0:
            print("Found Capture & Scan button.")
        else:
            print("Capture & Scan button NOT found!")

        # Verify Auto Scan is gone
        if page.get_by_text("Auto Scan").count() == 0:
             print("Auto Scan is correctly removed/renamed.")
        else:
             print("Auto Scan text found (might be label for something else or residual).")

        # Go to Debug Lab
        print("Switching to Debug Lab...")
        page.get_by_text("Debug Lab").click()

        # Check controls
        print("Checking Debug Lab controls...")
        if page.get_by_text("Preprocessing Strategy").count() > 0:
             print("Preprocessing Strategy label found.")

        if page.get_by_text("EasyOCR").count() > 0:
             print("EasyOCR checkbox found.")

        if page.get_by_text("PaddleOCR").count() > 0:
             print("PaddleOCR checkbox found.")

        # Check Result Zones
        print("Checking Result Zones...")
        if page.get_by_text("Track 1: EasyOCR (Full Frame)").count() > 0:
             print("Track 1 Full zone found.")
        if page.get_by_text("Track 2: PaddleOCR (Cropped)").count() > 0:
             print("Track 2 Cropped zone found.")

        # Take Screenshot
        print("Taking screenshot...")
        page.screenshot(path="verification_scan_ui.png")
        print("Done.")
        browser.close()

if __name__ == "__main__":
    verify_scan_ui()
