from nicegui import ui, events
import base64
import logging
import asyncio
import json
from typing import Optional, Callable
from src.services.scanner_service import scanner_service
from src.services.scan_logger import scan_logger
from src.services.ygo_api import ygo_service
from src.ui.components.single_card_view import SingleCardView
from src.services.collection_editor import CollectionEditor
from src.core.models import Collection
from src.core.persistence import persistence

logger = logging.getLogger(__name__)

SCANNER_JS = r"""
<script>
window.ScannerController = {
    stream: null,
    scanning: false,
    cooldown: false,
    lastFrame: null,
    stableCount: 0,
    threshold: 15,
    stabilityFrames: 10,
    videoEl: null,
    canvasEl: null,
    containerEl: null,
    overlayEl: null,
    debugEl: null,

    init: function(videoId, canvasId, containerId, overlayId, debugId) {
        this.videoEl = document.getElementById(videoId);
        this.canvasEl = document.getElementById(canvasId);
        this.containerEl = document.getElementById(containerId);
        this.overlayEl = document.getElementById(overlayId);
        this.debugEl = document.getElementById(debugId);
    },

    start: async function() {
        if (!this.videoEl) return;
        try {
            this.stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: 'environment', width: { ideal: 1920 }, height: { ideal: 1080 } }
            });
            this.videoEl.srcObject = this.stream;
            this.videoEl.play();
            this.scanning = true;
            this.cooldown = false;
            this.loop();
            this.updateOverlay('active');
        } catch (e) {
            console.error("Camera error", e);
            alert("Camera error: " + e);
        }
    },

    stop: function() {
        this.scanning = false;
        if (this.stream) {
            this.stream.getTracks().forEach(t => t.stop());
            this.stream = null;
        }
        if (this.videoEl) this.videoEl.srcObject = null;
        this.updateOverlay('inactive');
    },

    resume: function() {
        this.cooldown = false;
        this.scanning = true;
        this.stableCount = 0;
        this.updateOverlay('active');
        this.loop();
    },

    updateOverlay: function(state) {
        if (!this.overlayEl) return;
        if (state === 'processing') {
            this.overlayEl.innerHTML = '<div class="animate-spin rounded-full h-12 w-12 border-b-2 border-white mb-2"></div><span class="text-lg font-bold text-white">Processing...</span>';
            this.overlayEl.className = "absolute inset-0 bg-black/60 flex flex-col items-center justify-center z-10";
        } else if (state === 'active') {
            // Pulse dot
            this.overlayEl.innerHTML = '<div class="absolute top-2 right-2 bg-red-500 w-3 h-3 rounded-full animate-pulse"></div>';
            this.overlayEl.className = "absolute inset-0 pointer-events-none";
        } else {
            this.overlayEl.innerHTML = '<span class="text-gray-400">Camera Off</span>';
            this.overlayEl.className = "absolute inset-0 bg-gray-900 flex items-center justify-center z-20";
        }
    },

    loop: function() {
        if (!this.scanning) return;
        requestAnimationFrame(() => this.loop());

        if (this.cooldown) return;

        if (this.videoEl && this.videoEl.readyState === 4) { // HAVE_ENOUGH_DATA
             const w = 64;
             const h = 64;
             this.canvasEl.width = w;
             this.canvasEl.height = h;
             const ctx = this.canvasEl.getContext('2d', { willReadFrequently: true });
             ctx.drawImage(this.videoEl, 0, 0, w, h);

             const frame = ctx.getImageData(0, 0, w, h);
             const score = this.calculateDiff(frame.data, this.lastFrame);
             this.lastFrame = frame.data;

             if (this.debugEl) this.debugEl.innerText = "Diff: " + score.toFixed(1);

             if (score < this.threshold) {
                 this.stableCount++;
             } else {
                 this.stableCount = 0;
             }

             if (this.stableCount > this.stabilityFrames) {
                 this.capture();
             }
        }
    },

    calculateDiff: function(data1, data2) {
      if (!data1 || !data2) return 100;
      let diff = 0;
      let count = 0;
      for (let i = 0; i < data1.length; i += 16) {
          diff += Math.abs(data1[i] - data2[i]);
          diff += Math.abs(data1[i+1] - data2[i+1]);
          diff += Math.abs(data1[i+2] - data2[i+2]);
          count++;
      }
      return diff / count;
    },

    capture: function() {
        this.cooldown = true;
        this.stableCount = 0;
        this.updateOverlay('processing');

        // Full res
        this.canvasEl.width = this.videoEl.videoWidth;
        this.canvasEl.height = this.videoEl.videoHeight;
        const ctx = this.canvasEl.getContext('2d');
        ctx.drawImage(this.videoEl, 0, 0);

        const dataUrl = this.canvasEl.toDataURL('image/jpeg', 0.85);

        // Emit event
        if (this.containerEl) {
            this.containerEl.dispatchEvent(new CustomEvent('scan_data', { detail: dataUrl, bubbles: true }));
        }
    }
};
</script>
"""

