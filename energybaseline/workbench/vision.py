"""Actual 2-D image learning and explicit printed-template OCR; no downloads or raw pixel/text export."""

from __future__ import annotations

import hashlib

import numpy as np
from scipy import ndimage

from .common import digest, fields, integer, number, real_array
from .deep import state_digest, torch_scope


def image_network(mode, channels, classes, width):
    import torch
    from torch import nn
    from torch.nn import functional as F

    def block(a, b):
        return nn.Sequential(nn.Conv2d(a, b, 3, padding=1), nn.ReLU(), nn.Conv2d(b, b, 3, padding=1), nn.ReLU())

    class Model(nn.Module):
        def __init__(self):
            super().__init__()
            self.first = block(channels, width)
            self.second = block(width, width * 2)
            if mode == "classification":
                self.head = nn.Linear(width * 2, classes)
            else:
                self.bottom = block(width * 2, width * 4)
                self.up_second = block(width * 6, width * 2)
                self.up_first = block(width * 3, width)
                self.head = nn.Conv2d(width, 1, 1)

        def forward(self, x):
            first = self.first(x)
            second = self.second(F.max_pool2d(first, 2))
            if mode == "classification":
                return self.head(F.adaptive_avg_pool2d(second, 1).flatten(1))
            bottom = self.bottom(F.max_pool2d(second, 2))
            decoded = self.up_second(
                torch.cat(
                    [F.interpolate(bottom, size=second.shape[-2:], mode="bilinear", align_corners=False), second], 1
                )
            )
            return self.head(
                self.up_first(
                    torch.cat(
                        [F.interpolate(decoded, size=first.shape[-2:], mode="bilinear", align_corners=False), first], 1
                    )
                )
            ).squeeze(1)

    return Model()


def _boxes(mask, minimum):
    labeled, count = ndimage.label(mask)
    boxes = []
    for position, slices in enumerate(ndimage.find_objects(labeled), 1):
        if slices is None or np.sum(labeled[slices] == position) < minimum:
            continue
        y, x = slices
        boxes.append([x.start, y.start, x.stop, y.stop])
    if len(boxes) > 128:
        raise ValueError("Component detection exceeds 128 objects per image.")
    return boxes


def _iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return intersection / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection)


def _load_files_unchecked(spec):
    from pathlib import Path
    from io import BytesIO
    from PIL import Image, ImageOps
    from ..io.paths import validate_path

    root = spec["image_root"]
    names = spec["image_files"]
    shape = spec.get("image_size", [16, 16])
    mode = spec.get("color_mode", "L")
    if (
        not isinstance(root, str)
        or not isinstance(names, list)
        or not 8 <= len(names) <= 256
        or any(not isinstance(n, str) for n in names)
    ):
        raise ValueError("Image files require an explicit root and 8..256 relative names.")
    if not isinstance(shape, list) or len(shape) != 2 or mode not in ("L", "RGB"):
        raise ValueError("Declare an image size and grayscale/RGB color mode.")
    height, width = [integer(v, "image side", 8, 32) for v in shape]
    root_path = validate_path(root, allowed_roots=[root], must_exist=True)
    if not root_path.is_dir():
        raise ValueError("Image root must be a directory.")
    images, hashes, total = [], [], 0
    for name in names:
        if Path(name).is_absolute():
            raise ValueError("Image names must be relative to the explicit root.")
        path = validate_path(root_path / name, allowed_roots=[root_path], must_exist=True)
        if (
            path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".bmp")
            or not path.is_file()
            or path.stat().st_size > 10_000_000
        ):
            raise ValueError("Only bounded PNG/JPEG/BMP image files are supported.")
        with path.open("rb") as stream:
            raw = stream.read(10_000_001)
        if len(raw) > 10_000_000:
            raise ValueError("Image file grew past its byte budget.")
        total += len(raw)
        if total > 50_000_000:
            raise ValueError("Image byte budget exceeded.")
        with Image.open(BytesIO(raw)) as image:
            if image.width * image.height > 4_000_000 or getattr(image, "n_frames", 1) != 1:
                raise ValueError("Image dimensions/animation exceed decoding limits.")
            image = ImageOps.exif_transpose(image).convert(mode).resize((width, height), Image.Resampling.BILINEAR)
            array = np.asarray(image, dtype=float) / 255
            images.append(array[None] if mode == "L" else array.transpose(2, 0, 1))
        hashes.append(hashlib.sha256(raw).hexdigest())
    return np.asarray(images), hashes


def _load_files(spec):
    from PIL import Image

    try:
        return _load_files_unchecked(spec)
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ValueError(
            "Image loading failed path, format, size or decoding validation; source details are not exported."
        ) from None


