import os
import cv2
import numpy as np
import random
import glob
import yaml
from typing import List, Tuple
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SyntheticDataGenerator:
    def __init__(self,
                 source_dir: str,
                 output_dir: str,
                 bg_size: Tuple[int, int] = (640, 640),
                 samples_per_card: int = 10):
        self.source_dir = source_dir
        self.output_dir = output_dir
        self.bg_size = bg_size
        self.samples_per_card = samples_per_card

        self.images_train_dir = os.path.join(output_dir, 'images', 'train')
        self.images_val_dir = os.path.join(output_dir, 'images', 'val')
        self.labels_train_dir = os.path.join(output_dir, 'labels', 'train')
        self.labels_val_dir = os.path.join(output_dir, 'labels', 'val')

        self._setup_directories()

    def _setup_directories(self):
        for d in [self.images_train_dir, self.images_val_dir,
                  self.labels_train_dir, self.labels_val_dir]:
            os.makedirs(d, exist_ok=True)

    def generate(self):
        """Main generation loop."""
        source_files = glob.glob(os.path.join(self.source_dir, "*.[jJ][pP][gG]")) + \
                       glob.glob(os.path.join(self.source_dir, "*.[pP][nN][gG]"))

        if not source_files:
            logger.warning(f"No images found in {self.source_dir}")
            return

        logger.info(f"Found {len(source_files)} source images. Generating dataset...")

        total_generated = 0
        for src_path in source_files:
            try:
                card_img = cv2.imread(src_path)
                if card_img is None:
                    continue

                # Generate samples
                for i in range(self.samples_per_card):
                    # 80% train, 20% val
                    is_train = random.random() < 0.8

                    if is_train:
                        img_dir, lbl_dir = self.images_train_dir, self.labels_train_dir
                    else:
                        img_dir, lbl_dir = self.images_val_dir, self.labels_val_dir

                    filename = f"{os.path.splitext(os.path.basename(src_path))[0]}_{i}"

                    self._create_sample(card_img, filename, img_dir, lbl_dir)
                    total_generated += 1

            except Exception as e:
                logger.error(f"Error processing {src_path}: {e}")

        self._create_yaml()
        logger.info(f"Generation complete. Created {total_generated} images.")

    def _create_sample(self, card_img, filename, img_dir, lbl_dir):
        # 1. Create White Background (with slight noise)
        bg = np.ones((self.bg_size[1], self.bg_size[0], 3), dtype=np.uint8) * 255

        # Add slight noise to background (simulating paper/lighting)
        noise = np.random.normal(0, 5, bg.shape).astype(np.uint8)
        bg = cv2.add(bg, noise)

        # 2. Resize Card (Random scale 0.3 to 0.8 of bg)
        h_bg, w_bg = self.bg_size
        scale = random.uniform(0.3, 0.8)

        h_c, w_c = card_img.shape[:2]
        aspect = w_c / h_c

        # Target height
        h_target = int(h_bg * scale)
        w_target = int(h_target * aspect)

        card_resized = cv2.resize(card_img, (w_target, h_target))

        # 3. Create Padding/Canvas for Rotation
        # We place the resized card in the center of a larger canvas, rotate it, then crop or paste
        # Simpler: Use Perspective Transform directly to place it on the background

        # Define source points (corners of the resized card)
        src_pts = np.float32([
            [0, 0],
            [w_target, 0],
            [w_target, h_target],
            [0, h_target]
        ])

        # Define destination center
        center_x = random.randint(int(w_bg * 0.2), int(w_bg * 0.8))
        center_y = random.randint(int(h_bg * 0.2), int(h_bg * 0.8))

        # Random Rotation
        angle = random.uniform(0, 360)
        theta = np.radians(angle)
        c, s = np.cos(theta), np.sin(theta)
        R = np.array(((c, -s), (s, c)))

        # Random Perspective/Tilt (small perturbation)

        # Center the src points around (0,0) before rotating
        src_centered = src_pts - np.array([w_target/2, h_target/2])

        # Rotate
        rotated_pts = np.dot(src_centered, R.T)

        # Add perspective noise
        perspective_noise = np.random.uniform(-10, 10, rotated_pts.shape)
        dst_pts = rotated_pts + perspective_noise

        # Shift to destination center
        dst_pts = dst_pts + np.array([center_x, center_y])

        # Calculate Homography
        M = cv2.getPerspectiveTransform(src_pts, dst_pts.astype(np.float32))

        # Warp
        warped_card = cv2.warpPerspective(card_resized, M, (w_bg, h_bg), borderValue=(255, 255, 255))

        # Composite: We need a mask to paste only the card part
        # Create mask from source
        mask_src = np.ones((h_target, w_target), dtype=np.uint8) * 255
        mask_warped = cv2.warpPerspective(mask_src, M, (w_bg, h_bg))

        # Invert mask for background
        mask_inv = cv2.bitwise_not(mask_warped)

        bg_bg = cv2.bitwise_and(bg, bg, mask=mask_inv)
        card_fg = cv2.bitwise_and(warped_card, warped_card, mask=mask_warped)

        final_img = cv2.add(bg_bg, card_fg)

        # 4. Save Image
        img_path = os.path.join(img_dir, filename + ".jpg")
        cv2.imwrite(img_path, final_img)

        # 5. Save Label (OBB Format)
        # Class x1 y1 x2 y2 x3 y3 x4 y4 (Normalized)
        # Ensure points are within [0,1]
        dst_pts_norm = dst_pts.copy()
        dst_pts_norm[:, 0] /= w_bg
        dst_pts_norm[:, 1] /= h_bg

        # Clip to [0, 1] - technically OBB can go slightly out, but for training good to keep in
        dst_pts_norm = np.clip(dst_pts_norm, 0.0, 1.0)

        # Flatten
        flat_pts = dst_pts_norm.flatten()
        label_str = f"0 {' '.join(map(str, flat_pts))}"

        lbl_path = os.path.join(lbl_dir, filename + ".txt")
        with open(lbl_path, "w") as f:
            f.write(label_str + "\n")

    def _create_yaml(self):
        """Creates the dataset.yaml file for YOLO."""
        yaml_content = {
            'path': os.path.abspath(self.output_dir),
            'train': 'images/train',
            'val': 'images/val',
            'names': {
                0: 'card'
            }
        }

        yaml_path = os.path.join(self.output_dir, 'dataset.yaml')
        with open(yaml_path, 'w') as f:
            yaml.dump(yaml_content, f, default_flow_style=False)
        logger.info(f"Created dataset config at {yaml_path}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate synthetic training data for card detection")
    parser.add_argument("--source", default="data/images", help="Source directory of card images")
    parser.add_argument("--output", default="data/training_data", help="Output directory for dataset")
    parser.add_argument("--count", type=int, default=10, help="Samples per source image")

    args = parser.parse_args()

    gen = SyntheticDataGenerator(args.source, args.output, samples_per_card=args.count)
    gen.generate()
