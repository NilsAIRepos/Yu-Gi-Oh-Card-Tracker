
from playwright.sync_api import sync_playwright, expect

def run(playwright):
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://localhost:8888")

    # Click button to open dialog
    page.get_by_text("Open Dialog").click()

    # Wait for dialog content
    expect(page.get_by_text("Resolve Ambiguities")).to_be_visible()

    # Verify the text format
    # "Orig: LOB | DE | Common | Near Mint | 1st"
    # Note: row.set_rarity="Common", set_condition="Near Mint", first_edition=True -> "1st"
    expected_text = "Orig: LOB | DE | Common | Near Mint | 1st"
    expect(page.get_by_text(expected_text)).to_be_visible()

    # Take screenshot
    page.screenshot(path="verification/ambiguity_dialog.png")

    browser.close()

with sync_playwright() as playwright:
    run(playwright)
