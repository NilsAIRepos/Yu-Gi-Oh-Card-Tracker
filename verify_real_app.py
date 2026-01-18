from playwright.sync_api import sync_playwright, expect

def test_browse_sets(page):
    page.goto("http://localhost:8080")

    # 1. Navigate to "Browse Sets" (if needed, assuming default route or button)
    # The app seems to have tabs or a dashboard. Let's find "Browse Sets".
    browse_btn = page.get_by_text("Browse Sets").first
    if browse_btn.is_visible():
        browse_btn.click()
    else:
        # Maybe we are already there or need to use sidebar
        pass

    # Wait for sets to load
    # Find a set card. The real app uses actual set names. Let's look for "Legend of Blue Eyes" or similar common first sets if available,
    # or just any set card.
    # We can look for the "Sort" dropdown to confirm we are on the page.
    expect(page.get_by_text("Sort", exact=True)).to_be_visible()

    # Get the first set card name
    # The set cards have a class 'q-card' and contain a label.
    # Let's just pick the first visible set name.
    first_set_card = page.locator(".q-card").first
    expect(first_set_card).to_be_visible()

    # Get the text of the set name inside
    # The structure in code: ui.label(set_info['name']).classes('text-sm font-bold truncate w-full text-white')
    set_name_el = first_set_card.locator(".text-white.font-bold").first
    set_name = set_name_el.inner_text()
    print(f"Found set: {set_name}")

    # 1. Test Click without Search
    # Click it
    set_name_el.click()

    # Expect to go to detail view
    # Header should change or "Back to Sets" button appears
    back_btn = page.get_by_text("Back to Sets")
    expect(back_btn).to_be_visible()
    print("Scenario 1 (No Search): Success")

    # Go back
    back_btn.click()
    expect(back_btn).not_to_be_visible()

    # 2. Test Click with Search
    search_input = page.get_by_placeholder("Search Sets...")
    search_input.fill(set_name)

    # Wait for filter
    # Ideally the grid refreshes.
    # We should see the set card again.
    # Let's wait a bit for debounce (300ms) + network
    page.wait_for_timeout(1000)

    filtered_card = page.get_by_text(set_name).first
    expect(filtered_card).to_be_visible()

    # Click it
    filtered_card.click()

    # Expect detail view again
    expect(back_btn).to_be_visible()
    print("Scenario 2 (With Search): Success")

    # Go back
    back_btn.click()

    # 3. Test Clearing Search Resets View
    search_input.fill("")
    page.wait_for_timeout(1000)

    # Should see more than 1 card now (assuming there are multiple sets)
    count = page.locator(".q-card").count()
    if count > 1:
        print(f"Scenario 3 (Clear Search): Success (Found {count} sets)")
    else:
        print(f"Scenario 3 (Clear Search): Warning - Only found {count} sets (might be only 1 exists)")

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            test_browse_sets(page)
        except Exception as e:
            print(f"Test failed: {e}")
            page.screenshot(path="failed_test.png")
        finally:
            browser.close()
