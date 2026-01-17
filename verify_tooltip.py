from playwright.sync_api import sync_playwright

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://127.0.0.1:8081")

        # Wait for the label to appear
        label = page.get_by_text("Hover me")
        label.wait_for()

        # Hover over the label to trigger the tooltip
        label.hover()

        # Wait a bit for tooltip to appear
        page.wait_for_timeout(1000)

        # Take a screenshot
        page.screenshot(path="tooltip_verification.png")

        browser.close()

if __name__ == "__main__":
    run()
