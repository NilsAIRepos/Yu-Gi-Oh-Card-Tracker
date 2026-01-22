from playwright.sync_api import sync_playwright, expect
import os
import time

def verify_tracks(page):
    print("Navigating to Scan page...")
    page.goto("http://localhost:8080/scan")

    print("Clicking Debug Lab tab...")
    page.get_by_text("Debug Lab").click()

    # Wait for content
    page.wait_for_timeout(1000)

    print("Verifying checkboxes...")
    # Check for new tracks using role checkbox
    expect(page.get_by_role("checkbox", name="Keras-OCR")).to_be_visible()
    expect(page.get_by_role("checkbox", name="MMOCR")).to_be_visible()
    expect(page.get_by_role("checkbox", name="DocTR")).to_be_visible()
    expect(page.get_by_role("checkbox", name="Tesseract")).to_be_visible()

    print("Verifying result zones...")
    # These are in expanders.
    # Using text locator
    expect(page.get_by_text("Track 3: Keras-OCR (Full Frame)")).to_be_visible()
    expect(page.get_by_text("Track 6: Tesseract (Cropped)")).to_be_visible()

    print("Taking screenshot...")
    os.makedirs("/home/jules/verification", exist_ok=True)
    page.screenshot(path="/home/jules/verification/scan_tracks_ui.png", full_page=True)

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            verify_tracks(page)
            print("Verification passed!")
        except Exception as e:
            print(f"Verification failed: {e}")
            page.screenshot(path="/home/jules/verification/scan_tracks_fail.png")
        finally:
            browser.close()
