from nicegui import ui

def dashboard_page():
    ui.label('Financial Dashboard').classes('text-h4 q-mb-md')

    # Placeholder Metrics
    with ui.row().classes('w-full gap-4 q-mb-lg'):
        metric_card('Total Portfolio Value', '$1,245.50', 'text-positive', 'trending_up')
        metric_card('Total Investment', '$850.00', 'text-info', 'attach_money')
        metric_card('Total Cards', '1,024', 'text-accent', 'style')
        metric_card('Spread', '+46.5%', 'text-positive', 'percent')

    # Placeholder Charts/Widgets
    with ui.grid(columns=2).classes('w-full gap-4'):
        with ui.card().classes('bg-dark border border-gray-700 h-64 flex items-center justify-center'):
            ui.icon('bar_chart', size='4rem').classes('text-grey-8')
            ui.label('Value History Chart (Placeholder)').classes('text-grey')

        with ui.card().classes('bg-dark border border-gray-700 h-64 flex items-center justify-center'):
            ui.icon('pie_chart', size='4rem').classes('text-grey-8')
            ui.label('Rarity Distribution (Placeholder)').classes('text-grey')

def metric_card(label, value, color_class, icon):
    with ui.card().classes('flex-1 bg-dark border border-gray-700 p-4 items-center flex-row gap-4'):
        ui.icon(icon, size='3rem').classes(color_class)
        with ui.column().classes('gap-0'):
            ui.label(label).classes('text-grey-4 text-sm uppercase font-bold')
            ui.label(value).classes(f"text-2xl font-bold {color_class}")
