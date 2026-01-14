import sys
import os
from nicegui import ui, app

# Ensure src is in the python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.ui.layout import create_layout
from src.ui.dashboard import dashboard_page
from src.ui.collection import collection_page
from src.ui.deck_builder import deck_builder_page
from src.ui.import_tools import import_tools_page

@ui.page('/')
def home():
    create_layout(dashboard_page)

@ui.page('/collection')
def collection():
    create_layout(collection_page)

@ui.page('/decks')
def decks():
    create_layout(deck_builder_page)

@ui.page('/import')
def import_tools():
    create_layout(import_tools_page)

# Serve images
app.add_static_files('/images', 'data/images')

if __name__ in {"__main__", "__mp_main__"}:
    ui.run(title='OpenYuGi', favicon='🃏')
