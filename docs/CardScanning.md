# AI Card Scanner Documentation

## 1. Introduction

The OpenYuGi AI Scanner is a local-first computer vision system designed to identify Yu-Gi-Oh! cards via a webcam. Unlike cloud-based scanners, it runs entirely on your hardware, ensuring privacy and zero latency penalties from network uploads.

It utilizes a multi-stage pipeline involving:
1.  **Object Detection**: Finding the card in the video frame (Contour or YOLO).
2.  **Optical Character Recognition (OCR)**: Reading text (Set Code, Name, Stats) using EasyOCR or DocTR.
3.  **Visual Analysis**: Identifying Rarity (foil vs common) and 1st Edition status.
4.  **Feature Matching**: Verifying artwork against a local database using YOLO classification or ORB features.
5.  **Heuristic Matching**: A weighted scoring algorithm to determine the best match from the database.

---

## 2. Hardware & Setup

### Requirements
*   **Webcam**: 1080p resolution recommended. The scanner relies on reading small text (Set IDs), so 720p or lower may struggle.
*   **Lighting**: Bright, even, diffused lighting is critical.
    *   *Avoid*: Direct overhead lights that create glare (white spots) on glossy cards/sleeves. Glare blinds the OCR.
    *   *Best*: Angled desk lamp or natural daylight.
*   **Compute**:
    *   **CPU**: Works on modern CPUs (Intel i5/Ryzen 5 or newer).
    *   **GPU**: Highly recommended for real-time performance. Supports NVIDIA (CUDA) and Apple Silicon (MPS) if PyTorch is configured correctly.

### Initial Configuration
1.  Navigate to the **Scan Cards** tab.
2.  **Camera Selection**: In the left panel, select your device from the dropdown.
3.  **Start/Stop**: Click **Start** to initialize the feed.

---

## 3. Workflow Guide

### A. The "Live Scan" Interface

The interface is split into two zones:
*   **Left (Capture)**: The active camera feed and capture controls.
*   **Right (Staging Gallery)**: The "Recent Scans" list. This is a temporary holding area (persisted to `data/scans/scans_temp.json`) where you review cards before committing them to your main collection.

### B. Scanning Process

#### 1. Preparation
*   **Target Collection**: Select the destination collection in the top header (e.g., "Main Binder").
*   **Set Defaults**: Configure the attributes that will apply to *all new scans*:
    *   **Lang**: Default language (e.g., EN, DE). The scanner will try to detect this, but this serves as a fallback.
    *   **Cond**: Default condition (e.g., Near Mint).
    *   **Storage**: (Optional) Assign a default container (e.g., "Binder 1").

#### 2. Capture
Place the card in the center of the frame.
*   **Trigger**: Press **SPACEBAR** or click **CAPTURE & SCAN**.
*   **Freeze Frame**: The video will freeze for a configurable duration (default 1000ms) to confirm the capture.
*   **Feedback**:
    *   *Success*: A green notification appears, and the card is added to the right-hand gallery.
    *   *Ambiguous*: An orange dialog appears asking you to clarify (see below).
    *   *Failure*: A red notification indicates no match or detection failure.

#### 3. Resolving Ambiguity
The system triggers an **Ambiguity Dialog** if:
*   **DB Ambiguity**: The detected Set Code (e.g., `LOB-EN001`) exists in the database with multiple rarities (e.g., Ultra Rare vs. Secret Rare) or different artworks.
*   **Match Ambiguity**: The top two candidates have a Score difference smaller than the **Ambiguity Threshold**.

**Action**: Click the correct card from the list. If the correct card is missing (e.g., OCR misread `LOB` as `L0B`), you can dismiss and rescan.

#### 4. Managing "Recent Scans"
This gallery supports bulk operations similar to the main storage views.
*   **Right-Click**: Decrement quantity by 1 (removes card if qty becomes 0).
*   **Tooltip**: Hover over any card to fetch and display the high-resolution artwork from the local cache or API.
*   **Edit**: Click a card to open the **Single Card View** editor.
*   **Undo**: Reverts the last action.
    *   *Logic*: It compares the timestamp of the last "Recent Scan" change vs. the last "Target Collection" change. It effectively undoes whichever happened last.
*   **Batch Update**:
    1.  In the gallery header, check the properties to update (Lang, Cond, Storage).
    2.  Click **UPDATE**.
    3.  The system applies the *current defaults* from the top header to *all filtered cards* in the list.

