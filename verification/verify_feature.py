from playwright.sync_api import sync_playwright
import time
import os

def verify(page):
    print("Navigating to DB Editor...")
    page.goto("http://localhost:8080/db_editor")

    # Wait for the "Consolidated" button
    page.wait_for_selector('button:has-text("Consolidated")', timeout=10000)
    page.click('button:has-text("Consolidated")')

    try:
        page.wait_for_selector('.collection-card', timeout=5000)
    except:
        pass

    cards = page.query_selector_all('.collection-card')
    if not cards:
        print("No cards.")
        return

    print(f"Found {len(cards)} cards. Clicking...")
    cards[0].click(force=True)

    # Wait for dialog content to be visible
    print("Waiting for visible header...")
    # Use visible=true pseudo-class
    header = page.locator('div:text("Consolidated View:") >> visible=true')
    header.wait_for(timeout=5000)

    # Look for "Target Art Style" dropdown label
    print("Looking for 'Target Art Style'...")
    dropdown_label = page.locator('.q-field__label:has-text("Target Art Style") >> visible=true')
    dropdown_label.wait_for()

    # Take screenshot of dialog
    page.screenshot(path="verification/verification_dialog.png")
    print("Dialog screenshot taken.")

    # Click the dropdown to open options
    print("Opening dropdown...")
    # Sometimes clicking the label works, sometimes need the field control
    dropdown_label.click(force=True)

    # Wait for options. NiceGUI (Quasar) uses a portal for menu.
    # Look for "+ New Artstyle"
    print("Waiting for '+ New Artstyle' option...")
    # The menu is usually in a portal, so checking visible=true globally
    option = page.locator('.q-item__label:has-text("+ New Artstyle") >> visible=true')
    option.wait_for(timeout=5000)

    # Take screenshot of options
    page.screenshot(path="verification/verification_options.png")
    print("Options screenshot taken.")

    # Click it
    print("Clicking '+ New Artstyle'...")
    option.click()

    # Wait for New Artstyle Dialog
    print("Waiting for 'Add New Artstyle' dialog...")
    new_dialog_header = page.locator('div:text("Add New Artstyle") >> visible=true')
    new_dialog_header.wait_for(timeout=5000)

    # Screenshot
    page.screenshot(path="verification/verification_new_art_dialog.png")
    print("New Art Dialog screenshot taken.")

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            verify(page)
        except Exception as e:
            print(f"Verification failed: {e}")
            page.screenshot(path="verification/verification_failure.png")
        finally:
            browser.close()
