import time
from playwright.sync_api import sync_playwright

def verify_debug_lab_upload():
    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_viewport_size({"width": 1600, "height": 1200})

        print("1. Navigating to Home...")
        try:
            page.goto("http://localhost:8080/", timeout=10000)
            page.wait_for_load_state("networkidle")
        except Exception as e:
            print(f"Failed to load page: {e}")
            return

        print("2. Navigating to Scan Page...")
        # Try to find the link in the sidebar or menu
        try:
            # Check if we need to open a drawer (if mobile layout) but we set large viewport
            page.get_by_text("Scan Cards", exact=False).click()
        except:
            # Fallback direct navigation
            page.goto("http://localhost:8080/scan")

        page.wait_for_load_state("networkidle")
        time.sleep(1)

        print("3. Switching to Debug Lab...")
        try:
            page.get_by_text("Debug Lab").click()
            time.sleep(1)
        except Exception as e:
            print(f"Failed to switch tabs: {e}")
            page.screenshot(path="debug_lab_fail_tab.png")
            return

        print("4. Uploading Image...")
        try:
            # Locate the file input. NiceGUI's ui.upload usually has an input[type=file]
            with page.expect_file_chooser() as fc_info:
                # Trigger the file chooser by clicking the upload button/area
                # The Quasar uploader has a generic 'add' button or the drop zone
                # We can target the input directly if hidden, or click the button.
                # NiceGUI `ui.upload` -> `.q-uploader__input`
                # But Playwright `set_input_files` is easier if we find the input.
                page.locator("input[type=file]").set_input_files("dummy_card.jpg")

            print("   Image selected.")
        except Exception as e:
             print(f"   Using fallback upload method: {e}")
             # Sometimes set_input_files works without expect_file_chooser if we target the handle directly
             try:
                 page.locator("input[type=file]").set_input_files("dummy_card.jpg")
             except Exception as e2:
                 print(f"Upload failed: {e2}")
                 page.screenshot(path="debug_lab_fail_upload.png")
                 return

        print("5. Waiting for Processing...")
        # Check for status change.
        # Initial status is "Status: Stopped" or "Status: Ready to Start"
        # After upload, it should queue and then process.
        # We look for "Processing" or check if the queue count increases.

        # Give it a moment to upload
        time.sleep(2)

        # Check if "Scan Queue (1)" or similar appears
        try:
            # We assume the file is small and uploads fast.
            # Look for Log updates or Queue updates.
            # The 'Execution Log' should show "Starting scan..."

            # Wait for some log text
            print("   Waiting for logs or status update...")
            # We can poll for the text "Processing" in the status bar

            # Allow up to 10 seconds for the cycle
            for i in range(20):
                content = page.content()
                if "Processing:" in content:
                    print("   observed 'Processing' status!")
                    break
                if "Finished scan" in content:
                    print("   observed 'Finished scan' in logs!")
                    break
                if "Frame decode failed" in content:
                     print("   observed 'Frame decode failed' (Expected for dummy file)")
                     break
                time.sleep(0.5)

            # Final Screenshot
            page.screenshot(path="debug_lab_result.png")
            print("6. Verification Complete. Screenshot saved to 'debug_lab_result.png'")

        except Exception as e:
            print(f"Verification failed during wait: {e}")
            page.screenshot(path="debug_lab_fail_wait.png")

        browser.close()

if __name__ == "__main__":
    verify_debug_lab_upload()
