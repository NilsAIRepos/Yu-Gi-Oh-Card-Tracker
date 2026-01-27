from nicegui import ui
from src.ui.scan import ScanPage

@ui.page('/')
def index():
    try:
        page = ScanPage()
        # Mocking collections for render_header to run logic
        page.collections = ['col1.json']
        page.target_collection_file = 'col1.json'

        # This calls render_header inside
        with ui.column():
            page.render_header()

        print("Render Header successful")
    except Exception as e:
        print(f"Caught expected error: {e}")

# We don't need to run the server fully, just executing the page logic in a script context if possible,
# but nicegui elements need a context.
# A simple script might fail because no client is connected.
# However, we can try instantiating the class and checking the method code or running a minimal verify.

# Let's just try to instantiate and call render_header in a dummy way?
# NiceGUI elements need a client context.
# We can use ui.run() but it blocks.
# We can checking the file content via grep/read first to confirm the line exists.
