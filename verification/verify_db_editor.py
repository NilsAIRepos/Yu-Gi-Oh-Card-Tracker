from playwright.sync_api import sync_playwright
import time

def verify(page):
    print("Navigating to /db_editor")
    page.goto("http://localhost:8080/db_editor")

    page.wait_for_selector("text=Card Database Editor")

    try:
        page.wait_for_selector(".collection-card", timeout=10000)
    except:
        print("Timed out waiting for cards.")

    cards = page.locator(".collection-card")
    count = cards.count()
    print(f"Found {count} cards.")

    if count > 0:
        print("Clicking first card...")
        # Ensure we click the element that has the click listener (the card itself)
        cards.first.click()

        print("Waiting for dialog...")
        # Wait for something unique in the dialog, e.g. "Edit Variant Details" or "Set Code"
        try:
            page.wait_for_selector("text=Edit Variant Details", timeout=5000)
            print("Dialog opened!")
            page.screenshot(path="verification_db_editor_dialog.png")
            print("Dialog screenshot taken.")
        except Exception as e:
            print(f"Dialog did not appear: {e}")
            page.screenshot(path="verification_dialog_fail.png")
    else:
        print("No cards found.")

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_viewport_size({"width": 1280, "height": 720})
        try:
            verify(page)
        except Exception as e:
            print(f"Error: {e}")
        finally:
            browser.close()
