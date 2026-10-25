"""Train your own photo model and install it for the app (runs fully on your computer).

Dataset layout (either works):
  dataset/<class>/*.jpg                      one folder per class
  dataset/train/<class>/*.jpg, dataset/valid/<class>/*.jpg   (Roboflow "Folder Structure" export)
Folders named "defect-<name>" (e.g. defect-cracked_wall) are reported as defects; all others are photo classes.
Aim for 50+ photos per class; more photos and more variety give better results.

Setup (once):  pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
               pip install onnx onnxscript
Train:         python scripts/train_photo_model.py path/to/dataset [--epochs 15]

Writes data/models/photo_model.onnx and data/models/photo_model.json. The app uses them automatically from then on
(restart the server / redeploy). Delete both files to go back to the general model.
"""
import argparse
import json
import random
from pathlib import Path

import torch
from torch import nn
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, models, transforms

MEAN, STD = [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]
OUT = Path(__file__).resolve().parent.parent / "data" / "models"


def load(root: Path, size: int):
    train_tf = transforms.Compose([transforms.RandomResizedCrop(size, scale=(0.6, 1.0)), transforms.RandomHorizontalFlip(),
                                   transforms.ColorJitter(0.3, 0.3, 0.2), transforms.ToTensor(), transforms.Normalize(MEAN, STD)])
    eval_tf = transforms.Compose([transforms.Resize(size), transforms.CenterCrop(size), transforms.ToTensor(), transforms.Normalize(MEAN, STD)])
    if (root / "train").is_dir():
        train = datasets.ImageFolder(root / "train", train_tf)
        val_dir = next((root / d for d in ("valid", "val", "test") if (root / d).is_dir()), None)
        val = datasets.ImageFolder(val_dir, eval_tf) if val_dir else None
        if val is not None and val.classes != train.classes:
            raise SystemExit(f"Class folders differ between train ({train.classes}) and {val_dir.name} ({val.classes}).")
        return train, val, train.classes
    full_train, full_eval = datasets.ImageFolder(root, train_tf), datasets.ImageFolder(root, eval_tf)
    idx = list(range(len(full_train)))
    random.Random(0).shuffle(idx)
    cut = max(1, int(0.2 * len(idx)))  # 20% held back to measure accuracy
    return Subset(full_train, idx[cut:]), Subset(full_eval, idx[:cut]), full_train.classes


def accuracy(model, loader) -> float:
    model.eval()
    right = total = 0
    with torch.no_grad():
        for x, y in loader:
            right += (model(x).argmax(1) == y).sum().item()
            total += len(y)
    return right / max(1, total)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--size", type=int, default=224)
    args = ap.parse_args()
    train, val, classes = load(args.dataset, args.size)
    if len(classes) < 2:
        raise SystemExit("Need at least two class folders.")
    print(f"{len(classes)} classes: {classes}\n{len(train)} training photos, {len(val) if val else 0} for testing")

    model = models.mobilenet_v3_large(weights=models.MobileNet_V3_Large_Weights.DEFAULT)  # starts from general knowledge
    model.classifier[3] = nn.Linear(model.classifier[3].in_features, len(classes))
    for p in model.features.parameters():
        p.requires_grad = False  # first train only the new head, then fine-tune everything
    opt = torch.optim.AdamW(filter(lambda p: p.requires_grad, model.parameters()), lr=1e-3)
    loss_fn = nn.CrossEntropyLoss()
    tl = DataLoader(train, batch_size=16, shuffle=True)
    vl = DataLoader(val, batch_size=32) if val else None
    best, best_state = -1.0, None
    for epoch in range(args.epochs):
        if epoch == max(1, args.epochs // 3):
            for p in model.features.parameters():
                p.requires_grad = True
            opt = torch.optim.AdamW(model.parameters(), lr=1e-4)
        model.train()
        for x, y in tl:
            opt.zero_grad()
            loss = loss_fn(model(x), y)
            loss.backward()
            opt.step()
        acc = accuracy(model, vl) if vl else 0.0
        print(f"epoch {epoch + 1}/{args.epochs}  loss {loss.item():.3f}  test accuracy {acc:.1%}")
        if acc >= best:
            best, best_state = acc, {k: v.clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    model.eval()

    OUT.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(model, torch.randn(1, 3, args.size, args.size), str(OUT / "photo_model.onnx"),
                      input_names=["pixel_values"], output_names=["logits"], dynamic_axes={"pixel_values": {0: "batch"}}, opset_version=17, dynamo=False)
    display = [c.replace("defect-", "").replace("_", " ").strip().capitalize() for c in classes]
    meta = {"name": "officer-trained model", "labels": display,
            "defect_labels": [d for c, d in zip(classes, display) if c.lower().startswith("defect-")],
            "input_size": args.size, "mean": MEAN, "std": STD, "test_accuracy": round(best, 4)}
    (OUT / "photo_model.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nSaved {OUT / 'photo_model.onnx'} (test accuracy {best:.1%}). Restart the app to use it.")


if __name__ == "__main__":
    main()