def image_learning(spec, mode):
    file_mode = "image_files" in spec
    fields(
        spec,
        ("image_files", "image_root", "labels", "train_images", "seed")
        if file_mode
        else ("images", "labels", "train_images", "seed"),
        ("epochs", "width", "learning_rate", "threshold", "minimum_component_area", "sample_groups")
        + (("image_size", "color_mode") if file_mode else ()),
    )
    images, file_hashes = _load_files(spec) if file_mode else (real_array(spec["images"], "images"), None)
    if images.ndim == 3:
        images = images[:, None]
    if (
        images.ndim != 4
        or not 8 <= images.shape[0] <= 256
        or images.shape[1] not in (1, 3)
        or any(not 8 <= v <= 32 for v in images.shape[2:])
        or ((images < 0) | (images > 1)).any()
    ):
        raise ValueError("Images require NCHW grayscale/RGB pixels in [0,1], 8..256 samples, 8..32 pixel sides.")
    split = integer(spec["train_images"], "train_images", 4, len(images) - 1)
    epochs = integer(spec.get("epochs", 10), "epochs", 1, 100)
    width = integer(spec.get("width", 8), "width", 4, 24)
    seed = integer(spec["seed"], "seed", 0, 2**32 - 1)
    lr = number(spec.get("learning_rate", 0.003), "learning_rate", 1e-5, 0.1)
    if images.size * epochs > 20_000_000:
        raise ValueError("Image training pixel-work budget exceeded.")
    image_hashes = [hashlib.sha256(image.tobytes()).hexdigest() for image in images]
    if set(image_hashes[:split]) & set(image_hashes[split:]):
        raise ValueError("Exact duplicate images cannot cross the heldout boundary.")
    if "sample_groups" in spec:
        groups = spec["sample_groups"]
        if (
            not isinstance(groups, list)
            or len(groups) != len(images)
            or any(type(g) is not int for g in groups)
            or set(groups[:split]) & set(groups[split:])
        ):
            raise ValueError("Sample groups must be explicit integer IDs disjoint across train/holdout.")
    labels = real_array(spec["labels"], "labels")
    if mode == "classification":
        if "threshold" in spec or "minimum_component_area" in spec:
            raise ValueError("Segmentation thresholds do not apply to classification.")
        if labels.shape != (len(images),) or not np.equal(labels, np.floor(labels)).all():
            raise ValueError("Image classes must be explicit integer positions.")
        classes = np.unique(labels[:split])
        count = len(classes)
        if not 2 <= count <= 10 or not np.array_equal(classes, np.arange(count)) or not np.isin(labels, classes).all():
            raise ValueError("Training must contain each contiguous class; holdout cannot introduce unknown classes.")
    else:
        count = 1
        if (
            labels.shape != (len(images), *images.shape[2:])
            or not np.isin(labels, [0, 1]).all()
            or len(np.unique(labels[:split])) != 2
        ):
            raise ValueError("Segmentation requires binary masks and both training pixel classes.")
        if mode != "detection" and "minimum_component_area" in spec:
            raise ValueError("Component areas apply only to detection.")
    import torch

    mean = images[:split].mean(axis=(0, 2, 3), keepdims=True)
    sd = images[:split].std(axis=(0, 2, 3), keepdims=True)
    sd = np.where(sd > 1e-8, sd, 1)
    normalized = ((images - mean) / sd).astype("float32")
    with torch_scope(seed):
        model = image_network(mode, images.shape[1], count, width)
        optimizer = torch.optim.Adam(model.parameters(), lr=lr)
        x = torch.from_numpy(normalized)
        y = torch.from_numpy(labels.astype("int64" if mode == "classification" else "float32"))
        criterion = torch.nn.CrossEntropyLoss() if mode == "classification" else torch.nn.BCEWithLogitsLoss()
        losses = []
        for _ in range(epochs):
            model.train()
            total = 0.0
            for batch in torch.randperm(split).split(32):
                optimizer.zero_grad()
                loss = criterion(model(x[batch]), y[batch])
                if not torch.isfinite(loss):
                    raise ValueError("Nonfinite image training objective.")
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
                optimizer.step()
                total += float(loss.detach()) * len(batch)
            losses.append(total / split)
        model.eval()
        with torch.no_grad():
            logits = model(x[split:]).numpy()
        weights = state_digest(model)
    if not np.isfinite(logits).all():
        raise ValueError("Nonfinite heldout image predictions.")
    output = {
        "task": mode,
        "architecture": "Conv2d classifier" if mode == "classification" else "Two-level U-Net binary foreground model",
        "backend": "torch",
        "train_images": split,
        "test_images": len(images) - split,
        "train_image_sha256": digest(image_hashes[:split]),
        "all_image_values_sha256": digest(image_hashes),
        "source_file_sha256": file_hashes,
        "trained_weight_sha256": weights,
        "parameters": sum(p.numel() for p in model.parameters()),
        "first_training_loss": losses[0],
        "last_training_loss": losses[-1],
        "preprocessing_scope": "Training images only; caller-ordered split, no holdout tuning",
        "raw_pixels_or_text_exported": False,
        "pretrained_weights_used": False,
        "baseline_changed": False,
    }
    if mode == "classification":
        predicted = logits.argmax(axis=1)
        truth = labels[split:].astype(int)
        confusion = np.zeros((count, count), dtype=int)
        np.add.at(confusion, (truth, predicted), 1)
        f1 = []
        for c in range(count):
            tp = confusion[c, c]
            denom = confusion[c].sum() + confusion[:, c].sum()
            f1.append(float(2 * tp / denom) if denom else None)
        output.update(
            accuracy=float(np.mean(predicted == truth)), confusion_matrix=confusion.tolist(), f1_by_class_position=f1
        )
        return output
    threshold = number(spec.get("threshold", 0.5), "threshold", 0.01, 0.99)
    predicted = logits >= np.log(threshold / (1 - threshold))
    truth = labels[split:].astype(bool)
    intersection = int(np.sum(predicted & truth))
    union = int(np.sum(predicted | truth))
    denom = int(predicted.sum() + truth.sum())
    output.update(
        foreground_iou=intersection / union if union else None,
        dice=2 * intersection / denom if denom else None,
        pixel_accuracy=float(np.mean(predicted == truth)),
        threshold=threshold,
    )
    if mode == "detection":
        minimum = integer(spec.get("minimum_component_area", 3), "minimum_component_area", 1, 100)
        all_boxes = []
        matched = 0
        predicted_count = 0
        true_count = 0
        for mask, target in zip(predicted, truth):
            boxes = _boxes(mask, minimum)
            targets = _boxes(target, minimum)
            used = set()
            for box in boxes:
                candidates = [(_iou(box, t), i) for i, t in enumerate(targets) if i not in used]
                if candidates:
                    score, index = max(candidates)
                    if score >= 0.5:
                        used.add(index)
                        matched += 1
            predicted_count += len(boxes)
            true_count += len(targets)
            all_boxes.append(boxes[:20])
        output.update(
            detector="Foreground-mask connected components, not a pretrained semantic object detector",
            matching_iou=0.5,
            precision=matched / predicted_count if predicted_count else None,
            recall=matched / true_count if true_count else None,
            predicted_object_count=predicted_count,
            target_object_count=true_count,
            first_twenty_boxes_per_image=all_boxes,
        )
    return output


