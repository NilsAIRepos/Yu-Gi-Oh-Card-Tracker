from nicegui import ui

def deck_builder_page():
    ui.label('Deck Builder').classes('text-h4 q-mb-md')

    with ui.row().classes('w-full h-[calc(100vh-200px)] gap-4'):
        # Left: Card Search / Collection
        with ui.column().classes('w-1/3 h-full bg-dark border border-gray-700 p-4 rounded'):
            ui.input(placeholder='Search cards...').classes('w-full').props('outlined dark dense')

            with ui.scroll_area().classes('w-full h-full q-mt-md border border-gray-800 rounded p-2'):
                 for i in range(10):
                     with ui.row().classes('w-full items-center p-2 hover:bg-white/5 rounded cursor-pointer'):
                         ui.icon('image', size='2rem').classes('text-grey')
                         with ui.column().classes('gap-0'):
                             ui.label(f'Sample Card {i+1}').classes('font-bold')
                             ui.label('ATK/2500 DEF/2100').classes('text-xs text-grey')

        # Right: Deck Construct Area (Main, Side, Extra)
        with ui.column().classes('w-2/3 h-full gap-4'):
             deck_area('Main Deck (40-60)', 'h-2/3')
             with ui.row().classes('w-full h-1/3 gap-4'):
                 deck_area('Extra Deck (0-15)', 'w-1/2 h-full')
                 deck_area('Side Deck (0-15)', 'w-1/2 h-full')

def deck_area(title, size_classes):
    with ui.column().classes(f"{size_classes} bg-dark border border-gray-700 p-4 rounded"):
        ui.label(title).classes('font-bold text-grey-4 q-mb-sm')
        with ui.card().classes('w-full h-full bg-black/20 border-2 border-dashed border-gray-600 flex items-center justify-center'):
            ui.label('Drag Cards Here').classes('text-grey-6')
