from playwright.sync_api import sync_playwright, expect
import time

def run(playwright):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1920, 'height': 1080})
    page = context.new_page()

    # 1. Open Deck Builder
    print("Navigating to Deck Builder...")
    page.goto("http://localhost:8080/decks")
    # Wait for the header text "Deck Builder"
    page.wait_for_selector("text=Deck Builder")

    # 2. Create New Deck
    print("Creating new deck...")

    # Debug: Screenshot before click
    page.screenshot(path="verification/debug_before_click.png")

    # Find button with add_circle icon
    # Try filtering by icon text content more loosely
    new_deck_btn = page.locator("button").filter(has=page.locator("i", has_text="add_circle"))

    if new_deck_btn.count() > 0:
        print("Found button via icon.")
        new_deck_btn.first.click()
    else:
        # Fallback: Try to find by tooltip if visible (unlikely without hover)
        # Or try the select
        print("Button not found via icon. Trying select...")
        # Open select
        # Quasar select trigger
        page.locator(".q-select").first.click()
        page.get_by_text("+ New Deck").click()

    # Fill dialog
    # Wait for dialog
    try:
        page.wait_for_selector(".q-dialog", timeout=5000)
    except:
        print("Dialog did not appear. Dumping page content.")
        page.screenshot(path="verification/debug_dialog_fail.png")
        raise
    # Find input inside dialog. There is usually one input for deck name.
    # We can try to type into it.
    # NiceGUI input usually has a class 'q-field__native'
    page.locator(".q-dialog input.q-field__native").fill("VerifyDeck")
    # Click Create (in dialog)
    page.get_by_role("button", name="Create").click()

    time.sleep(1) # Wait for load

    # 3. Add Cards to trigger warnings
    # Find a card in gallery.
    print("Adding cards...")
    # Click the first card in the gallery list.
    # The gallery items are cards with click handlers.
    # Selector: .deck-builder-search-results .q-card
    # Let's try to click the first image in gallery-list
    first_card = page.locator("#gallery-list > div").first
    first_card.click()

    # Wait for dialog
    page.wait_for_selector("text=Add to Deck")

    # 4. Check Buttons (Step 5 of plan)
    # Check if "Add to Main" exists.
    # Depending on the random card loaded, it might be Main or Extra.
    # We can check visibility.
    print("Checking buttons...")
    has_main = page.get_by_role("button", name="Add to Main").is_visible()
    has_extra = page.get_by_role("button", name="Add to Extra").is_visible()
    print(f"Buttons visible: Main={has_main}, Extra={has_extra}")

    # Add 4 copies to Main (or Extra if Main not available)
    target_btn = page.get_by_role("button", name="Add to Main")
    if not has_main:
        target_btn = page.get_by_role("button", name="Add to Extra")

    # Set quantity to 3 (max in input is 3)
    # Actually the input has max=3. But we can click Add multiple times.
    # Let's click Add 4 times.
    # Wait, clicking Add closes the dialog.
    # So we need to reopen it 4 times.

    # Close dialog first (if open) - wait, clicking add closes it.
    target_btn.click()
    time.sleep(0.5)

    # Reopen and add 3 more times
    for _ in range(3):
        first_card.click()
        page.wait_for_selector("text=Add to Deck")
        if not has_main:
             page.get_by_role("button", name="Add to Extra").click()
        else:
             page.get_by_role("button", name="Add to Main").click()
        time.sleep(0.5)

    # 5. Check Warnings
    print("Checking for warning icons...")
    # Look for warning icon in the deck area.
    # The deck area cards have an icon 'warning'.
    # NiceGUI renders icons as <i class="q-icon ...">warning</i>
    warning_icon = page.locator("i:text('warning')").first
    expect(warning_icon).to_be_visible()

    # Hover to see tooltip
    warning_icon.hover()
    time.sleep(0.5)

    page.screenshot(path="verification/warnings_shown.png")
    print("Screenshot saved: warnings_shown.png")

    # 6. Change Settings to Strict
    print("Changing settings to Strict...")
    # Click menu button to open drawer (if closed, but it's open by default on desktop)
    # Click "Configuration" button in drawer
    page.get_by_role("button", name="Configuration").click()

    # Wait for dialog
    page.wait_for_selector("text=Deck Builder Warnings")

    # Select Strict
    # NiceGUI select: click label, then click option.
    # The label is "Deck Builder Warnings".
    # Find the q-select that corresponds to it.
    # We can click the arrow_drop_down icon near it?
    # Or click the text.
    page.get_by_text("Warning (Default)").click() # Current value
    page.get_by_text("Strict + Warning").click() # New option

    # Close settings
    page.get_by_role("button", name="Close").click()
    time.sleep(0.5)

    # 7. Try to Save
    print("Attempting to save invalid deck...")
    # Click Save As (floppy disk with pen) or just trigger auto-save by moving something?
    # Or "Save Deck As" button in header.
    # Or just "Save" isn't a button, it's "Save Deck As" or auto.
    # But `save_current_deck` is called on changes.
    # We just added cards, so it tried to save.
    # But we changed settings AFTER adding cards.
    # So we need to trigger a save again.
    # Let's move a card or add another one.
    first_card.click()
    if not has_main:
         page.get_by_role("button", name="Add to Extra").click()
    else:
         page.get_by_role("button", name="Add to Main").click()

    # Expect notification
    # NiceGUI notifications are usually div with class q-notification
    print("Checking for error notification...")
    page.wait_for_selector(".q-notification")

    page.screenshot(path="verification/strict_save_error.png")
    print("Screenshot saved: strict_save_error.png")

with sync_playwright() as p:
    run(p)
