from nicegui import ui
import asyncio

class BrowseSetsPage:
    def __init__(self):
        self.state = {
            'filtered_sets': [{'code': f'SET-{i}', 'name': f'Set {i}'} for i in range(20)],
            'search_query': '',
        }
        self.original_sets = list(self.state['filtered_sets'])

    async def open_set_detail(self, set_code):
        ui.notify(f"Opening {set_code}")
        print(f"Opening {set_code}")

    async def _handle_click(self, set_code, e):
        print(f"Click detected on set: {set_code}")
        await self.open_set_detail(set_code)

    def render_set_card(self, set_info):
        from functools import partial
        print(f"Rendering card for {set_info['code']}")
        with ui.element('div').classes('w-full p-4 border cursor-pointer').on('click', partial(self._handle_click, set_info['code'])):
            ui.label(set_info['name'])

    @ui.refreshable
    def render_content(self):
        with ui.column().classes('w-full'):
            with ui.grid(columns='3'):
                for s in self.state['filtered_sets']:
                    self.render_set_card(s)

def browse_sets_page():
    page = BrowseSetsPage()

    async def on_search(e):
        val = e.value or ""
        page.state['search_query'] = val
        page.state['filtered_sets'] = [s for s in page.original_sets if val in s['name']]
        page.render_content.refresh()

    ui.input(placeholder='Search Sets...', on_change=on_search) \
        .bind_value(page.state, 'search_query').props('debounce=300')

    page.render_content()

if __name__ in {"__main__", "__mp_main__"}:
    browse_sets_page()
    ui.run(port=8081, show=False)
