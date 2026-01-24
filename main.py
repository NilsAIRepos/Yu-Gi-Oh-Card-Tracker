import sys
import os
from nicegui import ui, app
from fastapi.responses import JSONResponse

# Ensure src is in the python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.core.logging_setup import setup_logging
setup_logging()

from src.ui.layout import create_layout
from src.ui.dashboard import dashboard_page
from src.ui.collection import collection_page
from src.ui.deck_builder import deck_builder_page
from src.ui.import_tools import import_tools_page
from src.ui.browse_sets import browse_sets_page
from src.ui.bulk_add import bulk_add_page
from src.ui.scan import scan_page
from src.ui.db_editor import db_editor_page
from src.ui.storage import storage_page

@ui.page('/')
def home():
    create_layout(dashboard_page)

@ui.page('/collection')
def collection():
    create_layout(collection_page)

@ui.page('/storage')
def storage():
    create_layout(storage_page)

@ui.page('/sets')
def sets():
    create_layout(browse_sets_page)

@ui.page('/decks')
def decks():
    create_layout(deck_builder_page)

@ui.page('/bulk_add')
def bulk_add():
    create_layout(bulk_add_page)

@ui.page('/import')
def import_tools():
    create_layout(import_tools_page)

@ui.page('/scan')
def scan():
    create_layout(scan_page)

@ui.page('/db_editor')
def db_editor():
    create_layout(db_editor_page)

# Serve images
os.makedirs('data/images', exist_ok=True)
os.makedirs('data/img', exist_ok=True)
os.makedirs('data/collections/storage', exist_ok=True)
app.add_static_files('/images', 'data/images')
app.add_static_files('/data/img', 'data/img') # Serve data/img for Art Match if used
app.add_static_files('/sets', 'data/sets')
app.add_static_files('/storage', 'data/collections/storage')
app.add_static_files('/debug', 'debug')

# Handle Chrome DevTools probe to prevent 404 warnings
@app.get('/.well-known/appspecific/com.chrome.devtools.json')
def chrome_devtools_probe():
    return JSONResponse(content={})

if __name__ in {"__main__", "__mp_main__"}:
    # Disable reload to prevent restart loops when writing to data/ directory (images, db)
    ui.run(title='OpenYuGi', favicon='🃏', reload=False)
