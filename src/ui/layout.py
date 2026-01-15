from nicegui import ui
from src.ui.theme import apply_theme
from src.core.config import config_manager

def create_layout(content_function):
    """
    Wraps the content_function in the standard application layout
    (Sidebar, Header, Content Area).
    """
    # Apply theme ensuring consistent colors
    apply_theme()

    def open_settings():
        with ui.dialog() as d, ui.card().classes('w-96'):
            ui.label('Settings').classes('text-h6')

            def change_lang(e):
                if e.value != config_manager.get_language():
                    config_manager.set_language(e.value)
                    ui.notify('Language changed. Please reload or navigate to refresh data.')
                    # Reloading simply via JS since ui.navigate.reload might not be available or reliable in all versions
                    ui.run_javascript('window.location.reload()')

            ui.select(['en', 'de', 'fr', 'it', 'pt'],
                      label='Language',
                      value=config_manager.get_language(),
                      on_change=change_lang).classes('w-full')

            with ui.row().classes('w-full justify-end q-mt-md'):
                ui.button('Close', on_click=d.close).props('flat')
        d.open()

    # Define the drawer first so it's available for the toggle button
    with ui.left_drawer(value=True).classes('bg-dark text-white') as left_drawer:
        with ui.column().classes('w-full q-mt-md'):
            ui.label('Navigation').classes('text-grey-4 q-px-md text-sm uppercase font-bold')

            def nav_button(text, icon, target):
                ui.button(text, icon=icon, on_click=lambda: ui.navigate.to(target)).props('flat align=left').classes('w-full text-grey-3 hover:bg-white/10')

            nav_button('Dashboard', 'dashboard', '/')
            nav_button('Collection', 'style', '/collection')
            nav_button('Deck Builder', 'construction', '/decks')
            nav_button('Import/Scan', 'qr_code_scanner', '/import')

            ui.separator().classes('q-my-md bg-grey-8')
            ui.label('Settings').classes('text-grey-4 q-px-md text-sm uppercase font-bold')

            # Custom button for Configuration
            ui.button('Configuration', icon='settings', on_click=open_settings).props('flat align=left').classes('w-full text-grey-3 hover:bg-white/10')

    with ui.header().classes(replace='row items-center') as header:
        header.classes('bg-primary text-white')
        # Now left_drawer is definitely defined in scope
        with ui.button(on_click=lambda: left_drawer.toggle(), icon='menu').props('flat color=white'):
            pass
        ui.label('OpenYuGi').classes('text-h6 q-ml-md font-bold')

    with ui.column().classes('w-full q-pa-md items-start'):
        content_function()
