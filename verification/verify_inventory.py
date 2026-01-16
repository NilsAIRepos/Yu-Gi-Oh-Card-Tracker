from playwright.sync_api import sync_playwright, expect
import time

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = context.new_page()

        print("Navigating to Collection page...")
        page.goto("http://localhost:8080/collection")

        print("Waiting for cards to load...")
        try:
            page.wait_for_selector('.collection-card', timeout=60000)
        except:
            print("Timed out waiting for cards. Taking screenshot of error/loading state.")
            page.screenshot(path="verification/loading_timeout.png")
            browser.close()
            return

        print("Switching to Collectors View...")
        collectors_btn = page.get_by_role("button", name="Collectors")
        collectors_btn.click()

        page.wait_for_timeout(2000)

        print("Opening a card...")
        first_card = page.locator('.collection-card').first
        first_card.click()

        print("Waiting for dialog...")
        page.wait_for_selector('.q-dialog')

        print("Checking for Manage Inventory...")
        expect(page.get_by_text("Manage Inventory")).to_be_visible()

        # Use more specific locators to avoid ambiguity
        # "Set Name" might also be in the static text "Set: ..." so use label if possible
        # NiceGUI ui.select labels usually appear as text.

        # Verify Sections exist by looking for the input labels
        # We can look for the QSelect elements which contain these labels

        # Just check if "SET" and "ADD" buttons are there, that confirms the new section is rendered
        expect(page.get_by_role("button", name="SET")).to_be_visible()
        expect(page.get_by_role("button", name="ADD")).to_be_visible()

        # Check for inputs by label text but being careful
        # ui.select renders a label inside.
        # get_by_text("Language", exact=True) should match the label of the dropdown
        # But if there is "Language: EN" text elsewhere it might clash if not exact.

        print("Taking screenshot...")
        page.screenshot(path="verification/inventory_dialog.png")

        browser.close()

if __name__ == "__main__":
    run()
