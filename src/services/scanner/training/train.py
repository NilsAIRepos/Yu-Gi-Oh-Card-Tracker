import os
import argparse
import logging
import sys

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def train_model(data_yaml: str,
                model_name: str = "yolo26n-obb.pt",
                epochs: int = 50,
                imgsz: int = 640,
                batch: int = 16,
                device: str = ""):
    """
    Trains a YOLO model on the provided dataset.

    Args:
        data_yaml: Path to the dataset.yaml file.
        model_name: Model to start from (e.g. yolo26n-obb.pt).
                    Note: User requested YOLO 26 support.
                    If this model is not available in the installed ultralytics version,
                    it may attempt to download it or fail.
        epochs: Number of training epochs.
        imgsz: Image size.
        batch: Batch size.
        device: Device to train on (e.g., '0' for GPU, 'cpu' for CPU).
    """
    try:
        from ultralytics import YOLO
    except ImportError:
        logger.error("Ultralytics not installed. Please install it with `pip install ultralytics`.")
        sys.exit(1)

    logger.info(f"Starting training with model: {model_name}")
    logger.info(f"Dataset: {data_yaml}")

    # Initialize Model
    # Note: If yolo26 is not supported by the underlying library, this might raise an error.
    # We assume the user has a compatible version or the file exists.
    try:
        model = YOLO(model_name)
    except Exception as e:
        logger.error(f"Failed to load model {model_name}: {e}")
        logger.info("Falling back to yolov8n-obb.pt as a safe default base.")
        model = YOLO("yolov8n-obb.pt")

    # Train
    try:
        results = model.train(
            data=data_yaml,
            epochs=epochs,
            imgsz=imgsz,
            batch=batch,
            device=device,
            project="data/runs",
            name="yugioh_training",
            exist_ok=True # Overwrite existing run
        )

        logger.info("Training complete.")

        # Save the best model to data/models
        models_dir = "data/models"
        os.makedirs(models_dir, exist_ok=True)

        # The training results object contains the path to the best model
        # Usually runs/{project}/{name}/weights/best.pt
        best_model_path = os.path.join("data/runs", "yugioh_training", "weights", "best.pt")

        if os.path.exists(best_model_path):
            target_path = os.path.join(models_dir, "custom_card_model.pt")
            import shutil
            shutil.copy(best_model_path, target_path)
            logger.info(f"Best model saved to {target_path}")
        else:
            logger.warning(f"Could not find best model at {best_model_path}")

    except Exception as e:
        logger.error(f"Training failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train YOLO model for Card Detection")
    parser.add_argument("--data", default="data/training_data/dataset.yaml", help="Path to dataset.yaml")
    parser.add_argument("--model", default="yolo26n-obb.pt", help="Base model (e.g., yolo26n-obb.pt). Note: User specified YOLO 26.")
    parser.add_argument("--epochs", type=int, default=50, help="Number of epochs")
    parser.add_argument("--imgsz", type=int, default=640, help="Image size")
    parser.add_argument("--batch", type=int, default=16, help="Batch size")
    parser.add_argument("--device", default="", help="Device (cpu, 0, etc)")

    args = parser.parse_args()

    # Check if data exists
    if not os.path.exists(args.data):
        logger.error(f"Dataset config not found at {args.data}. Please run synthetic_data.py first.")
        sys.exit(1)

    train_model(args.data, args.model, args.epochs, args.imgsz, args.batch, args.device)
