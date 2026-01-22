from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union

class OCRResult(BaseModel):
    engine: str
    scope: str = "full" # full or crop
    raw_text: str
    set_id: Optional[str] = None
    set_id_conf: float = 0.0
    language: str = "EN"

class ScanStep(BaseModel):
    name: str
    status: str # SUCCESS, FAIL
    details: Optional[str] = None

class ScanRequest(BaseModel):
    id: str
    timestamp: float
    filepath: str
    options: Dict[str, Any]
    type: str = "Manual Scan"
    filename: str

class ScanResult(BaseModel):
    """Final result for consumption by the UI/DB"""
    name: str = "Unknown Card"
    card_id: Optional[int] = None
    set_code: Optional[str] = None
    rarity: str = "Unknown"
    visual_rarity: str = "Common"
    language: str = "EN"
    first_edition: bool = False
    ocr_conf: float = 0.0
    image_path: Optional[str] = None
    match_score: int = 0
    # For UI display
    raw_ocr: Optional[List[OCRResult]] = None

class ScanDebugReport(BaseModel):
    """Comprehensive state for the Debug Lab"""
    logs: List[str] = []
    queue_len: int = 0
    paused: bool = True
    current_step: str = "Idle"

    captured_image_url: Optional[str] = None
    warped_image_url: Optional[str] = None
    roi_viz_url: Optional[str] = None

    # Intermediate OCR results
    t1_full: Optional[OCRResult] = None # EasyOCR
    t1_crop: Optional[OCRResult] = None
    t2_full: Optional[OCRResult] = None # PaddleOCR
    t2_crop: Optional[OCRResult] = None
    t3_full: Optional[OCRResult] = None # Keras-OCR
    t3_crop: Optional[OCRResult] = None
    t4_full: Optional[OCRResult] = None # MMOCR
    t4_crop: Optional[OCRResult] = None
    t5_full: Optional[OCRResult] = None # DocTR
    t5_crop: Optional[OCRResult] = None
    t6_full: Optional[OCRResult] = None # Tesseract
    t6_crop: Optional[OCRResult] = None

    # Metadata
    preprocessing: str = "classic"
    active_tracks: List[str] = []

    visual_rarity: str = "Unknown"
    first_edition: bool = False
    steps: List[ScanStep] = []

class ScanEvent(BaseModel):
    type: str # 'status_update', 'scan_queued', 'scan_finished', 'error'
    data: Dict[str, Any] # Flexible payload
    snapshot: Optional[ScanDebugReport] = None # Include full state snapshot for UI consistency
