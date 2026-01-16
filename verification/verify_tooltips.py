from playwright.sync_api import sync_playwright, expect

def run(playwright):
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page()
    try:
        page.goto("http://localhost:8080/collection")
        page.wait_for_load_state("networkidle")

        gallery = page.get_by_text("Gallery", exact=True)
        gallery.wait_for(timeout=30000)

        # 1. Hover over "Consolidated" button
        btn = page.get_by_role("button", name="Consolidated")
        btn.hover()
        page.wait_for_selector(".q-tooltip")
        page.screenshot(path="verification/tooltip_consolidated.png")

        page.mouse.move(0, 0)
        page.wait_for_timeout(500)

        # 2. Open Settings
        config_btn = page.get_by_role("button", name="Configuration")
        config_btn.hover()
        page.wait_for_timeout(500)
        page.screenshot(path="verification/tooltip_config_btn.png")

        config_btn.click()
        page.wait_for_selector("text=Settings")

        update_btn = page.get_by_role("button", name="Update Card Database")
        update_btn.hover()
        page.wait_for_selector(".q-tooltip")
        page.screenshot(path="verification/tooltip_update_db.png")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        browser.close()

with sync_playwright() as playwright:
    run(playwright)
