from playwright.sync_api import sync_playwright
import time
import os

def run_verification():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Navigate to sets page
        page.goto("http://localhost:8080/sets")
        page.wait_for_load_state("networkidle")
        time.sleep(2)  # Allow NiceGUI to hydrate

        # Verify Sliders exist in header (checking for range sliders)
        # Quasar range slider class
        sliders = page.locator(".q-slider")
        print(f"Found {sliders.count()} sliders")

        # Verify Pagination Input exists
        # We target the specific input with type number
        pagination_inputs = page.locator("input[type='number']")
        print(f"Found {pagination_inputs.count()} pagination inputs")

        # Verify Image Height
        # Check one set card image container
        # We look for the div with h-64 class or check computed style
        set_cards = page.locator(".h-64")
        print(f"Found {set_cards.count()} set card containers with h-64")

        # Take screenshot
        os.makedirs("verification", exist_ok=True)
        page.screenshot(path="verification/success.png", full_page=True)

        browser.close()

if __name__ == "__main__":
    run_verification()
