from playwright.sync_api import sync_playwright, expect

def run_verification():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # 1. Navigate
        print("Navigating to Deck Builder...")
        page.goto("http://localhost:8080/decks")
        expect(page.get_by_text("Deck Builder")).to_be_visible()

        # 2. Select Reference Collection
        print("Selecting Test Collection...")
        # NiceGUI select creates a div with label. Clicking it opens menu.
        # We target the label "Reference Collection" which is the label of the select
        # The select value is in a sibling or child.
        # Quasar select is tricky.
        # Strategy: Click the dropdown arrow/field associated with label.
        # page.get_by_label("Reference Collection") might locate the input or container.
        page.get_by_text("Reference Collection").click()
        # Wait for menu options
        page.get_by_text("Test Collection").click()

        # 3. Create New Deck
        print("Creating New Deck...")
        page.get_by_text("Current Deck").click()
        page.get_by_text("+ New Deck").click()

        expect(page.get_by_text("Create New Deck")).to_be_visible()
        page.get_by_label("Deck Name").fill("Test Deck")
        page.get_by_role("button", name="Create").click()

        # 4. Search for Card
        print("Searching...")
        page.get_by_placeholder("Search cards...").fill("Blue-Eyes White Dragon")
        # Wait for debounce and search result
        # Expect card with text "Blue-Eyes White Dragon" in the library
        # The library is in `gallery-list`.
        expect(page.locator("#gallery-list").get_by_text("Blue-Eyes White Dragon").first).to_be_visible()

        # 5. Add 3 copies to Main Deck
        print("Adding 3 copies to Main...")

        # Function to add card
        def add_to_main():
            # Click the first card in gallery
            page.locator("#gallery-list").locator(".q-card").first.click()
            # Wait for dialog
            expect(page.get_by_text("Collection Status")).to_be_visible()
            # Click Add to Main
            page.get_by_role("button", name="Add to Main").click()
            # Wait for dialog to close
            expect(page.get_by_text("Collection Status")).not_to_be_visible()

        add_to_main()
        add_to_main()
        add_to_main()

        # 6. Verify Visuals in Main Deck
        print("Verifying Main Deck...")
        main_deck = page.locator("#deck-main")
        cards = main_deck.locator(".q-card")

        # Expect 3 cards
        expect(cards).to_have_count(3)

        # Card 1 & 2 should be colored (opacity-100)
        # Card 3 should be grayscale (opacity-50 grayscale)

        # We check classes.
        # NiceGUI/Quasar classes are space separated.
        # We can get class attribute.

        classes1 = cards.nth(0).get_attribute("class")
        classes2 = cards.nth(1).get_attribute("class")
        classes3 = cards.nth(2).get_attribute("class")

        print(f"Card 1 classes: {classes1}")
        print(f"Card 2 classes: {classes2}")
        print(f"Card 3 classes: {classes3}")

        if "opacity-100" not in classes1: raise Exception("Card 1 should be colored")
        if "opacity-100" not in classes2: raise Exception("Card 2 should be colored")
        if "opacity-50" not in classes3 or "grayscale" not in classes3: raise Exception("Card 3 should be grayscale")

        # 7. Add to Side Deck
        print("Adding to Side Deck...")
        page.locator("#gallery-list").locator(".q-card").first.click()
        expect(page.get_by_text("Collection Status")).to_be_visible()
        page.get_by_role("button", name="Add to Side").click()
        expect(page.get_by_text("Collection Status")).not_to_be_visible()

        # 8. Verify Side Deck
        print("Verifying Side Deck...")
        side_deck = page.locator("#deck-side")
        side_cards = side_deck.locator(".q-card")

        expect(side_cards).to_have_count(1)
        side_classes = side_cards.nth(0).get_attribute("class")
        print(f"Side Card classes: {side_classes}")

        if "opacity-50" not in side_classes or "grayscale" not in side_classes:
            raise Exception("Side Deck card should be grayscale because Main Deck used all owned copies")

        # 9. Screenshot
        print("Taking screenshot...")
        page.screenshot(path="verification.png")
        print("Success!")

        browser.close()

if __name__ == "__main__":
    run_verification()
