import time
from playwright.sync_api import sync_playwright, expect

def verify_scan_js():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Enable console log capture
        page = browser.new_page()

        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: console_errors.append(str(exc)))

        try:
            print("Navigating to /scan...")
            page.goto("http://localhost:8080/scan")

            # Allow some time for JS to load and execute
            page.wait_for_timeout(2000)

            # Check for specific syntax error
            syntax_error = next((err for err in console_errors if "SyntaxError" in err), None)
            if syntax_error:
                print(f"FAILURE: Found SyntaxError in console: {syntax_error}")
            else:
                print("SUCCESS: No SyntaxError found on load.")

            # Also check if startCamera is defined (it might fail if parsing failed)
            try:
                # We expect this to be defined if the script block parsed correctly.
                # If there is a syntax error in the block, the whole block might fail to execute.
                page.evaluate("typeof startCamera")
            except Exception as e:
                print(f"FAILURE: execution of JS failed, likely due to syntax error: {e}")

            # Report all errors
            if console_errors:
                print("Console Errors found:")
                for err in console_errors:
                    print(f"- {err}")

        except Exception as e:
            print(f"Test Exception: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    verify_scan_js()
