from playwright.sync_api import sync_playwright

def take_screenshot():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("http://localhost:8081")
        page.screenshot(path="reproduce_issue_success.png")
        browser.close()

if __name__ == "__main__":
    take_screenshot()
