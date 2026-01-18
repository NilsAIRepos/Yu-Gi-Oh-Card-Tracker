from playwright.sync_api import sync_playwright, expect

def test_browse_sets(page):
    page.goto("http://localhost:8081")

    # 1. Test Click without Search
    set_0 = page.get_by_text("Set 0")
    expect(set_0).to_be_visible()
    set_0.click()
    notification = page.get_by_text("Opening SET-0")
    expect(notification).to_be_visible()
    print("Scenario 1 (No Search): Success")

    # 2. Test Click with Search
    search_input = page.get_by_placeholder("Search Sets...")
    search_input.fill("Set 10")
    expect(set_0).not_to_be_visible()
    set_10 = page.get_by_text("Set 10")
    expect(set_10).to_be_visible()
    set_10.click()
    notification_10 = page.get_by_text("Opening SET-10")
    expect(notification_10).to_be_visible(timeout=3000)
    print("Scenario 2 (With Search): Success")

    # 3. Test Clearing Search Resets View
    search_input.fill("")
    expect(set_0).to_be_visible()
    print("Scenario 3 (Clear Search): Success")

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            test_browse_sets(page)
        except Exception as e:
            print(f"Test failed: {e}")
        finally:
            browser.close()
