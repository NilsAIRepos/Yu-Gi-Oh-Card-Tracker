from nicegui import ui
from src.ui.theme import apply_theme
from src.core.config import config_manager
from src.services.ygo_api import ygo_service
from src.core.migrations import fix_legacy_set_codes

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

            ui.separator().classes('q-my-md')
            ui.label('Data Management').classes('text-subtitle2 text-grey')

            async def update_db():
                n = ui.notification('Updating Card Database...', type='info', spinner=True, timeout=None)
                try:
                    count = await ygo_service.fetch_card_database(config_manager.get_language())
                    n.dismiss()
                    ui.notify(f'Database updated. {count} cards loaded.', type='positive')
                except Exception as e:
                    n.dismiss()
                    ui.notify(f'Update failed: {e}', type='negative')

            with ui.button('Update Card Database', on_click=update_db, icon='cloud_download').classes('w-full').props('color=secondary'):
                ui.tooltip('Fetch the latest card data from the remote API')

            async def download_all_imgs():
                # Dialog for progress
                prog_dialog = ui.dialog().props('persistent')
                with prog_dialog, ui.card().classes('w-96'):
                    ui.label('Downloading All Low Res Images').classes('text-h6')
                    ui.label('This may take a while...').classes('text-sm text-grey')
                    p_bar = ui.linear_progress(0).classes('w-full q-my-md')
                    status_lbl = ui.label('Starting...')
                prog_dialog.open()

                def on_progress(val):
                    p_bar.value = val
                    status_lbl.set_text(f"{int(val * 100)}%")

                try:
                    await ygo_service.download_all_images(progress_callback=on_progress, language=config_manager.get_language())
                    prog_dialog.close()
                    ui.notify(f'All low res images downloaded.', type='positive')
                except Exception as e:
                    prog_dialog.close()
                    ui.notify(f"Error: {e}", type='negative')

            with ui.button('Download All Low Res Images', on_click=download_all_imgs, icon='download_for_offline').classes('w-full q-mt-sm').props('color=secondary'):
                ui.tooltip('Download small images for all cards (saves bandwidth)')

            async def download_all_imgs_high():
                # Dialog for progress
                prog_dialog = ui.dialog().props('persistent')
                with prog_dialog, ui.card().classes('w-96'):
                    ui.label('Downloading All High Res Images').classes('text-h6')
                    ui.label('This may take a while and use significant disk space...').classes('text-sm text-grey')
                    p_bar = ui.linear_progress(0).classes('w-full q-my-md')
                    status_lbl = ui.label('Starting...')
                prog_dialog.open()

                def on_progress(val):
                    p_bar.value = val
                    status_lbl.set_text(f"{int(val * 100)}%")

                try:
                    await ygo_service.download_all_images_high_res(progress_callback=on_progress, language=config_manager.get_language())
                    prog_dialog.close()
                    ui.notify(f'All high res images downloaded.', type='positive')
                except Exception as e:
                    prog_dialog.close()
                    ui.notify(f"Error: {e}", type='negative')

            with ui.button('Download All High Res Images', on_click=download_all_imgs_high, icon='download_for_offline').classes('w-full q-mt-sm').props('color=purple'):
                ui.tooltip('Download high-quality images for all cards (requires disk space)')

            async def update_artworks():
                # Dialog for progress
                prog_dialog = ui.dialog().props('persistent')
                with prog_dialog, ui.card().classes('w-96'):
                    ui.label('Updating Artwork Mappings').classes('text-h6')
                    ui.label('Fetching set-specific image data...').classes('text-sm text-grey')
                    p_bar = ui.linear_progress(0).classes('w-full q-my-md')
                    status_lbl = ui.label('Starting...')
                prog_dialog.open()

                def on_progress(val):
                    p_bar.value = val
                    status_lbl.set_text(f"{int(val * 100)}%")

                try:
                    count = await ygo_service.fetch_artwork_mappings(progress_callback=on_progress)
                    prog_dialog.close()
                    ui.notify(f'Mappings updated. Checked {count} cards.', type='positive')

                    # Migration
                    n_mig = ui.notification('Migrating collections...', type='info', spinner=True)
                    migrated = await ygo_service.migrate_collections()
                    n_mig.dismiss()
                    ui.notify(f'Migration complete. Updated {migrated} collections.', type='positive')

                except Exception as e:
                    prog_dialog.close()
                    ui.notify(f"Error: {e}", type='negative')

            with ui.button('Update Artwork Mappings', on_click=update_artworks, icon='image').classes('w-full q-mt-sm').props('color=accent'):
                ui.tooltip('Link specific card versions to their correct artwork')
            with ui.row().classes('w-full justify-center'):
                ui.label('Note: Artwork matching is an approximation and may not be 100% accurate.').classes('text-xs text-grey italic q-mt-xs text-center')

            async def fix_legacy_codes():
                # Dialog for progress
                prog_dialog = ui.dialog().props('persistent')
                with prog_dialog, ui.card().classes('w-96'):
                    ui.label('Fixing Legacy Set Codes').classes('text-h6')
                    ui.label('Scanning collections...').classes('text-sm text-grey')
                    ui.spinner().classes('self-center q-my-md')
                prog_dialog.open()

                try:
                    count = await fix_legacy_set_codes()
                    prog_dialog.close()
                    ui.notify(f'Fixed {count} cards.', type='positive')
                except Exception as e:
                    prog_dialog.close()
                    ui.notify(f"Error: {e}", type='negative')

            with ui.button('Fix Legacy Set Codes', on_click=fix_legacy_codes, icon='build').classes('w-full q-mt-sm').props('color=warning'):
                ui.tooltip('Update old set codes in your collection to the new format')

            with ui.row().classes('w-full justify-end q-mt-md'):
                with ui.button('Close', on_click=d.close).props('flat'):
                    ui.tooltip('Close settings')
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
            with ui.button('Configuration', icon='settings', on_click=open_settings).props('flat align=left').classes('w-full text-grey-3 hover:bg-white/10'):
                ui.tooltip('Open application settings and database management')

    with ui.header().classes(replace='row items-center') as header:
        header.classes('bg-primary text-white')
        # Now left_drawer is definitely defined in scope
        with ui.button(on_click=lambda: left_drawer.toggle(), icon='menu').props('flat color=white'):
            pass
        ui.label('OpenYuGi').classes('text-h6 q-ml-md font-bold')

    with ui.column().classes('w-full q-pa-md items-start'):
        content_function()
