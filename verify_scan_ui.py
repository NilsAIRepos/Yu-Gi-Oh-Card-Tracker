from playwright.sync_api import sync_playwright, expect

def verify_scan_ui():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            # Navigate to the scan page
            page.goto("http://localhost:8080/scan")

            # Wait for the page to load
            expect(page.get_by_text("Card Scanner")).to_be_visible()

            # Check for the renamed button "Force Scan"
            # It might be in the debug drawer which is hidden by default.
            # I need to toggle debug mode first.

            # Find the "Debug Mode" switch and toggle it
            # NiceGUI switches usually have a label text next to them
            debug_switch = page.get_by_text("Debug Mode")
            debug_switch.click()

            # Now the drawer should be visible (or animating in).
            # Wait a moment for animation if needed, or just look for the button.

            # The button text should be "Force Scan"
            force_scan_btn = page.get_by_role("button", name="Force Scan")
            expect(force_scan_btn).to_be_visible()

            # Take a screenshot of the page with the debug drawer open
            page.screenshot(path="verification_scan_ui.png")
            print("Verification successful: Force Scan button found.")

        except Exception as e:
            print(f"Verification failed: {e}")
            page.screenshot(path="verification_scan_error.png")
        finally:
            browser.close()

if __name__ == "__main__":
    verify_scan_ui()
