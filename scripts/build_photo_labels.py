"""Build data/models/clip_labels.json: text embeddings for the photo classes and defects.

Run once (needs the CLIP text model and tokenizer, which are not shipped with the app):
    python scripts/build_photo_labels.py <clip text_model.onnx> <tokenizer.json>
The app only needs the image model (data/models/clip_vision_q.onnx) and this JSON file at run time.
"""
import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

CLASSES = {
    "Classroom": ["a classroom with desks and a blackboard", "students sitting in a classroom"],
    "Computer lab": ["a computer laboratory with many computers on desks"],
    "Science laboratory": ["a science laboratory with lab equipment and benches"],
    "Workshop": ["an engineering workshop with machines and tools"],
    "Library": ["a library with bookshelves full of books"],
    "Seminar hall / auditorium": ["an auditorium or seminar hall with rows of seats and a stage"],
    "Corridor": ["an empty corridor inside a college building"],
    "Staircase": ["a staircase inside a building"],
    "Toilet / washroom": ["a toilet or washroom with a toilet seat, urinals or wash basins"],
    "Drinking water facility": ["a drinking water cooler or water purifier on a wall"],
    "Canteen / cafeteria": ["a canteen or cafeteria with tables"],
    "Playground / sports": ["a playground or sports ground"],
    "Building exterior": ["the outside of a college building", "the entrance of a college campus"],
    "Parking area": ["a parking area with vehicles"],
    "Garden / lawn": ["a garden or green lawn with trees"],
    "Fire extinguisher": ["a red fire extinguisher mounted on a wall"],
    "Fire hose reel": ["a round red fire hose reel coiled on a drum mounted on a wall", "a red fire hose drum"],
    "Fire sand buckets": ["red fire safety sand buckets hanging on a metal stand", "red buckets marked FIRE"],
    "Dustbins / waste bins": ["dustbins or waste bins", "colour-coded waste segregation bins"],
    "Wheelchair ramp": ["a wheelchair ramp with a handrail"],
    "Lift / elevator": ["an elevator lift door"],
    "CCTV camera": ["a small white CCTV security camera fixed to a ceiling"],
    "First aid box": ["a first aid box on a wall"],
    "Hostel room": ["a hostel room with beds"],
    "Notice board / signboard": ["a notice board or signboard with text"],
    "Meeting / event": ["a group of people at a meeting or event", "students at a college event"],
    "Medical room": ["a medical room with a bed for patients"],
    "Solar panels": ["solar panels on a roof"],
    "Website / screenshot": ["a screenshot of a website on a computer screen"],
    "Logo / printed document": ["a logo", "a printed document or certificate"],
}
DEFECTS = {
    "Cracked wall": ["a wall with large cracks"],
    "Water leakage / damp": ["a wall or ceiling with water leakage and damp stains"],
    "Peeling paint": ["a wall with peeling paint"],
    "Broken window or door": ["a broken window or a broken door"],
    "Broken furniture": ["broken chairs or damaged furniture"],
    "Exposed electrical wiring": ["exposed loose electrical wires hanging"],
    "Garbage / litter": ["garbage and litter scattered on the floor"],
    "Overflowing dustbin": ["an overflowing dustbin full of trash"],
    "Dirty toilet": ["a dirty unhygienic toilet"],
    "Rust / corrosion": ["rusted corroded metal"],
}
GOOD = {
    "clean": ["a clean, well-maintained place in good condition"],
    "tidy": ["a tidy room in good repair"],
    "equipment": ["equipment in good working condition"],
    "outdoor": ["a clean campus outdoors"],
}
TEMPLATES = ["a photo of {}.", "{}"]


def main(text_model: str, tokenizer_json: str) -> None:
    tok = Tokenizer.from_file(tokenizer_json)
    sess = ort.InferenceSession(text_model, providers=["CPUExecutionProvider"])

    def embed(prompts: list[str]) -> list[float]:
        vecs = []
        for p in prompts:
            for t in TEMPLATES:
                ids = tok.encode(t.format(p)).ids[:77]
                v = sess.run(None, {"input_ids": np.array([ids], dtype=np.int64)})[0][0]
                vecs.append(v / np.linalg.norm(v))
        m = np.mean(vecs, axis=0)
        return [round(float(x), 6) for x in m / np.linalg.norm(m)]

    out = {
        "model": "clip-vit-base-patch32 (quantized)",
        "classes": [{"name": k, "embedding": embed(v)} for k, v in CLASSES.items()],
        "defects": [{"name": k, "embedding": embed(v)} for k, v in DEFECTS.items()],
        "good": [embed(v) for v in GOOD.values()],
    }
    path = Path(__file__).resolve().parent.parent / "data" / "models" / "clip_labels.json"
    path.write_text(json.dumps(out), encoding="utf-8")
    print(f"wrote {path} ({len(out['classes'])} classes, {len(out['defects'])} defects)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
