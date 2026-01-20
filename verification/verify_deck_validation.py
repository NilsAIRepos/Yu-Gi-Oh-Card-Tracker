from playwright.sync_api import sync_playwright, expect
import time

def run(playwright):
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(viewport={'width': 1920, 'height': 1080})
    page = context.new_page()

    print("Navigating to Deck Builder...")
    page.goto("http://localhost:8080/decks")
    page.wait_for_selector("text=Deck Builder")

    print("Creating new deck...")
    new_deck_btn = page.locator("button").filter(has=page.locator("i", has_text="add_circle"))
    if new_deck_btn.count() > 0:
        new_deck_btn.first.click()
    else:
        page.locator(".q-select").first.click()
        page.get_by_text("+ New Deck").click()

    try:
        page.wait_for_selector(".q-dialog", timeout=5000)
    except:
        print("Dialog did not appear.")
        raise

    page.locator(".q-dialog input.q-field__native").fill("VerifyDeck")
    page.get_by_role("button", name="Create").click()
    time.sleep(1)

    print("Adding cards...")
    first_card = page.locator("#gallery-list > div").first
    first_card.click()
    page.wait_for_selector("text=Add to Deck")

    print("Checking buttons...")
    has_main = page.get_by_role("button", name="Add to Main").is_visible()

    target_btn = page.get_by_role("button", name="Add to Main")
    if not has_main:
        target_btn = page.get_by_role("button", name="Add to Extra")

    target_btn.click()
    time.sleep(0.5)

    for _ in range(3):
        first_card.click()
        page.wait_for_selector("text=Add to Deck")
        if not has_main:
             page.get_by_role("button", name="Add to Extra").click()
        else:
             page.get_by_role("button", name="Add to Main").click()
        time.sleep(0.5)

    print("Checking for warning icons...")
    warning_icon = page.locator("i:text('warning')").first
    expect(warning_icon).to_be_visible()
    warning_icon.hover()
    time.sleep(0.5)
    page.screenshot(path="verification/warnings_shown.png")

    print("Changing settings to Strict...")
    page.get_by_role("button", name="Configuration").click()
    page.wait_for_selector("text=Deck Builder Warnings")

    print("Opening dropdown...")
    page.locator(".q-field").filter(has_text="Deck Builder Warnings").locator(".q-field__native").click()

    print("Selecting Strict option...")
    page.get_by_role("option", name="Strict + Warning").click()

    # Close specific settings dialog
    page.locator(".q-card").filter(has_text="Settings").get_by_role("button", name="Close").click()
    time.sleep(0.5)

    print("Attempting to save invalid deck...")
    first_card.click()
    if not has_main:
         page.get_by_role("button", name="Add to Extra").click()
    else:
         page.get_by_role("button", name="Add to Main").click()

    print("Checking for error notification...")
    page.wait_for_selector(".q-notification")
    page.screenshot(path="verification/strict_save_error.png")
    print("Verification success!")

with sync_playwright() as p:
    run(p)
