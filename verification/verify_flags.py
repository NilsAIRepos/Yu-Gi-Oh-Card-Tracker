from playwright.sync_api import sync_playwright, expect
import time

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1920, 'height': 1080})
        page = context.new_page()

        print("Navigating to app...")
        page.goto("http://localhost:8080")

        print("Waiting for page load (Dashboard)...")
        page.wait_for_selector("text=Dashboard", timeout=10000)

        print("Navigating to Collection page...")
        # Sidebar link "COLLECTION"
        # Or the big card "Collection"
        page.get_by_text("Collection").first.click()

        print("Waiting for Gallery header...")
        page.wait_for_selector("text=Gallery", timeout=10000)

        print("Switching to Collectors view...")
        try:
             # NiceGUI buttons are sometimes divs with q-btn class.
             # Using specific text might be safer or get_by_role('button')
             page.get_by_role("button", name="Collectors").click(timeout=5000)
        except:
             print("Button by role failed, trying by text...")
             page.get_by_text("Collectors").click()

        print("Waiting for cards...")
        page.wait_for_selector(".collection-card", timeout=10000)

        # Allow images to load
        time.sleep(2)

        print("Checking for flag...")
        # Use a more generic locator first to see if any images exist
        images = page.locator(".collection-card img").all()
        print(f"Found {len(images)} images in cards.")

        # Look for flag specifically
        flag = page.locator("img[src*='flagcdn.com']").first
        if flag.count() > 0:
             expect(flag).to_be_visible(timeout=5000)
             print("Flag found.")
        else:
             print("No flag found. Check if data is loaded or if default cards have EN flags.")
             # Dump page content if no flags found
             # print(page.content())

        print("Taking screenshot...")
        page.screenshot(path="verification/verification.png")

        if page.locator(".collection-card").count() > 0:
            # Find a card with a flag if possible
            flag_card = page.locator(".collection-card").filter(has=page.locator("img[src*='flagcdn.com']")).first
            if flag_card.count() > 0:
                 flag_card.screenshot(path="verification/card_detail.png")
            else:
                 page.locator(".collection-card").first.screenshot(path="verification/card_detail.png")

        print("Done.")
        browser.close()

if __name__ == "__main__":
    run()
