import cv2
import numpy as np
import os
import time
from playwright.sync_api import sync_playwright, expect

def create_dummy_image():
    # Create a black image
    img = np.zeros((1080, 1920, 3), dtype=np.uint8)
    # Draw a "Card" rectangle (white)
    cv2.rectangle(img, (800, 400), (800+480, 400+700), (255, 255, 255), -1)
    # Add text "LOB-EN001" to the "card"
    cv2.putText(img, "LOB-EN001", (900, 1000), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
    cv2.imwrite("verification_dummy.png", img)

def test_scan_ui(page):
    print("Navigating to /scan...")
    page.goto("http://localhost:8080/scan")

    # Wait for tabs
    print("Waiting for Live Scan tab...")
    expect(page.get_by_text("Live Scan")).to_be_visible()

    # Switch to Debug Lab
    print("Switching to Debug Lab...")
    page.get_by_text("Debug Lab").click()
    expect(page.get_by_text("1. Input Source")).to_be_visible()

    # Upload Image
    print("Uploading image...")
    file_path = os.path.abspath("verification_dummy.png")
    page.locator("input[type='file']").set_input_files(file_path)

    # Wait for results
    print("Waiting for Track 2 results...")
    expect(page.get_by_text("Track 2 (Full Frame):")).to_be_visible(timeout=15000)

    # Verify content
    print("Verifying content...")
    expect(page.get_by_text("LOB-EN001").first).to_be_visible()

    # Take screenshot
    print("Taking screenshot...")
    page.screenshot(path="verification_frontend.png", full_page=True)
    print("Done.")

if __name__ == "__main__":
    create_dummy_image()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})
        try:
            test_scan_ui(page)
        except Exception as e:
            print(f"Error: {e}")
            page.screenshot(path="verification_error.png")
            raise e
        finally:
            browser.close()
