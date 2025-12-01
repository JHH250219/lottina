import os
from transformers import AutoProcessor, AutoModelForCausalLM
from PIL import Image
from flask import Flask, request, jsonify
import torch

API_KEY = os.getenv("OCR_API_KEY", "dev-fallback-key")

app = Flask(__name__)

print("🔄 Lade DeepSeek OCR Modell...")

model_path = "model"
processor = AutoProcessor.from_pretrained(model_path)
model = AutoModelForCausalLM.from_pretrained(
    model_path,
    torch_dtype=torch.float16,
    device_map="cuda"
)


@app.before_request
def auth():
    if request.headers.get("X-OCR-Key") != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401


@app.route("/ocr", methods=["POST"])
def ocr():
    if "image" not in request.files:
        return jsonify({"error": "No image uploaded"}), 400

    img = Image.open(request.files["image"]).convert("RGB")
    inputs = processor(images=img, return_tensors="pt").to("cuda")

    with torch.no_grad():
        output = model.generate(**inputs, max_new_tokens=1024)

    text = processor.batch_decode(output, skip_special_tokens=True)[0]
    return jsonify({"text": text})


if __name__ == "__main__":
    print("🚀 DeepSeek OCR Server läuft auf Port 8000...")
    app.run(host="0.0.0.0", port=8000)
