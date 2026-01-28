# AI Card Scanner

OpenYuGi features a powerful local AI scanner that runs entirely on your machine. It uses advanced computer vision (YOLO, OCR, Feature Matching) to identify cards from your webcam feed in real-time.

## 1. Overview & Requirements

The scanner is designed to be **Privacy-First** and **Local-Only**. No images are sent to the cloud.

### Requirements
- **Webcam**: A standard 1080p webcam is recommended.
- **Lighting**: Good, even lighting is critical. Avoid glare on the card surface (sleeves can cause reflections).
- **Hardware**: Runs on CPU, but an NVIDIA GPU is recommended for faster inference (via CUDA).

## 2. Interface Overview

The Scanner is divided into two tabs:

### A. Live Scan (Main)
This is your primary workspace.
- **Left Panel**: Camera feed, Camera selection, and Capture controls.
- **Right Panel**: "Recent Scans" gallery. This acts as a staging area where you review cards before adding them to your collection.
- **Header**: Target Collection selection and Default Attribute settings.

### B. Debug Lab (Advanced)
A playground for configuring the AI pipeline and troubleshooting.
- **Pipeline Visualization**: See exactly what the AI sees (Warped view, Regions of Interest).
- **Configuration**: Fine-tune OCR engines, preprocessing modes, and thresholds.
- **Manual Upload**: Test the scanner with static image files.

## 3. Scanning Workflow

### Step 1: Setup
1.  **Select Camera**: Choose your webcam from the dropdown in the left panel.
2.  **Target Collection**: In the top header, select the collection where you want the cards to eventually go (e.g., "My Binder").
3.  **Set Defaults**: Configure the attributes for upcoming scans:
    -   **Lang**: Default Language (e.g., EN, DE).
    -   **Cond**: Default Condition (e.g., Near Mint).
    -   **Storage**: (Optional) Default Storage container (e.g., "Binder 1").

### Step 2: Capture
1.  Place a card in the center of the camera view.
    -   *Tip: Ensure the card is roughly aligned, though the AI handles rotation well.*
2.  **Trigger Scan**:
    -   Click the **CAPTURE & SCAN** button.
    -   OR Press the **Spacebar** (ensure focus is not on a text input).
3.  The system will freeze the frame, detect the card, read the text, and match the artwork.

### Step 3: Review & Resolve
*   **Success**: The card appears in the **Recent Scans** gallery on the right.
*   **Ambiguity**: If the scanner finds multiple matches (e.g., same card in multiple sets, or same set with different rarities), an **Ambiguity Dialog** will appear.
    -   Select the correct version from the list.
    -   If the correct version isn't listed (e.g. OCR error), you can cancel and retry.
*   **Failure**: A notification will appear if no card was found. Try adjusting lighting or background contrast.

### Step 4: Manage Recent Scans
The "Recent Scans" area is a powerful grid view similar to the Bulk Add page.
*   **Edit**: Click any card to open the **Single Card View**, where you can modify Edition, Condition, Language, or Storage.
*   **Remove**: Right-click a card to decrease its quantity by 1.
*   **Filter & Sort**: Use the header controls to search by name/set, sort by price/rarity, or filter by specific attributes.
*   **Batch Update**:
    1.  Check the boxes for properties you want to update (Lang, Cond, Storage) in the header.
    2.  Click **UPDATE** to apply the current Default values to *all visible cards* in the list.
*   **Undo**: Click the **Undo** button to revert the last action (scan or edit).

### Step 5: Commit
Once you are happy with the list:
1.  Click the **COMMIT** button in the top-right.
2.  All cards in "Recent Scans" will be moved to your **Target Collection**.
3.  The Recent Scans list is cleared, ready for the next batch.

## 4. Advanced Configuration (Debug Lab)

If you are having trouble scanning specific cards, the **Debug Lab** offers granular control.

### Preprocessing Modes
Determines how the card is isolated from the background.
*   **Classic**: Standard contour detection. Fast, but needs good contrast (dark background recommended).
*   **Classic (White BG)**: Optimized for scanning on white surfaces (paper, mats).
*   **YOLO / YOLOv26**: Uses a Neural Network to detect the card object. Slower but extremely robust against cluttered backgrounds.

### OCR Engines
*   **EasyOCR**: Faster, general-purpose text reading.
*   **DocTR**: Slower but significantly more accurate, especially for small text or non-English languages.

### Art Style Match
*   **YOLO Classification**: Uses a secondary AI model to verify the card identity by its artwork.
*   **Index Images**: If enabled, you may need to click "Index Images" to build the database from your local card images (`data/images`).

### Settings
*   **Rotation**: Rotate the camera feed (0°, 90°, 180°, 270°) if your webcam is mounted upside down or sideways.
*   **Ambiguity Threshold**: Adjust how strict the matcher is. Lower values result in fewer ambiguity dialogs but higher risk of incorrect rarity assignment.
*   **Overlay Duration**: How long the "freeze frame" lasts after scanning.
*   **Save Scans**: Options to save the raw or warped images to `data/scans/` for debugging purposes.

## 5. Troubleshooting

*   **"No Card Found"**:
    *   Ensure the background contrasts with the card borders.
    *   Try switching **Preprocessing Mode** to **YOLO** in the Debug Lab.
*   **Wrong Set/Rarity**:
    *   Glare is the enemy. It creates white spots that blind the OCR. Use diffused lighting.
    *   Increase the **Ambiguity Threshold** to force the dialog to appear more often.
*   **Scanner is Slow**:
    *   Disable **DocTR** (use EasyOCR).
    *   Disable **Art Match**.
    *   Ensure you are using a GPU if possible.
