from playwright.sync_api import Page, expect, sync_playwright
import time

def verify_deck_builder(page: Page):
    print("Navigating to Deck Builder...")
    page.goto("http://localhost:8080/decks")

    # Wait for the gallery to load (cards to appear)
    # The gallery list has ID 'gallery-list'
    print("Waiting for gallery...")
    page.wait_for_selector("#gallery-list")

    # Wait a bit for data to populate if needed, though wait_for_selector might be enough for container
    # Wait for at least one card
    try:
        page.wait_for_selector("#gallery-list .q-card", timeout=10000)
    except:
        print("No cards found or timed out. Checking if 'No cards found' message exists.")
        # If no cards, maybe empty database?

    # Check Sortable options
    print("Verifying SortableJS options...")
    # The gallery is initialized with initSortable("gallery-list", ...)
    # The element should have a _sortable property

    tolerance = page.evaluate("document.getElementById('gallery-list')._sortable.options.fallbackTolerance")
    print(f"Fallback Tolerance: {tolerance}")

    if tolerance != 3:
        raise Exception(f"Expected fallbackTolerance to be 3, got {tolerance}")

    print("Taking screenshot...")
    page.screenshot(path="verification/deck_builder.png")

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            verify_deck_builder(page)
            print("Verification successful!")
        except Exception as e:
            print(f"Verification failed: {e}")
            page.screenshot(path="verification/error.png")
            raise
        finally:
            browser.close()
