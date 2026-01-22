import logging
from typing import List, Optional, Dict, Any
from src.core.text_utils import correct_set_code, fuzzy_ratio
from src.services.scanner.models import MatchResultInfo, MatchCandidate, OCRResult

logger = logging.getLogger(__name__)

class CardMatcher:
    def __init__(self, ygo_service):
        self.ygo_service = ygo_service
        self.min_confidence = 50.0

    def match_card(self, debug_report_dump: Dict[str, Any], language: str = "en") -> MatchResultInfo:
        """
        Analyzes the OCR report and finds the best matching cards.
        """
        # 1. Aggregate OCR Data
        # We look at all tracks (t1_full, t1_crop, etc.)

        detected_codes = []
        detected_names = []
        detected_langs = []

        # Priority: Crop > Full? Usually Crop is cleaner for text, Full for context.
        # But OCR engines vary.

        # Collect all raw texts and set ids
        for i in range(1, 5):
            for scope in ['crop', 'full']:
                key = f"t{i}_{scope}"
                res = debug_report_dump.get(key)
                if res:
                    # res is a dict if coming from dump
                    if isinstance(res, dict):
                        # Extract potential set code
                        if res.get('set_id'):
                            detected_codes.append({
                                'code': res['set_id'],
                                'conf': res.get('set_id_conf', 0),
                                'src': f"Track {i} ({scope})"
                            })

                        # Extract language
                        if res.get('language'):
                             detected_langs.append(res['language'])

                        # Extract potential name (from raw text)
                        # Name is usually at the top. OCR raw text is a blob.
                        # This is hard without specific ROI for name in OCRResult.
                        # But CardScanner.ocr_scan returns 'raw_text'.
                        # We might need to rely on the 'name' ROI OCR if we had one specific for name.
                        # Currently we don't have a specific 'name' OCR result in the report,
                        # just the full card OCR.
                        # However, 't*_crop' usually contains the text found in the crop.
                        # If the crop was the whole card, it has everything.
                        pass

        # 2. Process Set Codes
        # Deduplicate and Correct
        unique_codes = {} # code -> max_conf

        for item in detected_codes:
            raw = item['code']
            corrected, alts = correct_set_code(raw)

            # Add Corrected
            if corrected not in unique_codes:
                unique_codes[corrected] = item['conf']
            else:
                unique_codes[corrected] = max(unique_codes[corrected], item['conf'])

            # Add Alts (with slightly lower conf?)
            for alt in alts:
                if alt not in unique_codes:
                    unique_codes[alt] = item['conf'] * 0.9

        # Determine Language
        # Heuristic: Most frequent detected language, defaulting to input language
        target_lang = language
        if detected_langs:
             # Count frequencies
             from collections import Counter
             c = Counter(detected_langs)
             # If any non-EN is detected, prefer it? Or just most frequent?
             # Most frequent.
             most_common = c.most_common(1)[0][0]
             if most_common != "EN":
                 target_lang = most_common

        # 3. Search DB
        candidates_map = {} # card_variant_id -> MatchCandidate

        # Load DB (Synchronous access to cache)
        cards = self.ygo_service.get_cards_from_cache(target_lang)
        if not cards:
             # Fallback to English if target not loaded, or return empty
             cards = self.ygo_service.get_cards_from_cache("en")

        if not cards:
             return MatchResultInfo(candidates=[], best_match=None, ambiguous=False)

        # A. Set Code Matches
        for code, conf in unique_codes.items():
            # Find cards with this code
            # We search all cards
            # Optimization: Use a lookup map if available, but for now linear scan is ok (~12k cards)
            # Actually ygo_service should have a better lookup.
            # I'll use linear for robustness now.

            normalized_code = code.upper()

            for card in cards:
                if not card.card_sets: continue
                for s in card.card_sets:
                    if s.set_code == normalized_code:
                        # MATCH!

                        # Calculate Score
                        # Base score from OCR confidence
                        score = 60 + (conf * 0.4) # Max 100

                        cand_key = f"{card.id}_{s.variant_id}"
                        if cand_key not in candidates_map:
                            candidates_map[cand_key] = MatchCandidate(
                                card_id=card.id,
                                name=card.name,
                                set_code=s.set_code,
                                rarity=s.set_rarity,
                                image_path=None, # Filled later
                                confidence=score,
                                reason=f"Set Code Match ({code})"
                            )
                        else:
                            # Update if better
                            if score > candidates_map[cand_key].confidence:
                                candidates_map[cand_key].confidence = score
                                candidates_map[cand_key].reason = f"Set Code Match ({code})"

        # B. Name Matches (Fuzzy)
        # We need a name to search for.
        # Extract name from OCR Text?
        # Heuristic: The name is usually the first line of text or top-most text.
        # Let's aggregate raw texts and try to find a name.
        # This is tricky without line segmentation in OCRResult.
        # But `raw_text` in OCRResult is joined by " | ".
        # The first part is likely the name.

        potential_names = []
        for i in range(1, 5):
            res = debug_report_dump.get(f"t{i}_crop") or debug_report_dump.get(f"t{i}_full")
            if res and isinstance(res, dict) and res.get('raw_text'):
                parts = res['raw_text'].split('|')
                if parts:
                    name_guess = parts[0].strip()
                    if len(name_guess) > 3:
                        potential_names.append(name_guess)

        # Deduplicate names
        # ...

        if potential_names:
            # Pick longest/most frequent?
            # Let's take the first one that looks valid.
            target_name = potential_names[0] # Simplification

            # Fuzzy Search
            # We iterate all cards and fuzzy match name
            for card in cards:
                ratio = fuzzy_ratio(target_name, card.name)
                if ratio > 80:
                    # Potential Match

                    # If this card was already found via Set Code, BOOST it
                    # We need to find which variant.
                    # If we don't know the variant from set code, we pick the most common/first?
                    # Or add all variants?
                    # If we matched by name, we don't know the set code unless we cross ref.

                    # If we already have candidates for this card (via code), boost them
                    found_via_code = False
                    for key, cand in candidates_map.items():
                        if cand.card_id == card.id:
                            cand.confidence = min(100.0, cand.confidence + (ratio * 0.2))
                            cand.reason += f" + Name Match ({ratio}%)"
                            found_via_code = True

                    if not found_via_code:
                        # New candidate via name only
                        # We don't know the set code. We can pick the first one or generic.
                        # For now, pick the first variant.
                        if card.card_sets:
                            s = card.card_sets[0]
                            cand = MatchCandidate(
                                card_id=card.id,
                                name=card.name,
                                set_code=s.set_code, # Guess
                                rarity=s.set_rarity,
                                image_path=None,
                                confidence=ratio * 0.8, # Name match is less sure than Code
                                reason=f"Fuzzy Name Match ({ratio}%)"
                            )
                            candidates_map[f"{card.id}_name"] = cand

        # 4. Finalize
        final_candidates = list(candidates_map.values())

        # Resolve Images
        for cand in final_candidates:
            # Construct path manually to avoid async call
            # We assume images are stored as {id}.jpg in data/images
            cand.image_path = f"data/images/{cand.card_id}.jpg"

        # Sort
        final_candidates.sort(key=lambda x: x.confidence, reverse=True)

        # Determine Best
        best = final_candidates[0] if final_candidates else None

        return MatchResultInfo(
            candidates=final_candidates,
            best_match=best,
            ambiguous=(len(final_candidates) > 1 and final_candidates[1].confidence > (best.confidence * 0.9)) if best and len(final_candidates) > 1 else False
        )
