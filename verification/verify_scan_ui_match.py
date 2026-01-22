import os
import time
from playwright.sync_api import sync_playwright, expect

def run(playwright):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    # Navigate to Scan Page
    try:
        page.goto("http://localhost:8080/scan")
    except Exception as e:
        print(f"Failed to load page: {e}")
        return

    # Wait for page load
    page.wait_for_load_state("networkidle")

    # Click 'Debug Lab' tab
    # Use text selector as role might be tricky with Quasar
    page.get_by_text("Debug Lab").click()

    time.sleep(1)

    # Check for Slider
    # Slider label "Matching Confidence Cutoff:"
    expect(page.get_by_text("Matching Confidence Cutoff:")).to_be_visible()

    time.sleep(2) # Allow UI to settle

    os.makedirs("verification_screenshots", exist_ok=True)
    page.screenshot(path="verification_screenshots/debug_lab_match.png", full_page=True)

    browser.close()

if __name__ == "__main__":
    with sync_playwright() as playwright:
        run(playwright)
