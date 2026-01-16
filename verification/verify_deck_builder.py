import time
from playwright.sync_api import sync_playwright, expect

def verify_deck_builder():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})

        try:
            print("Navigating...")
            page.goto("http://localhost:8080/decks")
            page.wait_for_load_state("networkidle")
            time.sleep(2) # Extra wait for NiceGUI connection

            # Check if we need to create a deck
            if page.get_by_text("Select or create a deck").is_visible():
                print("Creating deck...")
                # Open select
                page.locator(".q-field__control").first.click()
                time.sleep(1)
                # Click + New Deck
                page.get_by_text("+ New Deck").click()
                time.sleep(1)

                # Fill dialog
                page.get_by_label("Deck Name").fill("VerifyDeck")
                page.get_by_role("button", name="Create").click()
                time.sleep(3)

                # Wait for zones
                expect(page.get_by_text("Main Deck (0)")).to_be_visible()

            print("Verifying Layout...")
            # Check 3 zones
            expect(page.get_by_text("Main Deck")).to_be_visible()
            expect(page.get_by_text("Extra Deck")).to_be_visible()
            expect(page.get_by_text("Side Deck")).to_be_visible()

            # Check Gallery
            gallery = page.locator(".deck-builder-search-results")
            expect(gallery).to_be_visible()

            # Wait for cards
            print("Waiting for cards...")
            time.sleep(3) # Wait for initial data load timer

            # Drag and Drop
            # Find the first card (draggable)
            # Gallery -> Grid -> Card (ui.card is q-card)
            draggable_item = gallery.locator(".q-card").first

            target = page.get_by_text("Drag cards here").first

            print("Dragging...")
            draggable_item.drag_to(target)
            time.sleep(1)

            # Verify count
            if page.get_by_text("Main Deck (1)").is_visible():
                print("Success: Card added.")
            else:
                print("Warning: Card count did not update. Drag might have failed or logic issue.")

            page.screenshot(path="verification/deck_builder_view.png")
            print("Screenshot saved.")

        except Exception as e:
            print(f"Error: {e}")
            page.screenshot(path="verification/error_screenshot.png")
            raise e
        finally:
            browser.close()

if __name__ == "__main__":
    verify_deck_builder()
