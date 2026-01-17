from nicegui import ui

ui.add_head_html('<script src="https://cdn.tailwindcss.com"></script>')

with ui.row().classes('p-20'):
    ui.label('Hover me').classes('text-2xl border p-4')
    with ui.tooltip().classes('bg-transparent shadow-none border-none p-0 overflow-visible z-[9999] max-w-none'):
            # Increased height to trigger potential width constraints
            ui.image('https://images.ygoprodeck.com/images/cards/33508719.jpg').classes('w-auto h-[80vh] min-w-[400px] object-contain rounded-lg shadow-2xl')

ui.run(port=8081)
