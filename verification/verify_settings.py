from playwright.sync_api import sync_playwright, expect

def run(playwright):
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("http://localhost:8080/")

    page.wait_for_selector("body")

    # Click Configuration to open settings
    page.get_by_role("button", name="Configuration").click()

    # Wait for dialog.
    expect(page.locator(".text-h6").filter(has_text="Settings")).to_be_visible()

    # Verify "Update All Languages DB" button exists
    expect(page.get_by_role("button", name="Update All Languages DB")).to_be_visible()

    # Open Language dropdown
    language_field = page.locator(".q-field").filter(has_text="Language")
    language_field.click()

    # Verify 'es' option IS NOT visible
    expect(page.get_by_role("option", name="es")).not_to_be_visible()

    # Verify 'en' option IS visible
    expect(page.get_by_role("option", name="en")).to_be_visible()

    # Take screenshot
    page.screenshot(path="verification/settings_ui.png")

    browser.close()

with sync_playwright() as playwright:
    run(playwright)
