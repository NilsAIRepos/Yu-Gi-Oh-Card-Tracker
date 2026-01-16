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
    page.get_by_placeholder("Search...").fill("Schuberta the Melodious Maestra")
    page.wait_for_timeout(2000)

    # Now look for the card.
    print("Looking for card...")

    # Let's filter by Owned to be sure we find OUR card.
    print("Toggling Owned...")
    page.get_by_text("Owned").click()
    page.wait_for_timeout(2000)

    if page.get_by_text("Schuberta the Melodious Maestra").count() > 0:
        print("Card found!")
        # Click the first one.
        page.get_by_text("Schuberta the Melodious Maestra").first.click()
    else:
        print("Card NOT found after filtering.")
        page.screenshot(path="verification/not_found.png")
        raise Exception("Card not found")

    # Wait for dialog
    print("Waiting for dialog...")
    page.wait_for_selector(".q-dialog")
    page.wait_for_timeout(2000)

    # Check for "Available Sets" text
    print("Checking for 'Available Sets'...")
    try:
        # It might be down below, so we try to scroll to it.
        # We locate the element by text.
        available_sets = page.get_by_text("Available Sets")
        available_sets.scroll_into_view_if_needed()
        print("Found 'Available Sets' text.")

        # Take a screenshot specifically of the dialog or just the whole viewport now that we scrolled
        page.screenshot(path="verification/singles_view_scrolled.png")
    except Exception as e:
        print(f"Could not find 'Available Sets': {e}")
        page.screenshot(path="verification/missing_sets.png")
        raise e

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            test_singles_dialog(page)
            print("Verification script finished successfully.")
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            browser.close()
