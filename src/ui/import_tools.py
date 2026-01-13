from nicegui import ui

def import_tools_page():
    ui.label('Import & Scan').classes('text-h4 q-mb-md')

    with ui.grid(columns=2).classes('w-full gap-6'):
        # Camera Scan
        with ui.card().classes('bg-dark border border-gray-700 p-6'):
            ui.label('Camera Scanner').classes('text-xl font-bold q-mb-md')
            ui.label('Use your webcam to scan cards directly.').classes('text-grey q-mb-lg')

            with ui.card().classes('w-full h-64 bg-black flex items-center justify-center q-mb-lg'):
                ui.icon('videocam', size='4rem').classes('text-grey-8')

            ui.button('Start Camera', icon='camera_alt').classes('w-full bg-accent text-dark')

        # Receipt / Text Import
        with ui.card().classes('bg-dark border border-gray-700 p-6'):
            ui.label('Bulk Text Import').classes('text-xl font-bold q-mb-md')
            ui.label('Paste a list of cards or receipt text below.').classes('text-grey q-mb-lg')

            ui.textarea(placeholder='3x Blue-Eyes White Dragon\n1x Dark Magician (LOB)').classes('w-full q-mb-lg').props('outlined dark rows=8')

            ui.button('Process Text', icon='article').classes('w-full bg-secondary text-dark')

    # PDF Upload
    with ui.card().classes('w-full bg-dark border border-gray-700 p-6 q-mt-6'):
        ui.label('Import Cardmarket Invoice (PDF)').classes('text-xl font-bold q-mb-md')
        ui.upload(label='Drop PDF here', auto_upload=True).props('dark accept=.pdf').classes('w-full')
