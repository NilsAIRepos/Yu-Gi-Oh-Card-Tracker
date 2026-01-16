import time
from playwright.sync_api import sync_playwright, expect

def test_singles_dialog(page):
    print("Navigating to collection page...")
    page.goto("http://localhost:8080/collection")
    page.wait_for_timeout(3000)

    print("Selecting collection...")
    # It seems test_verification is selected by default in my environment or accidentally.
    # But just in case, let's proceed.

    print("Clicking Collectors view...")
    # Wait for the button to be available
    page.wait_for_selector("button:has-text('Collectors')")
    page.get_by_role("button", name="Collectors").click()
    page.wait_for_timeout(2000)

    print("Searching for card...")
    page.get_by_placeholder("Search...").fill("Blue-Eyes White Dragon")
    page.wait_for_timeout(2000)

    # Now look for the card.
    print("Looking for card...")

    # In Collectors view, we might see multiple entries if I owned multiple variants, but I only own one in the test file.
    # However, since I am not filtering by 'Owned', I might see many Blue-Eyes entries (one for each set).
    # I should try to find the one that says "Owned: 1" or filter by Owned.

    # Let's filter by Owned to be sure we find OUR card.
    print("Toggling Owned...")
    # The switch has text "Owned".
    # page.get_by_role("switch", name="Owned").click() # Not sure if role is switch.
    # nicegui switch is q-toggle.
    page.get_by_text("Owned").click()
    page.wait_for_timeout(2000)

    if page.get_by_text("Blue-Eyes White Dragon").count() > 0:
        print("Card found!")
        # Click the first one.
        page.get_by_text("Blue-Eyes White Dragon").first.click()
    else:
        print("Card NOT found after filtering.")
        # Debug screenshot
        page.screenshot(path="verification/not_found.png")
        raise Exception("Card not found")

    # Wait for dialog
    print("Waiting for dialog...")
    page.wait_for_selector(".q-dialog")
    page.wait_for_timeout(2000)

    # Screenshot
    print("Taking screenshot...")
    page.screenshot(path="verification/singles_view.png")

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            test_singles_dialog(page)
            print("Verification script finished successfully.")
        except Exception as e:
            print(f"Error: {e}")
            page.screenshot(path="verification/error.png")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()