class ScannerUI:
    def __init__(self, collection_provider: Callable[[], Optional[Collection]], on_collection_update: Callable[[], None]):
        self.collection_provider = collection_provider
        self.on_collection_update = on_collection_update

        self.mode = 'MANUAL' # or 'BULK'
        self.is_active = False

        self.default_condition = "Near Mint"
        self.default_language = "EN"

        self.single_card_view = SingleCardView()
        self.scan_status_label = None
        self.consecutive_failures = 0
        self.undo_stack = []

        # Ensure DB is loaded
        ui.timer(0.1, self.ensure_db_loaded, once=True)

    async def ensure_db_loaded(self):
        await ygo_service.load_card_database()

    def toggle_camera(self):
        if self.is_active:
            self.stop_camera()
        else:
            self.start_camera()

    def start_camera(self):
        self.consecutive_failures = 0
        ui.run_javascript("ScannerController.start()")
        self.is_active = True
        if self.scan_status_label:
            self.scan_status_label.text = "Camera Active - Steady camera to scan"
            self.scan_status_label.classes(remove='text-red-400', add='text-green-400')

    def stop_camera(self):
        ui.run_javascript("ScannerController.stop()")
        self.is_active = False
        if self.scan_status_label:
            self.scan_status_label.text = "Camera Stopped"
            self.scan_status_label.classes(remove='text-green-400', add='text-gray-400')

    def resume_scanning(self):
        ui.run_javascript("ScannerController.resume()")

    async def handle_scan_event(self, e: events.GenericEventArguments):
        # detail is directly in args for CustomEvent if configured?
        # nicegui GenericEventArguments: sender, client, args
        # args is dict for CustomEvent detail usually
        data_url = e.args.get('detail') if isinstance(e.args, dict) else e.args

        if not data_url or not isinstance(data_url, str) or not data_url.startswith('data:image/'):
            logger.error(f"Invalid image data received: {type(data_url)}")
            self.resume_scanning()
            return

        # Decode
        try:
            header, encoded = data_url.split(",", 1)
            image_bytes = base64.b64decode(encoded)
        except Exception as ex:
            logger.error(f"Base64 decode error: {ex}")
            self.resume_scanning()
            return

        logger.info(f"Image received, processing... ({len(image_bytes)} bytes)")

        # Identify
        result = await asyncio.to_thread(scanner_service.identify_card, image_bytes)

        # Log
        await scan_logger.log_scan(image_bytes, result, self.mode)

        if result['success']:
            self.consecutive_failures = 0
            card = result['card']
            logger.info(f"Identified: {card.name}")
            ui.notify(f"Scanned: {card.name}", type='positive')

            if self.mode == 'MANUAL':
                await self.open_manual_dialog(card, result)
            else:
                await self.process_bulk_scan(card, result)
        else:
            self.consecutive_failures += 1
            logger.warning(f"Scan failed: {result['error']}")

            if self.consecutive_failures >= 5:
                ui.notify(f"Could not identify card after {self.consecutive_failures} attempts. Stopping.", type='negative')
                self.stop_camera()
            else:
                ui.notify(f"Not Identified: {result['error']}", type='warning')
                # Resume immediately if failed
                self.resume_scanning()

    async def open_manual_dialog(self, card, result):
        current_col = self.collection_provider()

        with ui.dialog() as dialog, ui.card().classes('w-full max-w-lg bg-gray-900 border border-gray-700'):
            ui.label(f"Identified: {card.name}").classes('text-h6 text-white')
            ui.label(f"Match Confidence: {int(result['confidence']*100)}%").classes('text-caption text-gray-400')

            img_url = card.card_images[0].image_url_small
            ui.image(img_url).classes('w-32 h-48 mx-auto object-contain my-2')

            # Inputs
            with ui.grid(columns=2).classes('w-full gap-4'):
                 sets = [s.set_code for s in card.card_sets] if card.card_sets else ["N/A"]
                 set_select = ui.select(sets, value=sets[0], label="Set").props('dark')

                 rarity_select = ui.select(['Common', 'Rare', 'Super Rare', 'Ultra Rare', 'Secret Rare'],
                                           value='Common', label="Rarity").props('dark')

                 qty_input = ui.number('Quantity', value=1, min=1).props('dark')

                 lang_select = ui.select(['EN', 'DE', 'FR', 'IT', 'JP'], value=self.default_language, label="Lang").props('dark')

            async def save():
                current_col = self.collection_provider()
                if current_col:
                     CollectionEditor.apply_change(
                        current_col, card,
                        set_code=set_select.value,
                        rarity=rarity_select.value,
                        language=lang_select.value,
                        quantity=int(qty_input.value),
                        condition=self.default_condition,
                        first_edition=False,
                        image_id=None,
                        mode='ADD'
                     )
                     self.on_collection_update()
                     ui.notify("Card Added", type='positive')

                dialog.close()
                self.resume_scanning()

            async def cancel():
                dialog.close()
                self.resume_scanning()

            with ui.row().classes('w-full justify-end mt-4'):
                ui.button('Ignore', on_click=cancel).props('flat color=grey')
                ui.button('Add to Collection', on_click=save).classes('bg-accent text-dark')

        dialog.open()

    async def process_bulk_scan(self, card, result):
        current_col = self.collection_provider()
        if not current_col:
             ui.notify("No collection selected", type='negative')
             self.resume_scanning()
             return

        set_code = card.card_sets[0].set_code if card.card_sets else "N/A"
        rarity = card.card_sets[0].set_rarity if card.card_sets else "Common"

        CollectionEditor.apply_change(
            current_col, card,
            set_code=set_code,
            rarity=rarity,
            language=self.default_language,
            quantity=1,
            condition=self.default_condition,
            first_edition=False,
            image_id=None,
            mode='ADD'
        )
        self.on_collection_update()

        self.undo_stack.append({
            'card': card,
            'set_code': set_code,
            'rarity': rarity,
            'language': self.default_language,
            'quantity': 1,
            'condition': self.default_condition
        })

        if hasattr(self, 'bulk_status_container'):
             self.bulk_status_container.clear()
             with self.bulk_status_container:
                 with ui.row().classes('w-full items-center bg-gray-800 p-2 rounded border border-gray-700'):
                     ui.label(f"Added: {card.name}").classes('font-bold text-accent flex-grow')
                     ui.label(f"{set_code} | {rarity}").classes('text-xs text-gray-400 mr-2')

                     async def undo():
                         CollectionEditor.apply_change(
                             current_col, card,
                             set_code=set_code,
                             rarity=rarity,
                             language=self.default_language,
                             quantity=-1,
                             condition=self.default_condition,
                             first_edition=False,
                             image_id=None,
                             mode='ADD'
                         )
                         self.on_collection_update()
                         self.bulk_status_container.clear()
                         ui.notify(f"Removed {card.name}", type='info')

                     ui.button('Undo', on_click=undo).props('flat dense color=red')

        self.resume_scanning()


    def render(self):
        # Inject JS Controller
        ui.add_head_html(SCANNER_JS)

        with ui.column().classes('w-full h-full gap-4'):
            # Controls
            with ui.card().classes('w-full bg-dark border border-gray-700 p-4'):
                with ui.row().classes('w-full items-center justify-between'):
                    with ui.row().classes('items-center gap-4'):
                         ui.label('Scanner').classes('text-h6')
                         self.scan_status_label = ui.label('Camera Stopped').classes('text-gray-400 font-mono')

                    with ui.row().classes('items-center gap-2'):
                         ui.label('Mode:').classes('text-gray-400')
                         ui.toggle(['MANUAL', 'BULK'], value=self.mode, on_change=lambda e: setattr(self, 'mode', e.value)) \
                            .props('no-caps dense')

                    ui.button(icon='videocam', on_click=self.toggle_camera).classes('bg-accent text-dark')

            # Bulk Mode Options
            with ui.row().bind_visibility_from(self, 'mode', lambda m: m == 'BULK').classes('w-full items-center gap-4 bg-gray-800 p-2 rounded'):
                ui.label('Bulk Defaults:').classes('text-gray-400')
                ui.select(['Near Mint', 'Played', 'Damaged'], value=self.default_condition,
                          on_change=lambda e: setattr(self, 'default_condition', e.value), label='Condition').props('dense dark options-dense')
                ui.select(['EN', 'DE', 'FR', 'IT', 'JP'], value=self.default_language,
                          on_change=lambda e: setattr(self, 'default_language', e.value), label='Language').props('dense dark options-dense')

            # Camera View (Raw HTML)
            # Use a container div that catches the event
            with ui.card().classes('w-full aspect-video bg-black p-0 overflow-hidden relative border border-gray-600') as card_container:
                 # IDs for JS
                 vid = "scanner-video"
                 cid = "scanner-canvas"
                 oid = "scanner-overlay"
                 did = "scanner-debug"

                 # The card_container itself will listen for events?
                 # NiceGUI element IDs are not predictable unless we wrap in another element with specific props?
                 # `ui.element('div')` can have ID.

                 with ui.element('div').classes('w-full h-full relative').props('id="scanner-container"') as container:
                     container.on('scan_data', self.handle_scan_event)

                     ui.html(f"""
                        <video id="{vid}" playsinline muted class="w-full h-full object-cover"></video>
                        <canvas id="{cid}" class="hidden"></canvas>
                        <div id="{oid}" class="absolute inset-0 bg-gray-900 flex items-center justify-center z-20">
                            <span class="text-gray-400">Camera Off</span>
                        </div>
                        <div id="{did}" class="absolute bottom-2 left-2 text-white text-[10px] font-mono bg-black/50 px-1 rounded"></div>
                     """, sanitize=False)

            # Init JS controller with IDs
            ui.run_javascript(f"ScannerController.init('{vid}', '{cid}', 'scanner-container', '{oid}', '{did}')")

            # Bulk Mode Status Banner (Container)
            self.bulk_status_container = ui.column().classes('w-full')

            # Instructions
            with ui.expansion('Instructions').classes('w-full bg-gray-900'):
                ui.markdown("""
                **Scanning Tips:**
                - Place the card on a dark, plain background.
                - Ensure good lighting.
                - Hold the camera steady until the frame locks.
                """)