def image_classification(spec):
    return image_learning(spec, "classification")


def image_segmentation(spec):
    return image_learning(spec, "segmentation")


def image_detection(spec):
    return image_learning(spec, "detection")


def template_ocr(spec):
    fields(spec, ("image", "templates", "threshold"), ("maximum_distance",))
    image = real_array(spec["image"], "image")
    templates = real_array(spec["templates"], "templates")
    if (
        image.ndim != 2
        or not 4 <= image.shape[0] <= 64
        or not 1 <= image.shape[1] <= 512
        or templates.ndim != 3
        or not 2 <= len(templates) <= 40
        or any(not 4 <= s <= 32 for s in templates.shape[1:])
        or ((image < 0) | (image > 1)).any()
        or ((templates < 0) | (templates > 1)).any()
    ):
        raise ValueError(
            "OCR requires bounded grayscale image/template arrays in [0,1], foreground dark on light background."
        )
    threshold = number(spec["threshold"], "threshold", 0.01, 0.99)
    maximum = number(spec.get("maximum_distance", 0.25), "maximum_distance", 0, 1)
    foreground = image < threshold
    bank = templates < threshold
    if any(not glyph.any() for glyph in bank):
        raise ValueError("Blank glyph templates are not valid.")

    def crop(glyph):
        yy, xx = np.nonzero(glyph)
        return glyph[yy.min() : yy.max() + 1, xx.min() : xx.max() + 1]

    from PIL import Image

    shape = templates.shape[1:]

    def normalized(glyph):
        return (
            np.asarray(
                Image.fromarray(crop(glyph).astype("uint8") * 255).resize(
                    (shape[1], shape[0]), resample=Image.Resampling.NEAREST
                )
            )
            > 0
        )

    bank = np.stack([normalized(g) for g in bank])
    active = foreground.any(axis=0)
    runs = np.flatnonzero(np.diff(np.r_[False, active, False]))
    if len(runs) > 200:
        raise ValueError("OCR is bounded to 100 separated glyphs.")
    positions = []
    distances = []
    for start, end in zip(runs[::2], runs[1::2]):
        glyph = normalized(foreground[:, start:end])
        distance = np.mean(glyph[None] != bank, axis=(1, 2))
        best = int(np.argmin(distance))
        tied = int(np.sum(np.isclose(distance, distance[best], atol=1e-12, rtol=0))) > 1
        positions.append(best if not tied and distance[best] <= maximum else None)
        distances.append(float(distance[best]))
    return {
        "method": "Separated printed-glyph segmentation and caller-supplied binary template matching",
        "template_positions": positions,
        "hamming_distances": distances,
        "unknown_glyphs": positions.count(None),
        "glyph_count": len(positions),
        "template_bank_sha256": digest(spec["templates"]),
        "recognized_plaintext_exported": False,
        "raw_pixels_exported": False,
        "scope": "Not handwriting, touching glyphs, multilingual neural OCR or a pretrained OCR engine",
        "baseline_changed": False,
    }
