from nicegui import ui
from src.core.persistence import persistence
from src.core.models import Collection

def collection_page():
    ui.label('Collection Management').classes('text-h4 q-mb-md')

    # --- Control Bar ---
    with ui.row().classes('w-full items-center gap-4 q-mb-lg'):
        # Collection Switcher
        files = persistence.list_collections()
        # Default to first file if available
        current_file = files[0] if files else None

        select = ui.select(files, value=current_file, label='Active Collection',
                  on_change=lambda e: load_collection(e.value)).classes('w-64')

        ui.space()

        # Add Card Button (Placeholder for Search/Add Modal)
        with ui.button('Add Card', icon='add').classes('bg-secondary text-dark'):
            # This would open the "Smart Data Entry" dialog
            ui.tooltip('Live Search & Add')

    # --- Content Area ---
    # We use a container that we can clear and rebuild when switching collections
    content_container = ui.column().classes('w-full')

    def load_collection(filename):
        content_container.clear()
        if not filename:
            with content_container:
                ui.label('No collection selected.').classes('text-grey')
            return

        try:
            collection: Collection = persistence.load_collection(filename)
            with content_container:
                render_collection_grid(collection)
        except Exception as e:
            with content_container:
                ui.label(f'Error loading collection: {str(e)}').classes('text-negative')

    def render_collection_grid(collection: Collection):
        ui.label(f"{collection.name} ({collection.total_cards} cards)").classes('text-h6 text-grey-4 q-mb-sm')

        if not collection.cards:
            ui.label('This collection is empty.').classes('text-italic text-grey')
            return

        with ui.grid(columns='repeat(auto-fill, minmax(250px, 1fr))').classes('w-full gap-4'):
            for card in collection.cards:
                with ui.card().classes('bg-dark border border-gray-700'):
                    # Image section
                    if card.image_url:
                        ui.image(card.image_url).classes('h-48 w-full object-contain bg-black')
                    else:
                        ui.label('No Image').classes('h-48 w-full flex items-center justify-center bg-black text-grey')

                    # Card Details
                    with ui.column().classes('p-4'):
                        ui.label(card.name).classes('text-lg font-bold leading-tight')
                        ui.label(f"{card.metadata.set_code} • {card.metadata.rarity}").classes('text-sm text-secondary')

                        with ui.row().classes('w-full justify-between items-center q-mt-sm'):
                            ui.badge(f"Qty: {card.quantity}", color='accent').classes('text-dark')
                            ui.label(f"${card.metadata.market_value:.2f}").classes('text-positive font-bold')

                        with ui.expansion('Details').classes('w-full bg-transparent text-sm text-grey-4'):
                            ui.label(f"Condition: {card.metadata.condition}")
                            ui.label(f"1st Ed: {'Yes' if card.metadata.first_edition else 'No'}")
                            ui.label(f"Location: {card.metadata.storage_location or 'N/A'}")
                            ui.label(f"Paid: ${card.metadata.purchase_price:.2f}")

    # Initial Load
    if current_file:
        load_collection(current_file)
