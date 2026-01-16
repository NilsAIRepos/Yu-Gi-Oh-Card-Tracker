import time
import sys
import os
from playwright.sync_api import sync_playwright, expect

# Ensure data dir exists
os.makedirs('verification/screenshots', exist_ok=True)

def verify_features():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})

        try:
            print("Navigating to Deck Builder...")
            page.goto("http://localhost:8080/decks")
            page.wait_for_load_state("networkidle")
            time.sleep(2)

            # 1. Verify "Owned Only" Toggle Exists
            print("Checking Owned Only toggle...")
            toggle = page.get_by_role("switch", name="Owned Only")
            if toggle.is_visible():
                print("PASS: Owned Only toggle found.")
            else:
                print("FAIL: Owned Only toggle NOT found.")

            # 2. Verify Reference Collection has "None (All Owned)"
            print("Checking Reference Collection options...")
            # Click the dropdown (second one usually, first is Deck)
            # We can find by Label
            ref_col_selector = page.locator("label:has-text('Reference Collection')").locator("..")
            ref_col_selector.click()
            time.sleep(1)

            none_opt = page.get_by_role("option", name="None (All Owned)")
            if none_opt.is_visible():
                print("PASS: 'None (All Owned)' option found.")
                # Select it
                none_opt.click()
            else:
                print("FAIL: 'None (All Owned)' option NOT found.")
                # Close dropdown if failed
                page.keyboard.press("Escape")

            time.sleep(2)

            # 3. Verify Card Styling (Card Type presence)
            print("Verifying Card Styling...")
            # Look for a card in the gallery.
            # The new code puts card.type in a label.
            # We can check if we see "Monster" or "Spell" or "Trap" text in the card description area
            # where ATK/DEF used to be.
            # The label class has 'text-[9px]'.

            gallery = page.locator(".deck-builder-search-results")
            first_card = gallery.locator(".q-card").first

            # We expect to see text like "Normal Monster" or "Effect Monster" etc.
            # Let's just grab the text content and print it for manual check log.
            if first_card.is_visible():
                text = first_card.inner_text()
                print(f"First Card Text:\n{text}")

                if "Monster" in text or "Spell" in text or "Trap" in text:
                     print("PASS: Card Type likely displayed.")
                else:
                     print("WARNING: Card Type not explicitly found in text (might be truncated or loading).")

            # 4. Take Screenshot
            path = "verification/screenshots/deck_builder_features.png"
            page.screenshot(path=path)
            print(f"Screenshot saved to {path}")

        except Exception as e:
            print(f"Error: {e}")
            page.screenshot(path="verification/screenshots/error.png")
            raise e
        finally:
            browser.close()

if __name__ == "__main__":
    verify_features()