#### 5. Commit
Click **COMMIT** to move all cards from "Recent Scans" to your **Target Collection**. This action clears the staging area.

---

## 4. Technical Deep Dive: The Matching Algorithm

The scanner uses a weighted scoring system to rank database candidates against the OCR results. A score > 30 is required for a match.

| Factor | Points | Description |
| :--- | :--- | :--- |
| **Exact Set Code** | **+80** | OCR matches DB exactly (e.g., `MRD-EN001`). |
| **Normalized Code** | **+75** | Region-agnostic match (e.g., OCR sees `MRD-DE001`, DB has `MRD-EN001`). |
| **Name Match** | **+50** | Exact name match (normalized). |
| **Partial Name** | **+25** | Partial string match on name. |
| **Passcode** | **+45** | 8-digit code at bottom-left matches exactly. |
| **Art Match** | **+40** | Artwork matches a specific variant image ID. |
| **Card Type** | **+10** | "Spell"/"Trap" keyword matches card type. |
| **Stats** | **+15** (x2) | ATK and/or DEF values match. |

**Virtual Candidates**: If the scanner finds a "Normalized Match" (e.g., German card scanned, only English in DB), it injects a "Virtual Candidate" with the scanned Set Code (`MRD-DE...`) and gives it a score boost (+12) to ensure it appears as the top result, allowing you to add the localized variant to your DB automatically.

---

## 5. Advanced Configuration (Debug Lab)

The **Debug Lab** tab exposes the internal parameters of the pipeline.

### A. Preprocessing Modes
Controls how the system finds the card in the video frame.
1.  **Classic (Default)**: Uses OpenCV Contour detection. Fast. Requires a dark background.
2.  **Classic (White BG)**: Uses inverted thresholding. Optimized for white paper/mats.
3.  **YOLO / YOLOv26**: Uses a Neural Network (`yolov8n-obb` or `yolo26l-obb`) to detect the card.
    *   *Pros*: Extremely robust. Ignores background clutter. Handles partial occlusion.
    *   *Cons*: Slower (requires GPU for smooth performance).

### B. OCR Engines
1.  **EasyOCR**:
    *   *Speed*: Fast.
    *   *Accuracy*: Good for Set Codes, decent for names.
2.  **DocTR**:
    *   *Speed*: Slow (1-2s per scan).
    *   *Accuracy*: Excellent. Handles non-English characters and small text significantly better.

### C. Thresholds
*   **Ambiguity Threshold (Default: 10.0)**:
    *   The "Safety Zone" score difference.
    *   *Example*: Candidate A (Score 85), Candidate B (Score 80). Diff is 5. Since 5 < 10, the system flags this as ambiguous and asks the user.
    *   *Tuning*: Lower this value if you want fewer dialogs (but more risk of wrong auto-selection).
*   **Art Match Threshold (Default: 0.42)**:
    *   Minimum Cosine Similarity (0.0 - 1.0) required to consider an image a match.
    *   *Note*: Requires "Index Images" to be run once to build the `art_index_yolo.pkl` from your `data/images` folder.

### D. Visualizations
*   **Latest Capture**: The raw frame passed to the pipeline.
*   **Perspective Warp**: The rectified "top-down" view of the card used for OCR.
*   **Regions of Interest (ROI)**: Boxes showing where the scanner looked for Set ID, Name, and Art.

---

## 6. Troubleshooting

### "No Match Found"
*   **Cause**: OCR failed to read the Set Code AND Name.
*   **Fix**:
    *   Check lighting. Glare on the Set ID is the #1 cause of failure.
    *   Try **DocTR** engine in Debug Lab.
    *   Ensure the card is oriented correctly (text right-side up).

### "Scanner is Stuck / Frozen"
*   **Cause**: The backend worker thread might have crashed or paused.
*   **Fix**:
    *   Click **Stop** then **Start** in the Live Scan tab.
    *   Check the console logs for error tracebacks.

### "Wrong Rarity Detected"
*   **Cause**: Visual rarity detection (Common vs Foil) is based on pixel brightness variance and specific color thresholds (Gold/Silver).
*   **Fix**: This is heuristic-only. Always verify the rarity in the Ambiguity Dialog or Recent Scans list.

### "Art Match Not Working"
*   **Cause**: The index is empty or the image isn't in `data/images`.
*   **Fix**:
    1.  Go to **Debug Lab**.
    2.  Click **Index Images** (Purple button).
    3.  Wait for the logs to show "Art Index complete".
