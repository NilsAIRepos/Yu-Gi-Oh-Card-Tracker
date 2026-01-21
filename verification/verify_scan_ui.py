from playwright.sync_api import Page, expect, sync_playwright
import time

def verify_scan_ui(page: Page):
    print("Navigating to Scan page...")
    page.goto("http://127.0.0.1:8080/scan")

    # Wait for page to load
    page.wait_for_load_state("networkidle")

    # 1. Switch to Debug Lab tab
    print("Switching to Debug Lab...")
    page.get_by_role("tab", name="Debug Lab").click()

    # 2. Verify new controls exist
    print("Verifying controls...")
    # Check for Pause/Resume button (initially "Start Processing" because it starts Paused)
    expect(page.get_by_role("button", name="Start Processing")).to_be_visible()

    # Check for Scan Queue
    expect(page.get_by_text("Scan Queue (0)")).to_be_visible()

    # Check for Preprocessing Strategy radio
    expect(page.get_by_text("Preprocessing Strategy:")).to_be_visible()
    expect(page.get_by_label("classic")).to_be_checked()

    # 3. Simulate an interaction (Upload a file via input if possible, or just click buttons)
    # Let's toggle tracks
    print("Toggling tracks...")
    # Use get_by_role('checkbox') to avoid matching the collapsible headers
    page.get_by_role("checkbox", name="PaddleOCR").check()

    # 4. Take Screenshot
    print("Taking screenshot...")
    page.screenshot(path="verification/scan_debug_lab.png")
    print("Screenshot saved.")

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            verify_scan_ui(page)
        except Exception as e:
            print(f"Test failed: {e}")
            page.screenshot(path="verification/scan_debug_lab_error.png")
            raise
        finally:
            browser.close()
